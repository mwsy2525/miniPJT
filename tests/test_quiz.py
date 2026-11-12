"""출제(generate_quiz)와 채점 응답의 contexts·sources 테스트(오프라인, LLM 없음)."""
from __future__ import annotations

import pytest

from src import tools as T


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setenv("PROGRESS_DB_PATH", str(tmp_path / "p.db"))


def _quiz(seed=1, sid="s", uid="u", **kw):
    T.set_seed(seed)
    ctx = T.ToolContext(user_id=uid, session_id=sid)
    T.run_generate_quiz(ctx, **kw)
    return ctx


def test_only_reviewed_questions_are_served(monkeypatch):
    qs = T.load_questions()
    bad = dict(qs[0], id="draft-1", reviewed=False)
    monkeypatch.setattr(T, "load_questions", lambda: qs + [bad])
    for seed in range(30):
        assert _quiz(seed=seed, sid=f"s{seed}").problem_id != "draft-1"


def test_domain_filter_and_seed_reproducible():
    for d in range(1, 6):
        ctx = _quiz(domain=d)
        assert T.question_by_id(ctx.problem_id)["domain"] == d
    a = [_quiz(seed=7, sid=f"a{i}").problem_id for i in range(3)]
    b = [_quiz(seed=7, sid=f"b{i}").problem_id for i in range(3)]
    assert a == b


def test_random_across_whole_bank_and_resolves_repeats(monkeypatch):
    T.set_seed(3)
    seen = set()
    for i in range(60):
        ctx = T.ToolContext(user_id="u", session_id=f"r{i}")
        T.run_generate_quiz(ctx)
        seen.add(ctx.problem_id)
    assert len({T.question_by_id(p)["domain"] for p in seen}) > 1
    # 이미 푼 문항도 다시 출제: 후보를 1개로 줄여도 계속 출제된다
    one = [q for q in T.load_questions() if q["domain"] == 1][:1]
    monkeypatch.setattr(T, "load_questions", lambda: one)
    for i in range(2):
        c = T.ToolContext(user_id="u", session_id="again")
        T.run_generate_quiz(c, domain=1)
        T.run_grade_answer(c, "A")
        assert c.problem_id == one[0]["id"]


@pytest.mark.parametrize("domain", [9, "nonexistent", 0])
def test_unknown_domain_refused_with_domain_list(domain):
    ctx = _quiz(domain=domain)
    assert ctx.refusal_reason == "출제 문항 없음"
    assert ctx.db_writes == 0 and ctx.problem_id is None
    assert "Domain 1" in ctx.answer and "Domain 5" in ctx.answer
    assert ctx.contexts == [] and ctx.sources == []
    assert T.get_pending("s") is None


def test_zero_reviewed_domain_refused(monkeypatch):
    qs = [dict(q, reviewed=False) if q["domain"] == 2 else q for q in T.load_questions()]
    monkeypatch.setattr(T, "load_questions", lambda: qs)
    ctx = _quiz(domain=2)
    assert ctx.refusal_reason == "출제 문항 없음" and ctx.db_writes == 0
    assert "Domain 1" in ctx.answer and "Domain 2:" not in ctx.answer


def test_quiz_answer_has_english_only_and_empty_contexts():
    ctx = _quiz()
    q = T.question_by_id(ctx.problem_id)
    assert ctx.db_writes == 1 and ctx.refusal_reason is None
    assert q["stem_en"] in ctx.answer
    for k in "ABCD":
        assert q["choices_en"][k] in ctx.answer
    assert q["stem_ko"] not in ctx.answer
    for k in "ABCD":
        assert q["choices_ko"][k] not in ctx.answer
    for f in ("why_correct", "intent", "trap"):
        assert q[f] not in ctx.answer
    for r in q["why_wrong"].values():
        assert r not in ctx.answer
    assert "정답:" not in ctx.answer and "정답입니다" not in ctx.answer
    assert ctx.contexts == [] and ctx.sources == []
    assert ctx.answer.splitlines()[0].startswith("[시험 범위 · Domain")


def test_study_without_study_questions_is_refused():
    ctx = _quiz(scope="study")
    assert ctx.refusal_reason == "출제 문항 없음" and ctx.db_writes == 0


def test_grading_has_evidence_contexts_and_sources(monkeypatch):
    ctx = _quiz()
    q = T.question_by_id(ctx.problem_id)
    monkeypatch.setattr(T.retriever, "get_chunk_text", lambda cid, dp: f"TEXT {cid}")
    g = T.ToolContext(user_id="u", session_id="s")
    T.run_grade_answer(g, q["answer"])
    ctx = g
    assert ctx.db_writes == 2
    assert ctx.contexts == [f"TEXT {e['chunk_id']}" for e in q["evidence"]]
    assert [(s["doc_path"], s["chunk_id"]) for s in ctx.sources] == [
        (e["doc_path"], e["chunk_id"]) for e in q["evidence"]]
    assert all(s["url"] for s in ctx.sources)


def test_grading_contexts_are_real_snapshot_text():
    ctx = _quiz()
    q = T.question_by_id(ctx.problem_id)
    T.run_grade_answer(ctx, "a")
    assert ctx.contexts and all(isinstance(c, str) and c.strip() for c in ctx.contexts)
    assert len(ctx.sources) == len(q["evidence"])
