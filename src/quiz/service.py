"""generate_quiz: 검수된 문제 은행에서 영어 문제·보기만 출제한다(LLM 없음)."""
from __future__ import annotations

import random
import re
from typing import Any

from src.question_bank import service as bank
from src.sessions import service as sessions
from src.shared.config import env as _env
from src.shared.context import ToolContext

_rng: random.Random | None = None


def set_seed(seed: int | None) -> None:
    """무작위 출제 시드를 주입한다(None이면 .env QUIZ_SEED 또는 시스템 난수)."""
    global _rng
    _rng = random.Random(seed) if seed is not None else None


def _get_rng() -> random.Random:
    """출제용 난수 생성기."""
    global _rng
    if _rng is None:
        s = _env("QUIZ_SEED")
        _rng = random.Random(int(s)) if s.strip() else random.Random()
    return _rng


def _eligible(scope: str) -> list[dict[str, Any]]:
    """출제 가능한 검수 문항(reviewed=true, 범위 일치, exam은 Task Statement가 yaml에 있어야 함)."""
    valid_ts = bank.valid_task_statements()
    out = []
    for q in bank.load_questions():
        if q.get("reviewed") is not True or q.get("scope") != scope:
            continue
        if scope == "exam" and str(q.get("task_statement")) not in valid_ts:
            continue
        out.append(q)
    return out


def _parse_domain(domain: Any) -> int | None | str:
    """domain 인자를 번호로 해석한다. 지정 없음은 None, 해석 불가는 'invalid'."""
    if domain is None or str(domain).strip() == "":
        return None
    s = str(domain).strip()
    m = re.search(r"\d+", s)
    if m:
        return int(m.group())
    for d in bank.load_domains()["domains"]:
        if s.lower() in d["name"].lower() or d["name"].lower() in s.lower():
            return d["id"]
    return "invalid"


def _format_quiz(q: dict[str, Any], skipped: bool) -> str:
    """출제 응답 본문(영어 문제·보기만). 첫 줄에 범위 표시를 붙인다."""
    if q["scope"] == "exam":
        label = f"[시험 범위 · Domain {q['domain']} · Task {q['task_statement']}]"
    else:
        label = "[공식 문서 학습용]"
    lines = [label]
    if skipped:
        lines.append("이전에 출제된 풀지 않은 문제는 건너뛰고 새 문제로 바꿨습니다(시도 기록에는 남기지 않습니다).")
    lines += ["", f"문제 ID: {q['id']}", "", q["stem_en"], ""]
    lines += [f"{k}. {q['choices_en'][k]}" for k in "ABCD"]
    lines += ["", "답안을 A~D 중 하나로 보내 주세요."]
    return "\n".join(lines)


def run_generate_quiz(ctx: ToolContext, domain: Any = None, scope: str = "exam",
                      exclude: list[str] | None = None) -> str:
    """검수된 문제 은행에서 한 문항을 출제하고 대기 문제로 저장한다(db_writes=1).

    exclude는 이번 세트에서 이미 낸 문항 ID다. 남은 문항이 없으면 직전 문항만 피해서 다시 낸다.
    """
    ctx.record_call("generate_quiz", {"domain": domain, "scope": scope})
    if not ctx.both_ids:
        return ctx.finish("출제에는 user_id와 session_id가 모두 필요합니다. 두 ID를 함께 보내 주세요.", "ID 부족")
    scope = "study" if str(scope).strip().lower() == "study" else "exam"
    full_pool = _eligible(scope)
    pool = full_pool
    dom = _parse_domain(domain)

    def refuse() -> str:
        avail = sorted({q["domain"] for q in full_pool})
        listing = "\n".join(f"- Domain {d}: {bank.domain_name(d)}" for d in avail) or "(없음)"
        return ctx.finish("요청한 영역에는 출제할 수 있는 검수 문항이 없습니다.\n"
                          f"사용 가능한 영역 목록({'시험 범위' if scope == 'exam' else '학습용'}):\n{listing}",
                          "출제 문항 없음")

    if dom == "invalid":
        return refuse()
    if dom is not None:
        pool = [q for q in pool if q["domain"] == dom]
    if not pool:
        return refuse()
    if exclude:
        pool = ([q for q in pool if q["id"] not in exclude]
                or [q for q in pool if q["id"] != exclude[-1]] or pool)
    q = _get_rng().choice(pool)
    skipped = sessions.get_pending(ctx.session_id) is not None
    ctx.db_writes += sessions.set_pending(ctx.user_id, ctx.session_id, q["id"])
    ctx.problem_id = q["id"]
    return ctx.finish(_format_quiz(q, skipped))
