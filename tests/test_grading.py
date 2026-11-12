"""채점(grade_answer)·진도(get_progress) 테스트(오프라인, 채점 경로 LLM 호출 0)."""
from __future__ import annotations

import sqlite3

import pytest

from src import tools as T


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setenv("PROGRESS_DB_PATH", str(tmp_path / "p.db"))

    def boom(*a, **k):
        raise AssertionError("채점·진도 경로에서 LLM을 호출하면 안 된다")

    monkeypatch.setattr(T, "get_llm", boom)


def _count(table: str) -> int:
    with sqlite3.connect(T.db_path()) as c:
        return c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _pose(uid="u", sid="s", seed=1, **kw):
    T.set_seed(seed)
    ctx = T.ToolContext(user_id=uid, session_id=sid)
    T.run_generate_quiz(ctx, **kw)
    return ctx.problem_id


def _grade(ans, uid="u", sid="s"):
    ctx = T.ToolContext(user_id=uid, session_id=sid)
    T.run_grade_answer(ctx, ans)
    return ctx


def _other(q):
    return next(k for k in "ABCD" if k != q["answer"])


@pytest.mark.parametrize("fmt", ["{a}", "{a}.", " {a} ", "{a_low}", "{a_low}."])
def test_correct_answer_normalized(fmt):
    q = T.question_by_id(_pose())
    ctx = _grade(fmt.format(a=q["answer"], a_low=q["answer"].lower()))
    assert ctx.grading == {"chosen": q["answer"], "correct": q["answer"]}
    assert ctx.db_writes == 2 and ctx.refusal_reason is None
    assert ctx.answer.startswith("## 채점 결과\n정답입니다")
    assert _count("pending") == 0 and _count("attempts") == 1


def test_wrong_answer_has_all_seven_sections_in_order():
    q = T.question_by_id(_pose())
    ctx = _grade(_other(q))
    assert ctx.db_writes == 2 and ctx.answer.count("오답입니다") == 1
    heads = ["## 채점 결과", "## 문제·보기 (한영 병기)", "## 문제 의도", "## 정답인 이유",
             "## 오답인 이유", "## 학습 포인트", "## 참고 공식문서"]
    pos = [ctx.answer.index(h) for h in heads]
    assert pos == sorted(pos)
    for needle in (q["stem_ko"], q["stem_en"], q["intent"], q["trap"], q["why_correct"],
                   q["learning_points"][0], q["references"][0]["url"]):
        assert needle in ctx.answer
    assert ctx.contexts and all(isinstance(c, str) for c in ctx.contexts)
    assert len(ctx.sources) == len(q["evidence"])
    assert all(s["url"] and s["doc_path"] and s["chunk_id"] for s in ctx.sources)


@pytest.mark.parametrize("bad", ["E", "1", "B,C", "", "  ", "AB"])
def test_invalid_answer_keeps_pending_no_write(bad):
    q = T.question_by_id(_pose())
    ctx = _grade(bad)
    assert ctx.refusal_reason == "유효하지 않은 답안" and ctx.db_writes == 0
    assert ctx.grading is None and q["why_correct"] not in ctx.answer and "정답:" not in ctx.answer
    assert "A, B, C, D" in ctx.answer and "유지" in ctx.answer
    assert _count("pending") == 1 and _count("attempts") == 0


def test_no_pending_and_regrade_no_write():
    ctx = _grade("A")
    assert ctx.refusal_reason == "대기 문제 없음" and ctx.db_writes == 0 and "문제 내줘" in ctx.answer
    _pose()
    _grade("A")
    again = _grade("B")
    assert again.refusal_reason == "대기 문제 없음" and again.db_writes == 0
    assert _count("attempts") == 1 and _count("pending") == 0


def test_missing_user_id_no_write():
    _pose()
    ctx = T.ToolContext(user_id=None, session_id="s")
    T.run_grade_answer(ctx, "A")
    assert ctx.refusal_reason == "ID 부족" and ctx.db_writes == 0 and "user_id" in ctx.answer
    assert _count("attempts") == 0 and _count("pending") == 1
    ctx = T.ToolContext(user_id="u", session_id=None)
    T.run_grade_answer(ctx, "A")
    assert ctx.refusal_reason == "ID 부족" and "session_id" in ctx.answer


def test_user_mismatch_refused():
    _pose(uid="u1")
    ctx = _grade("A", uid="u2")
    assert ctx.refusal_reason == "user_id 불일치" and ctx.db_writes == 0
    assert _count("attempts") == 0 and _count("pending") == 1


def test_progress_own_records_only():
    for i, uid in enumerate(["u", "u", "v"]):
        q = T.question_by_id(_pose(uid=uid, sid=f"s{i}", seed=i))
        _grade(_other(q) if i == 0 else q["answer"], uid=uid, sid=f"s{i}")
    ctx = T.ToolContext(user_id="u")
    T.run_get_progress(ctx)
    p = T.compute_progress("u")
    assert ctx.db_writes == 0 and ctx.contexts == [] and ctx.sources == []
    assert len(p["wrong"]) == 1 and p["wrong"][0]["scope"] == "exam"
    assert sum(d["attempted"] for d in p["domains"].values()) == 2
    assert "## 오답 목록" in ctx.answer and "study" in ctx.answer
    assert len(T.compute_progress("v")["wrong"]) == 0


def test_attempt_record_fields():
    q = T.question_by_id(_pose())
    _grade(q["answer"])
    with sqlite3.connect(T.db_path()) as c:
        cols = [r[1] for r in c.execute("PRAGMA table_info(attempts)")]
    assert {"user_id", "session_id", "question_id", "chosen", "correct", "ts"} <= set(cols)
