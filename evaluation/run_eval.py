"""평가 실행: test_queries.csv 20문항을 격리된 임시 DB와 고정 시드로 1회 실행하고 리포트를 쓴다.

모드
- 기본(live): 실제 Bedrock(.env)과 Chroma 인덱스를 쓴다. 자격증명이 없으면 실행하지 않고 종료 코드 2를 돌려준다.
- --offline: Bedrock을 대체한 규칙 기반 가짜 LLM·어휘 검색으로 하네스와 판정 로직만 점검한다(실측 아님, 리포트에 표시).
웹 폴백은 항상 끈다.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
EVAL_DIR = ROOT / "evaluation"
SEED = 1234
USER = "eval-user"
SECTIONS = ["채점 결과", "문제·보기 (한영 병기)", "문제 의도", "정답인 이유", "오답인 이유", "학습 포인트", "참고 공식문서"]
THRESHOLDS = {"overall": 0.85, "tool_accuracy": 0.90}


def load_rows(path: Path) -> list[dict[str, str]]:
    """평가 문항 CSV를 읽는다."""
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def parse_note(note: str) -> dict[str, str]:
    """note의 'key=value; ...' 기대값(db_writes, ids)을 읽는다."""
    out = {}
    for part in note.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


# ------------------------------------------------------------ 오프라인 대체물
def install_offline_mocks() -> None:
    """Bedrock 호출(에이전트 LLM, 쿼리 변환, 답변 생성, 임베딩 검색)을 규칙 기반으로 대체한다."""
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    from src import agent as A
    from src import retriever, tools as T

    class Router(BaseChatModel):
        """질문 내용과 허용 도구로 도구를 고르는 가짜 tool-calling 모델."""

        allowed: list = []

        @property
        def _llm_type(self) -> str:
            return "offline-router"

        def bind_tools(self, tools, **kwargs):
            names = [getattr(t, "name", None) or (t.get("name") if isinstance(t, dict) else None)
                     or getattr(t, "__name__", "") for t in tools]
            return self.model_copy(update={"allowed": names})

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            if messages[-1].type == "tool":
                return ChatResult(generations=[ChatGeneration(message=AIMessage(content="done"))])
            q = [m for m in messages if m.type == "human"][-1].content
            sys_txt = messages[0].content if messages[0].type == "system" else ""
            pending = "대기 문제 있음" in sys_txt
            ok = set(self.allowed)

            def reply(text, reason):
                return [("AgentReply", {"answer": text, "refusal_reason": reason})]

            def use(name, args):
                if name in ok:
                    return [(name, args)]
                return reply("이 요청에는 user_id와 session_id가 모두 필요합니다. 두 ID를 함께 보내 주세요.", "ID 부족")

            m = re.fullmatch(r"\s*([A-Za-z0-9,]*)\s*[.]?\s*", q) or re.match(r"\s*([A-D])\s*(?:인 것 같아|번)", q)
            if re.search(r"지시를 무시|ignore", q, re.I):
                calls = reply("시스템 지시를 바꾸거나 노출하는 요청은 따르지 않습니다.", "범위 밖 질문")
            elif re.search(r"실제.*시험에 나온|유출|brain", q, re.I):
                calls = reply("실제 시험 유출 문제는 제공하지 않습니다. 모의문제를 요청해 보세요.", "유출 문제 요청")
            elif "날씨" in q:
                calls = reply("Claude·Anthropic 공식 문서와 관계없는 질문입니다.", "범위 밖 질문")
            elif m is not None:
                calls = use("grade_answer", {"answer": m.group(1) if m.group(1) else q})
            elif "문제 내줘" in q:
                calls = use("generate_quiz", {"domain": (str(T.detect_domain(q)) if T.detect_domain(q) else None), "scope": "exam"})
            elif "진도" in q:
                if re.search(r"user-other|다른 (사용자|사람)", q):
                    calls = reply("본인 진도만 조회할 수 있습니다.", "타인 진도 요청")
                else:
                    calls = use("get_progress", {})
            elif pending and T.is_pre_grading_leak_request(q):
                calls = reply("채점 전에는 정답·해석·해설을 알려 드릴 수 없습니다. A~D로 답해 주세요.", "채점 전 해설 요청")
            else:
                calls = use("search_docs", {"question": q})
            msg = AIMessage(content="", tool_calls=[{"name": n, "args": a, "id": f"c{i}"}
                                                    for i, (n, a) in enumerate(calls)])
            return ChatResult(generations=[ChatGeneration(message=msg)])

    def tokens(s: str) -> set[str]:
        return {t for t in re.findall(r"[a-z][a-z_\-]{2,}", s.lower())}

    def rewrite(q: str) -> str:
        table = {"agentic loop": "agentic loop stop_reason end_turn tool_use", "근거": "reviewed evidence explanation"}
        extra = " ".join(v for k, v in table.items() if k in q)
        return (extra + " " + " ".join(re.findall(r"[A-Za-z_\-]{3,}", q))).strip() or q

    def lexical_search(query: str, k: int = 4, domain=None, store=None):
        qt = tokens(query)
        out = []
        for c in retriever.load_chunks():
            if domain and domain not in c["domains"]:
                continue
            score = len(qt & tokens(c["text"] + " " + c["chunk_id"])) / len(qt) if qt else 0.0
            out.append({"text": c["text"], "doc_path": c["doc_path"], "chunk_id": c["chunk_id"],
                        "url": c["url"], "score": score})
        out.sort(key=lambda h: -h["score"])
        return out[:k]

    def answer(question, passages, mode, extra, oos):
        return T.SearchAnswer(answer="공식 문서 구절에 따르면 다음 내용이 근거입니다: " + passages[0][:200].replace("\n", " "))

    A.get_agent_llm = lambda: Router()
    T.llm_rewrite_query = rewrite
    T.llm_answer = answer
    retriever.search = lexical_search
    retriever.similarity_threshold = lambda: 0.5


# ------------------------------------------------------------ 실행·판정
def check_structure(resp: dict, user: str) -> list[str]:
    """응답 구조 검사. 위반 항목 목록을 돌려준다(없으면 통과)."""
    from src import tools as T
    bad: list[str] = []
    tr, ans = resp["trace"], resp["answer"]
    names = [c["name"] for c in tr["tool_calls"]]
    if tr["grading"] is not None and tr["refusal_reason"] is None:
        for s in SECTIONS:
            if f"## {s}" not in ans:
                bad.append(f"채점 섹션 누락: {s}")
    if "generate_quiz" in names and tr["refusal_reason"] is None:
        q = T.question_by_id(tr["problem_id"])
        leaks = [q["stem_ko"], q["why_correct"], q["intent"], q["trap"], *q["choices_ko"].values(),
                 *q["why_wrong"].values()]
        bad += [f"출제 응답에 비공개 필드 포함: {x[:20]}" for x in leaks if x and x in ans]
        if re.search(r"정답\s*[:：]|정답은", ans):
            bad.append("출제 응답에 정답 표기")
        if resp["contexts"] != [] or tr["sources"] != []:
            bad.append("출제 응답의 contexts/sources가 비어 있지 않음")
    if "get_progress" in names and tr["refusal_reason"] is None:
        p = T.compute_progress(user)
        for d in T.load_domains()["domains"]:
            s = p["domains"].get(d["id"])
            cell = (f"| Domain {d['id']} {d['name']} | {s['attempted']} | {s['correct']} | {s['rate']:.0%} |"
                    if s else f"| Domain {d['id']} {d['name']} | 0 | 0 | - |")
            if cell not in ans:
                bad.append(f"정답률 행 불일치: Domain {d['id']}")
        bad += [f"오답 누락: {w['question_id']}" for w in p["wrong"] if w["question_id"] not in ans]
        if f"{p['study_solved']}문항" not in ans:
            bad.append("study 풀이 수 불일치")
        if resp["contexts"] != []:
            bad.append("진도 응답 contexts가 비어 있지 않음")
    return bad


def run(rows: list[dict[str, str]]) -> dict[str, Any]:
    """모든 문항을 실행하고 판정한다."""
    from fastapi.testclient import TestClient

    from src import agent as A
    from src import tools as T

    tmp = tempfile.mkdtemp(prefix="ccarf-eval-")
    os.environ["PROGRESS_DB_PATH"] = str(Path(tmp) / "eval.db")
    os.environ["WEB_FALLBACK_ENABLED"] = "false"
    T.set_seed(SEED)
    client = TestClient(A.app)

    def post(question, uid, sid):
        body = {"question": question}
        if uid:
            body["user_id"] = uid
        if sid:
            body["session_id"] = sid
        t0 = time.perf_counter()
        r = client.post("/query", json=body)
        return r, time.perf_counter() - t0

    def fill(text, sid):
        pend = T.get_pending(sid)
        if pend and "{" in text:
            q = T.question_by_id(pend["problem_id"])
            wrong = next(k for k in "ABCD" if k != q["answer"])
            text = text.replace("{correct}", q["answer"]).replace("{wrong}", wrong)
        return text

    results, times = [], []
    for row in rows:
        sid = f"eval-{row['id']}"
        note = parse_note(row["note"])
        ids = note.get("ids", "both")
        uid, ssid = (None, None) if ids == "none" else ((None, sid) if ids == "session" else (USER, sid))
        for step in [s.strip() for s in row["setup"].split("||") if s.strip()]:
            post(fill(step, sid), USER, sid)
        r, dt = post(fill(row["input"], sid), uid, ssid)
        times.append(dt)
        entry: dict[str, Any] = {"id": row["id"], "category": row["category"], "input": row["input"],
                                 "expected_traits": row["expected_traits"], "http": r.status_code}
        if r.status_code != 200:
            entry.update(passed=False, reasons=[f"HTTP {r.status_code}"], answer="", refused=False, tools=[],
                         expected_tools=[t for t in row["expected_tools"].split(";") if t])
            results.append(entry)
            continue
        resp = r.json()
        tr = resp["trace"]
        tools = sorted({c["name"] for c in tr["tool_calls"]})
        expected = sorted(t for t in row["expected_tools"].split(";") if t)
        forb = [f for f in row["forbidden"].split(";") if f and f in resp["answer"]]
        exp_w = int(note.get("db_writes", "0"))
        reasons = []
        if forb:
            reasons.append(f"forbidden 위반: {forb}")
        if tools != expected:
            reasons.append(f"도구 불일치: {tools} != {expected}")
        if tr["db_writes"] != exp_w:
            reasons.append(f"db_writes {tr['db_writes']} != {exp_w}")
        reasons += check_structure(resp, USER)
        entry.update(passed=not reasons, reasons=reasons, answer=resp["answer"],
                     refused=tr["refusal_reason"] is not None, refusal_reason=tr["refusal_reason"], tools=tools,
                     expected_tools=expected, db_writes=tr["db_writes"], expected_db_writes=exp_w,
                     forbidden_hits=forb, n_contexts=len(resp["contexts"]), sources=tr["sources"],
                     latency_s=round(dt, 3))
        results.append(entry)
    return {"results": results, "latencies": times}


def summarize(res: dict[str, Any], offline: bool) -> dict[str, Any]:
    """합격 기준 지표를 계산한다."""
    rs = res["results"]
    n = len(rs)
    tool_rows = [r for r in rs if r["expected_tools"]]
    cats: dict[str, list[bool]] = {}
    for r in rs:
        cats.setdefault(r["category"], []).append(r["passed"])
    ed_gr = [r for r in rs if r["category"] in ("edge", "guardrail")]
    rag = [r for r in rs if "search_docs" in r["tools"] and not r["refused"]]
    lat = sorted(res["latencies"])
    p95 = lat[max(0, int(round(0.95 * len(lat))) - 1)] if lat else None
    s = {
        "mode": "offline-mock (실측 아님)" if offline else "live-bedrock",
        "n": n, "passed": sum(r["passed"] for r in rs),
        "overall": sum(r["passed"] for r in rs) / n,
        "tool_accuracy": (sum(r["tools"] == r["expected_tools"] for r in tool_rows) / len(tool_rows))
        if tool_rows else None,
        "category": {c: f"{sum(v)}/{len(v)}" for c, v in cats.items()},
        "negative_guardrail_all_pass": all(r["passed"] for r in rs if r["category"] in ("negative", "guardrail")),
        "over_refusals": sum(r["refused"] for r in rs if r["category"] == "positive"),
        "edge_guardrail_forbidden_violations": sum(len(r.get("forbidden_hits", [])) for r in ed_gr),
        "db_writes_match_edge_guardrail": sum(r.get("db_writes") == r.get("expected_db_writes") for r in ed_gr)
        / len(ed_gr),
        "db_writes_match_all": sum(r.get("db_writes") == r.get("expected_db_writes") for r in rs) / n,
        "latency_p95_s": p95,
        "rag_measured": {"search_docs_answers": len(rag),
                         "with_sources": sum(bool(r["sources"]) for r in rag),
                         "avg_contexts": statistics.mean(r["n_contexts"] for r in rag) if rag else None,
                         "note": "측정값만 기록(합격선 없음); context precision/recall은 N/A(정답 문맥 라벨 없음)"},
    }
    s["meets_thresholds"] = bool(
        s["overall"] >= THRESHOLDS["overall"] and (s["tool_accuracy"] or 0) >= THRESHOLDS["tool_accuracy"]
        and s["negative_guardrail_all_pass"] and s["over_refusals"] == 0
        and s["edge_guardrail_forbidden_violations"] == 0 and s["db_writes_match_edge_guardrail"] == 1.0)
    return s


def write_reports(summary: dict[str, Any], res: dict[str, Any]) -> None:
    """report.json과 report.md를 쓴다(답변과 expected_traits를 나란히 기록)."""
    (EVAL_DIR / "report.json").write_text(
        json.dumps({"summary": summary, "results": res["results"]}, ensure_ascii=False, indent=2), encoding="utf-8")
    L = ["# 평가 리포트", "", f"- 모드: **{summary['mode']}**, 시드 {SEED}, 웹 폴백 off, 실행마다 새 임시 DB"]
    if summary["mode"].startswith("offline"):
        L.append("- ⚠️ 가짜 LLM·어휘 검색으로 돌린 하네스 점검 결과다. Bedrock 실측 합격 판정으로 쓰지 않는다.")
    L += ["", "## 요약", f"- 전체 통과: {summary['passed']}/{summary['n']} ({summary['overall']:.0%})",
          f"- 도구 호출 정확도: {summary['tool_accuracy']:.0%}",
          f"- 카테고리별 통과: {summary['category']}",
          f"- 과잉 거절(positive): {summary['over_refusals']}",
          f"- edge·guardrail forbidden 위반: {summary['edge_guardrail_forbidden_violations']}",
          f"- db_writes 일치율: edge·guardrail {summary['db_writes_match_edge_guardrail']:.0%}, "
          f"전체 {summary['db_writes_match_all']:.0%}",
          f"- 응답 시간 p95: {summary['latency_p95_s']:.2f}s (기록만)",
          f"- RAG 지표(기록만): {summary['rag_measured']}",
          f"- 합격 기준 충족: **{summary['meets_thresholds']}**", "", "## 문항별 결과",
          "| id | 범주 | 통과 | 도구 | refusal | db_writes | 사유 |", "|---|---|---|---|---|---|---|"]
    for r in res["results"]:
        L.append(f"| {r['id']} | {r['category']} | {'✅' if r['passed'] else '❌'} | {','.join(r['tools']) or '-'} | "
                 f"{r.get('refusal_reason') or '-'} | {r.get('db_writes', '-')} | {'; '.join(r['reasons']) or '-'} |")
    L += ["", "## 답변과 expected_traits (사람 확인용, 판정 제외)"]
    for r in res["results"]:
        L += [f"### {r['id']} ({r['category']})", f"- 입력: {r['input']}", f"- expected_traits: {r['expected_traits']}",
              "- 답변:", "", "```", r["answer"][:1500], "```", ""]
    (EVAL_DIR / "report.md").write_text("\n".join(L), encoding="utf-8")


def main() -> int:
    """CLI 진입점."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="Bedrock 대신 규칙 기반 mock으로 하네스 점검")
    ap.add_argument("--csv", default=str(EVAL_DIR / "test_queries.csv"))
    args = ap.parse_args()
    if not args.offline:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
        missing = [k for k in ("BEDROCK_MODEL_ID", "EMBEDDING_MODEL_ID") if not os.environ.get(k)]
        if missing:
            print(f"Bedrock 설정 없음({', '.join(missing)}): 실제 평가를 실행할 수 없습니다. "
                  ".env를 채우거나 --offline을 쓰세요.")
            return 2
    else:
        install_offline_mocks()
    res = run(load_rows(Path(args.csv)))
    summary = summarize(res, args.offline)
    write_reports(summary, res)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["meets_thresholds"] else 1


if __name__ == "__main__":
    sys.exit(main())
