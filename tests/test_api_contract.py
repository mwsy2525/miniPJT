"""POST /query 입출력 계약 테스트(Bedrock·웹 조회는 mock, 오프라인 실행)."""
from __future__ import annotations

import json
import re
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from src import agent as A
from src import tools as T
from src.orchestration import service as orch
from src.retrieval import service as retriever
from src.search import service as search
from src.shared import llm

TOP = {"answer", "contexts", "trace"}
TRACE = {"tool_calls", "problem_id", "grading", "refusal_reason", "db_writes", "sources"}


class ScriptedModel(BaseChatModel):
    """질문 내용으로 도구 호출을 정하는 가짜 tool-calling 모델."""

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        last = messages[-1]
        if last.type == "tool":
            msg = AIMessage(content="done")
        else:
            q = messages[-1].content if last.type == "human" else ""
            human = [m for m in messages if m.type == "human"][-1].content
            q = human
            if re.fullmatch(r"\s*[A-Ea-e][.]?\s*|B,C|B인 것 같아", q):
                m = re.match(r"\s*([A-Za-z,]+)", q)
                calls = [("grade_answer", {"answer": m.group(1)})]
            elif "문제 내줘" in q:
                calls = [("generate_quiz", {"domain": None, "scope": "exam"})]
            elif "진도" in q:
                calls = [("get_progress", {})]
            elif "stop_reason" in q:
                calls = [("search_docs", {"question": q})]
            elif "날씨" in q:
                calls = [("AgentReply", {"answer": "범위 밖입니다.", "refusal_reason": "범위 밖 질문"})]
            else:
                calls = [("AgentReply", {"answer": "ID가 필요합니다.", "refusal_reason": "ID 부족"})]
            msg = AIMessage(content="", tool_calls=[
                {"name": n, "args": a, "id": f"c{i}"} for i, (n, a) in enumerate(calls)])
        return ChatResult(generations=[ChatGeneration(message=msg)])


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROGRESS_DB_PATH", str(tmp_path / "p.db"))
    monkeypatch.setattr(orch, "get_agent_llm", lambda: ScriptedModel())
    monkeypatch.setattr(search, "llm_rewrite_query", lambda q: "stop_reason tool_use")
    monkeypatch.setattr(search, "llm_answer", lambda *a, **k: T.SearchAnswer(answer="stop_reason은 tool_use/end_turn을 구분합니다."))
    monkeypatch.setattr(retriever, "search", lambda q, k=4, domain=None, store=None: [
        {"text": "passage text about stop_reason", "doc_path": "platform/handling-stop-reasons.md",
         "chunk_id": "handling-stop-reasons#end-turn", "url": "https://x/y", "score": 0.9}])
    T.set_seed(1)
    return TestClient(A.app)


def post(c, **body):
    r = c.post("/query", json=body)
    assert r.status_code == 200, r.text
    j = r.json()
    assert set(j) == TOP and isinstance(j["contexts"], list)
    assert set(j["trace"]) == TRACE
    assert all(isinstance(x, str) for x in j["contexts"])
    return j


def test_quiz_then_grade(client):
    q = post(client, question="문제 내줘", user_id="u", session_id="s")
    assert q["trace"]["tool_calls"][0]["name"] == "generate_quiz"
    assert q["contexts"] == [] and q["trace"]["sources"] == [] and q["trace"]["db_writes"] == 1
    assert q["answer"].startswith("[시험 범위")
    g = post(client, question="B인 것 같아", user_id="u", session_id="s")
    assert g["trace"]["tool_calls"] == [{"name": "grade_answer", "args": {"answer": "B"}}]
    assert g["trace"]["db_writes"] == 2 and g["trace"]["grading"]["chosen"] == "B"
    assert g["contexts"] and all(set(s) == {"url", "doc_path", "chunk_id"} for s in g["trace"]["sources"])
    for sec in ["채점 결과", "한영 병기", "문제 의도", "정답인 이유", "오답인 이유", "학습 포인트", "참고 공식문서"]:
        assert sec in g["answer"] or sec.replace("한영 병기", "한영 병기") in g["answer"]
    again = post(client, question="B", user_id="u", session_id="s")
    assert again["trace"]["refusal_reason"] == "대기 문제 없음" and again["trace"]["db_writes"] == 0


