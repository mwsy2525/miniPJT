"""RAG 코퍼스·검색 테스트: 원문 contexts/URL sources, 웹 폴백 on/off, 영역 지정 검색(오프라인, mock)."""
from __future__ import annotations

import pytest

from src import retriever, tools as T


@pytest.fixture(autouse=True)
def _mocks(tmp_path, monkeypatch):
    monkeypatch.setenv("PROGRESS_DB_PATH", str(tmp_path / "p.db"))
    monkeypatch.setattr(T, "llm_rewrite_query", lambda q: "tool use")
    monkeypatch.setattr(T, "llm_answer", lambda *a, **k: T.SearchAnswer(answer="답변입니다."))
    monkeypatch.setattr(T, "llm_general_answer", lambda *a, **k: T.GeneralAnswer(unknown=True))


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
    monkeypatch.setenv("WEB_SEARCH_API_KEY", "k")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("웹 조회 발생"))
    assert retriever.web_search("q") == []
    monkeypatch.setattr(retriever, "search", lambda *a, **k: [])
    ctx = T.ToolContext()
    out = T.run_search_docs(ctx, "tool use?")
    assert ctx.contexts == [] and "근거 부족" in out


def test_web_fallback_filters_domains_and_errors(monkeypatch):
    import httpx
    monkeypatch.setenv("WEB_FALLBACK_ENABLED", "true")
    monkeypatch.setenv("WEB_SEARCH_API_KEY", "k")

    class R:
        def raise_for_status(self): pass
        def json(self):
            return {"results": [{"url": "https://evil.example.com/a", "content": "bad"},
                                {"url": "https://code.claude.com/docs/en/a", "content": "good"}]}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: R())
    assert [r["url"] for r in retriever.web_search("q")] == ["https://code.claude.com/docs/en/a"]

    def boom(*a, **k):
        raise httpx.TimeoutException("t")

    monkeypatch.setattr(httpx, "post", boom)
    assert retriever.web_search("q") == []


def test_general_knowledge_fallback_when_no_evidence(monkeypatch):
    """문서 근거가 없으면 표시를 붙여 AI 일반 지식으로 답하고, contexts와 sources는 비운다."""
    monkeypatch.setattr(retriever, "search", lambda *a, **k: [])
    monkeypatch.setattr(T, "llm_general_answer", lambda *a, **k: T.GeneralAnswer(answer="Claude는 Anthropic의 모델입니다."))
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


def test_general_fallback_not_used_for_pre_grading_hint(monkeypatch):
    """채점 전 힌트에서는 일반 지식 답변을 쓰지 않는다(정답 유출 방지)."""
    monkeypatch.setattr(retriever, "search", lambda *a, **k: [])
    monkeypatch.setattr(T, "llm_general_answer", lambda *a, **k: pytest.fail("힌트에서 일반 지식 호출"))
    q = T.load_questions()[0]
    T.set_pending("u", "s", q["id"])
    ctx = T.ToolContext(user_id="u", session_id="s")
    T.run_search_docs(ctx, "이 개념 설명해줘")
    assert ctx.refusal_reason == "근거 부족"
