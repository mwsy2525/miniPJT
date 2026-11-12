"""ID 조합 규칙, 대기 문제 생애주기(영속·교체·user_id 불일치) 테스트(오프라인, mock)."""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src import agent as A
from src import retriever, tools as T
from test_api_contract import ScriptedModel, post

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROGRESS_DB_PATH", str(tmp_path / "p.db"))
    monkeypatch.setattr(A, "get_agent_llm", lambda: ScriptedModel())
    monkeypatch.setattr(retriever, "search", lambda *a, **k: [])
    T.set_seed(1)
    return TestClient(A.app)


class IdRefuser(ScriptedModel):
    """허용 도구에 없는 요청에 ID 부족으로 거절하는 가짜 모델."""

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        from langchain_core.messages import AIMessage
        from langchain_core.outputs import ChatGeneration, ChatResult
        msg = AIMessage(content="", tool_calls=[{"name": "AgentReply", "id": "c0", "args": {
            "answer": "user_id와 session_id가 모두 필요합니다.", "refusal_reason": "ID 부족"}}])
        return ChatResult(generations=[ChatGeneration(message=msg)])


def _count(tmp_path, table):
    with sqlite3.connect(tmp_path / "p.db") as c:
        return c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


@pytest.mark.parametrize("uid,sid,expected", [
    (None, None, ["search_docs"]),
    (None, "s", ["search_docs"]),
    ("u", None, ["search_docs", "get_progress"]),
    ("u", "s", ["search_docs", "generate_quiz", "grade_answer", "get_progress"]),
])
def test_allowed_tools_per_id_combination(uid, sid, expected):
    assert [t.name for t in T.build_tools(T.ToolContext(user_id=uid, session_id=sid))] == expected


@pytest.mark.parametrize("ids", [{}, {"user_id": "u"}, {"session_id": "s"}])
@pytest.mark.parametrize("question", ["문제 내줘", "B"])
def test_quiz_and_grade_without_both_ids_write_nothing(client, tmp_path, monkeypatch, ids, question):
    monkeypatch.setattr(A, "get_agent_llm", lambda: IdRefuser())
    r = post(client, question=question, **ids)
    assert r["trace"]["db_writes"] == 0
    assert r["trace"]["tool_calls"] == []  # 허용되지 않은 도구는 넘기지 않았다
    assert r["trace"]["refusal_reason"] == "ID 부족"
    assert not (tmp_path / "p.db").exists() or _count(tmp_path, "attempts") + _count(tmp_path, "pending") == 0


def test_tool_level_missing_id_guides_required_ids(tmp_path, monkeypatch):
    monkeypatch.setenv("PROGRESS_DB_PATH", str(tmp_path / "p.db"))
    for ctx, fn, args in [(T.ToolContext(user_id="u"), T.run_generate_quiz, ()),
                          (T.ToolContext(session_id="s"), T.run_grade_answer, ("A",))]:
        out = fn(ctx, *args)
        assert "user_id" in out and "session_id" in out
        assert ctx.db_writes == 0 and ctx.refusal_reason == "ID 부족"


def test_pending_survives_restart(tmp_path, monkeypatch):
    db = tmp_path / "p.db"
    monkeypatch.setenv("PROGRESS_DB_PATH", str(db))
    T.set_seed(1)
    ctx = T.ToolContext(user_id="u", session_id="s")
    T.run_generate_quiz(ctx)
    pid = ctx.problem_id
    code = ("from src import tools as T; p=T.get_pending('s'); print(p['user_id'], p['problem_id'])")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                         env={**os.environ, "PROGRESS_DB_PATH": str(db)})
    assert out.stdout.split() == ["u", pid], out.stderr


def test_new_quiz_replaces_pending(client, tmp_path):
    first = post(client, question="문제 내줘", user_id="u", session_id="s")
    assert first["trace"]["db_writes"] == 1 and "건너뛰" not in first["answer"]
    second = post(client, question="문제 내줘", user_id="u", session_id="s")
    assert second["trace"]["db_writes"] == 1
    assert "건너뛰" in second["answer"]
    assert _count(tmp_path, "pending") == 1 and _count(tmp_path, "attempts") == 0
    assert T.get_pending("s")["problem_id"] == second["trace"]["problem_id"]


def test_grade_with_other_user_refused(client, tmp_path):
    q = post(client, question="문제 내줘", user_id="u1", session_id="s")
    r = post(client, question="A", user_id="u2", session_id="s")
    assert r["trace"]["db_writes"] == 0
    assert r["trace"]["refusal_reason"] == "user_id 불일치"
    assert r["trace"]["grading"] is None
    assert "user_id" in r["answer"]
    assert _count(tmp_path, "attempts") == 0
    assert T.get_pending("s")["problem_id"] == q["trace"]["problem_id"]
    assert T.get_pending("s")["user_id"] == "u1"


def test_pre_grading_guard_refuses_without_llm(tmp_path, monkeypatch):
    """대기 문제의 정답·보기 정오를 묻는 질문은 LLM과 도구 없이 코드가 거절하고, 답안 형태 입력은 통과시킨다."""
    import pytest
    from src import agent as A, tools as T
    monkeypatch.setenv("PROGRESS_DB_PATH", str(tmp_path / "p.db"))
    T.set_pending("u", "s", T.load_questions()[0]["id"])
    ctx = T.ToolContext(user_id="u", session_id="s")
    for q in ("A가 왜 정답이야?", "이 문제 정답이 뭐야?", "문제를 한국어로 번역해줘", "B가 맞아?"):
        assert A._pre_grading_guard(ctx, q), q
    for q in ("B", "B인 것 같아", "정답은 C", "B가 맞는 것 같아", "agentic loop 개념을 설명해줘"):
        assert not A._pre_grading_guard(ctx, q), q
    monkeypatch.setattr(A, "get_agent_llm", lambda: pytest.fail("LLM 호출 발생"))
    r = A.run_query("A가 왜 정답이야?", "u", "s")
    assert r["trace"]["tool_calls"] == [] and r["trace"]["refusal_reason"] == "채점 전 해설 요청"
    assert r["answer"] == A.REFUSAL_TEXT["채점 전 해설 요청"] and T.get_pending("s") is not None
    assert not A._pre_grading_guard(T.ToolContext(user_id="other", session_id="s"), "A가 왜 정답이야?")
