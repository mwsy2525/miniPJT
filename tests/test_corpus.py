"""RAG 코퍼스·검색 테스트: 원문 contexts/URL sources, 웹 폴백 on/off, 영역 지정 검색(오프라인, mock)."""
from __future__ import annotations

import pytest

from src import tools as T
from src.retrieval import service as retriever
from src.search import service as search
from src.shared import llm

_REAL_REWRITE = T.llm_rewrite_query  # 아래 autouse fixture가 바꾸기 전의 실제 함수


@pytest.fixture(autouse=True)
def _mocks(tmp_path, monkeypatch):
    monkeypatch.setenv("PROGRESS_DB_PATH", str(tmp_path / "p.db"))
    monkeypatch.setattr(search, "llm_rewrite_query", lambda q: "tool use")
    monkeypatch.setattr(search, "llm_answer", lambda *a, **k: T.SearchAnswer(answer="답변입니다."))
    monkeypatch.setattr(search, "llm_general_answer", lambda *a, **k: T.GeneralAnswer(unknown=True))


def _doc_for(domain):
    return next(d for d in retriever.load_manifest()["documents"] if domain in d["domains"])


def _hit(doc, score=0.9):
    c = next(c for c in retriever.load_chunks() if c["doc_path"] == doc["doc_path"])
    return {"text": c["text"], "doc_path": c["doc_path"], "chunk_id": c["chunk_id"], "url": c["url"], "score": score}


def test_manifest_files_exist_with_urls_and_dates():
    for d in retriever.load_manifest()["documents"]:
        assert (retriever.DOCS_DIR / d["doc_path"]).exists()
        assert d["url"].startswith("https://") and d["fetched_at"]
    assert retriever.load_chunks()
    assert all(c["chunk_id"].startswith(c["chunk_id"].split("#")[0] + "#") for c in retriever.load_chunks())


def test_contexts_are_snapshot_text_and_sources_have_urls(monkeypatch):
    doc = _doc_for(2)
    hit = _hit(doc)
    monkeypatch.setattr(retriever, "search", lambda *a, **k: [hit])
    ctx = T.ToolContext()
    out = T.run_search_docs(ctx, "tool use가 뭐야?")
    assert ctx.contexts == [hit["text"]]
    assert ctx.sources == [{"url": doc["url"], "doc_path": doc["doc_path"], "chunk_id": hit["chunk_id"]}]
    assert doc["url"] in out


def test_domain_scoped_question_passes_domain_and_filters(monkeypatch):
    d1_only = next(d for d in retriever.load_manifest()["documents"] if d["domains"] == [1])
    d2 = _doc_for(2)
    seen = {}

    def fake(q, k=4, domain=None, store=None):
        seen["domain"] = domain
        return [_hit(d2)]

    monkeypatch.setattr(retriever, "search", fake)
    ctx = T.ToolContext()
    T.run_search_docs(ctx, "Domain 2 에서 tool use 설명해줘")
    assert seen["domain"] == 2
    assert all(retriever.in_domain(s["doc_path"], 2) for s in ctx.sources)
    assert not retriever.in_domain(d1_only["doc_path"], 2)
    assert T.detect_domain("도메인 3 알려줘") == 3
    assert T.detect_domain("tool use가 뭐야?") is None


def test_retriever_search_filters_other_domains():
    class Doc:
        def __init__(self, d):
            h = _hit(d)
            self.metadata = {k: h[k] for k in ("chunk_id", "doc_path", "url")}
            self.page_content = h["text"]

    d1_only = next(d for d in retriever.load_manifest()["documents"] if d["domains"] == [1])

    class Store:
        def similarity_search_with_score(self, q, k, filter=None):
            assert filter == {"d2": True}
            return [(Doc(d1_only), 0.1), (Doc(_doc_for(2)), 0.2)]

    hits = retriever.search("x", k=4, domain=2, store=Store())
    assert hits and all(retriever.in_domain(h["doc_path"], 2) for h in hits)


