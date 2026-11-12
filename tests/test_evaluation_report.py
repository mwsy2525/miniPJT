"""평가셋 형식과 격리된 평가 실행(오프라인 mock)의 합격 기준 테스트."""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evaluation"))

import run_eval as R  # noqa: E402


def test_csv_shape():
    """20문항, 범주 분포, 컬럼이 규약과 같다."""
    rows = R.load_rows(ROOT / "evaluation" / "test_queries.csv")
    assert list(rows[0]) == ["id", "category", "input", "expected_traits", "forbidden", "expected_tools", "note", "setup"]
    assert Counter(r["category"] for r in rows) == {"positive": 8, "negative": 3, "edge": 4, "guardrail": 5}
    tools = {t for r in rows for t in r["expected_tools"].split(";") if t}
    assert tools <= {"search_docs", "generate_quiz", "grade_answer", "get_progress"}


def test_offline_eval_meets_thresholds(tmp_path, monkeypatch):
    """mock 평가 실행이 합격 기준을 충족하고 리포트를 남긴다(평가 DB는 임시 경로)."""
    from src import agent, retriever, tools
    for mod, name in ((agent, "get_agent_llm"), (tools, "llm_rewrite_query"), (tools, "llm_answer"),
                      (retriever, "search"), (retriever, "similarity_threshold")):
        monkeypatch.setattr(mod, name, getattr(mod, name))  # 테스트 종료 시 원복
    monkeypatch.setattr(R, "EVAL_DIR", tmp_path)
    R.install_offline_mocks()
    res = R.run(R.load_rows(ROOT / "evaluation" / "test_queries.csv"))
    s = R.summarize(res, True)
    R.write_reports(s, res)
    assert s["meets_thresholds"], [r for r in res["results"] if not r["passed"]]
    assert s["over_refusals"] == 0 and (tmp_path / "report.md").exists()
