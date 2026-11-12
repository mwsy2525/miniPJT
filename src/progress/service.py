"""get_progress: 본인 진도를 코드로 계산한다(LLM 없음). 정답률은 scope=exam 문항만."""
from __future__ import annotations

from typing import Any

from src.question_bank import service as bank
from src.sessions import service as sessions
from src.shared.context import ToolContext


def compute_progress(user_id: str) -> dict[str, Any]:
    """진도를 계산한다: 영역별 정답률(scope=exam만), 오답 목록(두 범위), study 풀이 수."""
    by_id = {q["id"]: q for q in bank.load_questions()}
    per_domain: dict[int, list[int]] = {}
    wrong: list[dict[str, Any]] = []
    study_solved = 0
    for a in sessions.user_attempts(user_id):
        q = by_id.get(a["question_id"])
        if not q:
            continue
        if q["scope"] == "exam":
            tot = per_domain.setdefault(q["domain"], [0, 0])
            tot[0] += 1
            tot[1] += a["correct"]
        else:
            study_solved += 1
        if not a["correct"]:
            wrong.append({"question_id": q["id"], "domain": q["domain"], "scope": q["scope"],
                          "chosen": a["chosen"], "correct": q["answer"]})
    return {"domains": {d: {"attempted": t[0], "correct": t[1], "rate": t[1] / t[0]}
                        for d, t in sorted(per_domain.items())},
            "wrong": wrong, "study_solved": study_solved}


def run_get_progress(ctx: ToolContext) -> str:
    """요청 user_id 본인의 진도(영역별 정답률[exam], 오답 목록, study 풀이 수)를 코드로 계산한다."""
    ctx.record_call("get_progress", {})
    if not ctx.user_id:
        return ctx.finish("진도 조회에는 user_id가 필요합니다.", "ID 부족")
    p = compute_progress(ctx.user_id)
    lines = ["## 영역별 정답률 (시험 범위 문항만)", "| 영역 | 푼 문항 | 정답 | 정답률 |", "|---|---|---|---|"]
    for d in bank.load_domains()["domains"]:
        s = p["domains"].get(d["id"])
        if s:
            lines.append(f"| Domain {d['id']} {d['name']} | {s['attempted']} | {s['correct']} | {s['rate']:.0%} |")
        else:
            lines.append(f"| Domain {d['id']} {d['name']} | 0 | 0 | - |")
    lines += ["", "## 오답 목록"]
    if p["wrong"]:
        lines += ["| 문항 ID | 영역 | 범위 | 고른 답 | 정답 |", "|---|---|---|---|---|"]
        lines += [f"| {w['question_id']} | Domain {w['domain']} | {w['scope']} | {w['chosen']} | {w['correct']} |"
                  for w in p["wrong"]]
    else:
        lines.append("오답 기록이 없습니다.")
    lines += ["", f"## 학습용(study) 문항 풀이 수\n{p['study_solved']}문항"]
    return ctx.finish("\n".join(lines))