def test_web_fallback_only_when_local_insufficient(monkeypatch):
    calls = []
    web = [{"text": "web passage", "url": "https://platform.claude.com/docs/en/x", "doc_path": None, "chunk_id": None}]
    monkeypatch.setattr(retriever, "web_search", lambda q, k=3: calls.append(q) or web)
    monkeypatch.setattr(retriever, "search", lambda *a, **k: [_hit(_doc_for(2))])
    T.run_search_docs(T.ToolContext(), "tool use?")
    assert calls == []  # 로컬 근거가 충분하면 웹 조회 0건
    monkeypatch.setattr(retriever, "search", lambda *a, **k: [])
    ctx = T.ToolContext()
    T.run_search_docs(ctx, "tool use?")
    assert calls == ["tool use"] and ctx.contexts == ["web passage"]
    assert ctx.sources == [{"url": web[0]["url"], "doc_path": None, "chunk_id": None}]


def test_web_fallback_off_makes_no_network_call(monkeypatch):
    import httpx
    monkeypatch.setenv("WEB_FALLBACK_ENABLED", "false")
    monkeypatch.setattr(httpx, "get", lambda *a, **k: pytest.fail("웹 조회 발생"))
    assert retriever.web_search("prompt caching") == []
    monkeypatch.setattr(retriever, "search", lambda *a, **k: [])
    ctx = T.ToolContext()
    out = T.run_search_docs(ctx, "tool use?")
    assert ctx.contexts == [] and "근거 부족" in out


def _fake_site(monkeypatch, pages, fail=None):
    """httpx.get을 가짜 공식 사이트로 바꾼다. 조회한 URL 목록을 돌려준다."""
    import httpx
    seen = []

    class R:
        def __init__(self, text): self.text = text
        def raise_for_status(self): pass

    def get(url, **kw):
        seen.append(url)
        if fail:
            raise fail
        return R(pages[url])

    monkeypatch.setattr(httpx, "get", get)
    monkeypatch.setitem(retriever._index_cache, "pages", [])
    return seen


def test_web_fallback_reads_official_index_without_api_key(monkeypatch):
    """키 없이 문서 목록에서 페이지를 골라 구절을 가져오고, 허용 도메인 밖 주소는 조회하지 않는다."""
    monkeypatch.setenv("WEB_FALLBACK_ENABLED", "true")
    monkeypatch.delenv("WEB_SEARCH_API_KEY", raising=False)
    monkeypatch.setenv("WEB_INDEX_URLS", "https://code.claude.com/docs/llms.txt,https://evil.example.com/llms.txt")
    index = ("- [Widgets guide](https://code.claude.com/docs/en/widgets.md): Configure widgets\n"
             "- [Evil widgets](https://evil.example.com/widgets.md): widgets\n"
             "- [Memory](https://code.claude.com/docs/en/memory.md): already in snapshot\n")
    page = "# Widgets\n\n## Configure widgets\nSet widget_mode to enable widgets.\n\n## Other\nNothing here.\n"
    seen = _fake_site(monkeypatch, {"https://code.claude.com/docs/llms.txt": index,
                                    "https://code.claude.com/docs/en/widgets.md": page})
    hits = retriever.web_search("configure widgets widget_mode")
    assert hits and hits[0]["url"] == "https://code.claude.com/docs/en/widgets"
    assert "widget_mode" in hits[0]["text"] and hits[0]["doc_path"] is None and hits[0]["chunk_id"] is None
    assert all("evil.example.com" not in u for u in seen)
    assert "https://code.claude.com/docs/en/memory.md" not in seen  # 스냅샷에 있는 문서는 다시 받지 않는다


def test_web_fallback_timeout_is_empty(monkeypatch):
    import httpx
    monkeypatch.setenv("WEB_FALLBACK_ENABLED", "true")
    _fake_site(monkeypatch, {}, fail=httpx.TimeoutException("t"))
    assert retriever.web_search("configure widgets") == []


