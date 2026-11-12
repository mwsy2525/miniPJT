"""grade_answer: 정답 키와 결정적으로 비교하고 검수 해설 세트로 응답을 채운다(LLM 없음)."""
from __future__ import annotations

from typing import Any

from src.question_bank import service as bank
from src.retrieval import service as retriever
from src.sessions import service as sessions
from src.shared.context import ToolContext


def normalize_answer(raw: Any) -> str | None:
    """답안 정규화: 대소문자·공백·마침표 차이만 있는 A~D를 인정한다. 그 외는 None."""
    s = str(raw if raw is not None else "").strip().rstrip(".").strip().upper()
    return s if s in {"A", "B", "C", "D"} else None


def _source_for(ev: dict[str, str]) -> dict[str, Any]:
    """evidence 항목을 trace.sources 항목으로 바꾼다."""
    d = retriever.doc_by_path(ev["doc_path"])
    return {"url": d["url"] if d else None, "doc_path": ev["doc_path"], "chunk_id": ev["chunk_id"]}


def build_grading_answer(q: dict[str, Any], chosen: str, correct: bool) -> str:
    """검수 해설 세트로 채점 응답 템플릿을 채운다(LLM 없음). 순서는 고정 7개 섹션."""
    ans = q["answer"]
    head = (f"정답입니다. 선택: {chosen}" if correct else f"오답입니다. 선택: {chosen} / 정답: {ans}")
    bilingual = [f"**Q.** {q['stem_en']}", f"**문제.** {q['stem_ko']}", ""]
    for k in "ABCD":
        mark = " ✅" if k == ans else ""
        bilingual.append(f"- {k}. {q['choices_en'][k]} / {q['choices_ko'][k]}{mark}")
    wrong = "\n".join(f"- {k}: {q['why_wrong'][k]}" for k in "ABCD" if k != ans and k in q["why_wrong"])
    points = "\n".join(f"- {p}" for p in q["learning_points"])
    refs = "\n".join(f"- {r['title']} — 읽을 부분: {r['note']}\n  {r['url']}" for r in q["references"])
    return "\n\n".join([
        f"## 채점 결과\n{head}",
        "## 문제·보기 (한영 병기)\n" + "\n".join(bilingual),
        f"## 문제 의도\n- 평가 포인트: {q['intent']}\n- 함정: {q['trap']}",
        f"## 정답인 이유\n{q['why_correct']}",
        f"## 오답인 이유\n{wrong}",
        f"## 학습 포인트\n{points}",
        f"## 참고 공식문서\n{refs}",
    ])


def run_grade_answer(ctx: ToolContext, answer: str) -> str:
    """대기 문제를 정답 키와 결정적으로 비교해 채점한다(db_writes=2, 거절 시 0)."""
    ctx.record_call("grade_answer", {"answer": answer})
    if not ctx.both_ids:
        return ctx.finish("채점에는 user_id와 session_id가 모두 필요합니다. 두 ID를 함께 보내 주세요.", "ID 부족")
    pend = sessions.get_pending(ctx.session_id)
    if not pend:
        return ctx.finish("이 세션에 채점할 대기 문제가 없습니다(출제되지 않았거나 이미 채점됨). "
                          "먼저 \"문제 내줘\"로 새 문제를 출제받은 뒤 답해 주세요.", "대기 문제 없음")
    if pend["user_id"] != ctx.user_id:
        return ctx.finish("이 문제는 다른 user_id로 출제되어 채점하지 않습니다. 문제를 받은 user_id로 답하거나 "
                          "새 문제를 요청해 주세요.", "user_id 불일치")
    ctx.problem_id = pend["problem_id"]
    chosen = normalize_answer(answer)
    if chosen is None:
        return ctx.finish("유효하지 않은 답안입니다. 보기 A, B, C, D 중 하나만 골라 답해 주세요. "
                          "대기 중인 문제는 그대로 유지됩니다.", "유효하지 않은 답안")
    q = bank.question_by_id(pend["problem_id"])
    if q is None:
        return ctx.finish("대기 문제를 문제 은행에서 찾을 수 없어 채점하지 못했습니다. 새 문제를 요청해 주세요.",
                          "대기 문제 없음")
    correct = chosen == q["answer"]
    ctx.db_writes += sessions.record_grading(ctx.user_id, ctx.session_id, q["id"], chosen, correct)
    ctx.grading = {"chosen": chosen, "correct": q["answer"]}
    ctx.contexts = [t for t in (retriever.get_chunk_text(e["chunk_id"], e["doc_path"]) for e in q["evidence"]) if t]
    ctx.sources = [_source_for(e) for e in q["evidence"]]
    return ctx.finish(build_grading_answer(q, chosen, correct))
