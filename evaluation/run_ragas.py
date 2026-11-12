"""문서 Q&A(search_docs)의 RAGAS 지표를 잰다. 환경이 달라 두 단계로 나눠 실행한다.

1) 답변 수집(프로젝트 환경):
   uv run python evaluation/run_ragas.py collect
   → ragas_queries.csv의 질문을 search_docs로 돌려 evaluation/ragas_samples.json에 답변과 검색 구절을 저장한다.

2) 채점(격리 환경, Python 3.12. ragas가 이 프로젝트의 Python·LangChain 버전과 맞지 않아 따로 돌린다):
   uv run --no-project --python 3.12 --with ragas --with "langchain-community<0.4" --with langchain-aws \
       python evaluation/run_ragas.py score
   → evaluation/ragas_report.md와 ragas_report.json을 쓴다.

판정 모델과 임베딩은 .env의 BEDROCK_MODEL_ID, EMBEDDING_MODEL_ID를 쓴다. 웹 폴백은 끈다.
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = ROOT / "evaluation"
QUERIES = EVAL_DIR / "ragas_queries.csv"
SAMPLES = EVAL_DIR / "ragas_samples.json"
METRICS = ["context_recall", "context_precision", "faithfulness", "answer_relevancy"]


def load_env() -> None:
    """.env를 읽어 환경변수에 넣는다(이미 있는 값은 그대로 둔다). 격리 환경에서도 쓰도록 직접 읽는다."""
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*([A-Z_][A-Z0-9_]*)=(.*)", line)
        if m and m.group(1) not in os.environ:
            os.environ[m.group(1)] = m.group(2).strip()


def collect() -> int:
    """질문마다 search_docs를 실행해 답변과 검색 구절을 저장한다."""
    os.environ["WEB_FALLBACK_ENABLED"] = "false"
    os.environ["TRACE_LOG_ENABLED"] = "false"
    os.environ["LANGSMITH_TRACING"] = "false"
    sys.path.insert(0, str(ROOT))
    from src.search import service as search
    from src.shared.context import ToolContext

    rows = list(csv.DictReader(QUERIES.open(encoding="utf-8-sig")))
    out = []
    for r in rows:
        ctx = ToolContext()
        t = time.time()
        text = search.run_search_docs(ctx, r["question"])
        answer = text.split("\n\n참고 문서:")[0].strip()  # 출처 URL 목록은 답변 내용이 아니므로 뗀다
        basis = ctx.tool_calls[-1]["args"].get("answer_basis", "docs")
        out.append({"id": r["id"], "question": r["question"], "reference": r["reference"], "answer": answer,
                    "contexts": ctx.contexts, "refusal_reason": ctx.refusal_reason, "answer_basis": basis,
                    "search_query_en": ctx.tool_calls[-1]["args"].get("search_query_en"),
                    "latency_s": round(time.time() - t, 2)})
        print(r["id"], "contexts", len(ctx.contexts), "refusal", ctx.refusal_reason, basis)
    SAMPLES.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def score() -> int:
    """수집한 답변을 RAGAS로 채점해 리포트를 쓴다. 검색 구절이 있는 답변만 채점한다."""
    from langchain_aws import BedrockEmbeddings, ChatBedrockConverse
    from ragas import EvaluationDataset, evaluate
    from ragas.dataset_schema import SingleTurnSample
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import Faithfulness, LLMContextPrecisionWithReference, LLMContextRecall, ResponseRelevancy
    from ragas.run_config import RunConfig

    samples = json.loads(SAMPLES.read_text(encoding="utf-8"))
    scored = [s for s in samples if s["contexts"] and not s["refusal_reason"]]
    skipped = [s for s in samples if s not in scored]
    region = os.environ.get("AWS_REGION") or None
    judge = LangchainLLMWrapper(ChatBedrockConverse(model=os.environ["BEDROCK_MODEL_ID"], region_name=region, temperature=0))
    emb = LangchainEmbeddingsWrapper(BedrockEmbeddings(model_id=os.environ["EMBEDDING_MODEL_ID"], region_name=region))
    ds = EvaluationDataset(samples=[SingleTurnSample(user_input=s["question"], response=s["answer"],
                                                     retrieved_contexts=s["contexts"], reference=s["reference"])
                                    for s in scored])
    metrics = [LLMContextRecall(), LLMContextPrecisionWithReference(), Faithfulness(), ResponseRelevancy()]
    result = evaluate(ds, metrics=metrics, llm=judge, embeddings=emb,
                      run_config=RunConfig(max_workers=2, timeout=240, max_retries=6), show_progress=False)
    df = result.to_pandas()
    cols = {"context_recall": "context_recall", "llm_context_precision_with_reference": "context_precision",
            "faithfulness": "faithfulness", "answer_relevancy": "answer_relevancy"}
    rows = []
    for s, (_, r) in zip(scored, df.iterrows()):
        rows.append({"id": s["id"], **{name: (None if r[c] != r[c] else round(float(r[c]), 3)) for c, name in cols.items()}})
    means = {}
    for name in METRICS:
        vals = [r[name] for r in rows if r[name] is not None]
        means[name] = {"mean": round(sum(vals) / len(vals), 3) if vals else None, "n": len(vals)}
    summary = {"judge_model": os.environ["BEDROCK_MODEL_ID"], "embedding_model": os.environ["EMBEDDING_MODEL_ID"],
               "n_questions": len(samples), "n_scored": len(scored), "means": means,
               "skipped": [{"id": s["id"], "refusal_reason": s["refusal_reason"], "answer_basis": s["answer_basis"]}
                           for s in skipped]}
    (EVAL_DIR / "ragas_report.json").write_text(
        json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")

    def fmt(v):
        return "-" if v is None else f"{v:.2f}"

    L = ["# RAGAS 평가 리포트 (문서 Q&A · search_docs)", "",
         f"- 실행 시각: {time.strftime('%Y-%m-%d %H:%M')}, 웹 폴백 off",
         f"- 답변·판정 모델: {summary['judge_model']} (답변을 만든 모델과 판정 모델이 같다), 임베딩: {summary['embedding_model']}",
         f"- 질문 {len(samples)}개 중 {len(scored)}개 채점. 검색 구절 없이 끝난 답변(거절, AI 일반 지식 답변)은 채점에서 뺐다.",
         "- 정답(reference)은 스냅샷 구절을 바탕으로 AI가 쓴 것이고 사람이 검수하지 않았다. 질문은 문제 은행 문항과 같은 문서 구절에서 골랐다.",
         "", "## 평균"]
    L += [f"- {name}: {fmt(means[name]['mean'])} (n={means[name]['n']})" for name in METRICS]
    L += ["", "## 질문별 점수", "| id | context_recall | context_precision | faithfulness | answer_relevancy | 질문 |",
          "|---|---|---|---|---|---|"]
    by_id = {s["id"]: s for s in samples}
    for r in rows:
        L.append(f"| {r['id']} | " + " | ".join(fmt(r[m]) for m in METRICS) + f" | {by_id[r['id']]['question']} |")
    if skipped:
        L += ["", "## 채점에서 뺀 질문", "| id | 사유 | 질문 |", "|---|---|---|"]
        L += [f"| {s['id']} | {s['refusal_reason'] or s['answer_basis']} | {s['question']} |" for s in skipped]
    (EVAL_DIR / "ragas_report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    load_env()
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    sys.exit(collect() if mode == "collect" else score() if mode == "score" else
             (print("사용법: run_ragas.py collect | score") or 2))