def test_general_knowledge_fallback_when_no_evidence(monkeypatch):
    """문서 근거가 없으면 표시를 붙여 AI 일반 지식으로 답하고, contexts와 sources는 비운다."""
    monkeypatch.setattr(retriever, "search", lambda *a, **k: [])
    monkeypatch.setattr(search, "llm_general_answer", lambda *a, **k: T.GeneralAnswer(answer="Claude는 Anthropic의 모델입니다."))
    ctx = T.ToolContext()
    out = T.run_search_docs(ctx, "Claude가 뭐야?")
    assert out.startswith(T.GENERAL_LABEL) and "Anthropic의 모델" in out
    assert ctx.refusal_reason is None and ctx.contexts == [] and ctx.sources == []
    assert ctx.tool_calls[-1]["args"]["answer_basis"] == "ai_general_knowledge"


def test_general_fallback_unknown_stays_evidence_gap(monkeypatch):
    """AI도 모르는 질문은 계속 근거 부족으로 거절한다."""
    monkeypatch.setattr(retriever, "search", lambda *a, **k: [])
    ctx = T.ToolContext()
    T.run_search_docs(ctx, "quantum-entangle 플래그?")
    assert ctx.refusal_reason == "근거 부족"


def test_unknown_identifier_in_passages_is_evidence_gap(monkeypatch):
    """구절이 검색돼도 질문 속 식별자(quantum-entangle)가 구절에 없으면 LLM 답변 없이 근거 부족으로 간다."""
    hit = {"text": "CLI flags: --model, --print, --output-format.", "score": 0.9, "url": "https://code.claude.com/docs/en/cli-reference",
           "doc_path": "code/cc-cli-reference.md", "chunk_id": "cc-cli-reference#cli-flags"}
    monkeypatch.setattr(retriever, "search", lambda *a, **k: [hit])
    monkeypatch.setattr(search, "llm_answer", lambda *a, **k: pytest.fail("구절 기반 답변 호출"))
    ctx = T.ToolContext()
    T.run_search_docs(ctx, "Claude Code의 quantum-entangle 플래그는 무엇인가요?")
    assert ctx.refusal_reason == "근거 부족" and ctx.contexts == []
    assert ctx.tool_calls[-1]["args"]["missing_terms"] == ["quantum-entangle"]
    assert T.missing_identifier_terms("--output-format 옵션 알려줘", [hit["text"]]) == []


def test_query_rewrite_rejects_chat_reply(monkeypatch):
    """검색어 변환 LLM이 검색어 대신 한국어 대화문을 내면 원문 질문으로 검색한다."""
    class FakeLLM:
        def __init__(self, out): self.out = out
        def invoke(self, _): return type("M", (), {"content": self.out})()
    monkeypatch.setattr(llm, "get_llm", lambda: FakeLLM("저는 이전 대화를 기억하지 못합니다.\n다시 알려주세요."))
    assert _REAL_REWRITE("방금 푼 문제 더 설명해줘") == "방금 푼 문제 더 설명해줘"
    monkeypatch.setattr(llm, "get_llm", lambda: FakeLLM("agentic loop stop_reason end_turn"))
    assert _REAL_REWRITE("루프 종료 조건") == "agentic loop stop_reason end_turn"


def test_general_fallback_not_used_for_pre_grading_hint(monkeypatch):
    """채점 전 힌트에서는 일반 지식 답변을 쓰지 않는다(정답 유출 방지)."""
    monkeypatch.setattr(retriever, "search", lambda *a, **k: [])
    monkeypatch.setattr(search, "llm_general_answer", lambda *a, **k: pytest.fail("힌트에서 일반 지식 호출"))
    q = T.load_questions()[0]
    T.set_pending("u", "s", q["id"])
    ctx = T.ToolContext(user_id="u", session_id="s")
    T.run_search_docs(ctx, "이 개념 설명해줘")
    assert ctx.refusal_reason == "근거 부족"