def test_progress_and_id_rules(client):
    post(client, question="문제 내줘", user_id="u", session_id="s")
    post(client, question="A", user_id="u", session_id="s")
    p = post(client, question="내 진도 보여줘", user_id="u")
    assert p["trace"]["tool_calls"][0]["name"] == "get_progress" and p["contexts"] == []
    # ID 조합별 허용 도구: 허용되지 않은 도구는 에이전트에 아예 넘기지 않는다
    assert [t.name for t in T.build_tools(T.ToolContext())] == ["search_docs"]
    assert [t.name for t in T.build_tools(T.ToolContext(session_id="s"))] == ["search_docs"]
    assert [t.name for t in T.build_tools(T.ToolContext(user_id="u"))] == ["search_docs", "get_progress"]
    assert len(T.build_tools(T.ToolContext(user_id="u", session_id="s"))) == 4


def test_toolless_refusal_and_validation(client):
    r = post(client, question="오늘 날씨 어때")
    assert r["trace"]["tool_calls"] == [] and r["trace"]["refusal_reason"] == "범위 밖 질문"
    # ID 부족은 두 ID가 모두 있으면 코드가 기록하지 않는다
    ok = post(client, question="알 수 없는 요청", user_id="u", session_id="s")
    assert ok["trace"]["refusal_reason"] is None
    idr = post(client, question="알 수 없는 요청", session_id="s")
    assert idr["trace"]["refusal_reason"] == "ID 부족"


def test_search_docs_and_invalid_answer(client):
    s = post(client, question="stop_reason이 뭐야?")
    assert s["trace"]["tool_calls"][0]["name"] == "search_docs"
    assert s["trace"]["tool_calls"][0]["args"]["search_query_en"] == "stop_reason tool_use"
    assert s["contexts"] == ["passage text about stop_reason"]
    assert s["trace"]["sources"][0]["chunk_id"] == "handling-stop-reasons#end-turn"
    post(client, question="문제 내줘", user_id="u", session_id="s")
    bad = post(client, question="B,C", user_id="u", session_id="s")
    assert bad["trace"]["refusal_reason"] == "유효하지 않은 답안" and bad["trace"]["db_writes"] == 0
    assert T.get_pending("s") is not None


@pytest.mark.parametrize("body", [
    {"question": ""}, {"question": "   "}, {"question": "x" * 2001}, {"question": 5},
    {"question": "ok", "user_id": 1}, {"question": "ok", "session_id": ["a"]}, {},
])
def test_validation_422_no_db_write(client, tmp_path, body):
    assert client.post("/query", json=body).status_code == 422
    assert not (tmp_path / "p.db").exists()


def test_button_routes_work_without_llm(tmp_path, monkeypatch):
    """화면 버튼 경로(/quiz, /grade, /progress)는 LLM 없이 같은 응답 형태로 동작한다."""
    from fastapi.testclient import TestClient
    from src import agent as A, tools as T
    monkeypatch.setenv("PROGRESS_DB_PATH", str(tmp_path / "p.db"))
    monkeypatch.setattr(llm, "get_llm", lambda: pytest.fail("LLM 호출 발생"))
    monkeypatch.setattr(orch, "get_agent_llm", lambda: pytest.fail("LLM 호출 발생"))
    c = TestClient(A.app)
    ids = {"user_id": "u", "session_id": "s"}
    seen = []
    for _ in range(len(T._eligible("exam"))):
        r = c.post("/quiz", json={**ids, "exclude": seen}).json()
        assert set(r) == {"answer", "contexts", "trace"} and r["contexts"] == [] and r["trace"]["db_writes"] == 1
        assert r["trace"]["problem_id"] not in seen
        seen.append(r["trace"]["problem_id"])
    again = c.post("/quiz", json={**ids, "exclude": seen}).json()["trace"]["problem_id"]
    assert again in seen and again != seen[-1]  # 다 풀면 직전 문항만 피해서 반복
    g = c.post("/grade", json={**ids, "answer": "A"}).json()
    assert g["trace"]["grading"]["chosen"] == "A" and g["trace"]["db_writes"] == 2
    p = c.post("/progress", json={"user_id": "u"}).json()
    assert "정답률" in p["answer"] and p["contexts"] == []
    assert c.post("/quiz", json={"user_id": "u"}).status_code == 422
