"""문제 은행과 출제 영역 데이터를 읽는다."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from src.shared.config import ROOT, env as _env


def questions_path() -> Path:
    """문제 은행 경로."""
    return Path(_env("QUESTIONS_PATH", str(ROOT / "data" / "questions.json")))


def load_questions() -> list[dict[str, Any]]:
    """문제 은행 전체를 읽는다(매 호출 새로 읽어 수정이 바로 반영된다)."""
    return json.loads(questions_path().read_text(encoding="utf-8"))


def question_by_id(qid: str) -> dict[str, Any] | None:
    """id로 문항을 찾는다."""
    return next((q for q in load_questions() if q["id"] == qid), None)


@lru_cache(maxsize=1)
def load_domains() -> dict[str, Any]:
    """data/exam_domains.yaml을 읽는다."""
    return yaml.safe_load((ROOT / "data" / "exam_domains.yaml").read_text(encoding="utf-8"))


def domain_name(domain_id: int) -> str:
    """영역 번호의 이름."""
    for d in load_domains()["domains"]:
        if d["id"] == domain_id:
            return d["name"]
    return f"Domain {domain_id}"


def valid_task_statements() -> set[str]:
    """exam_domains.yaml에 있는 Task Statement 번호 집합."""
    return {str(k) for d in load_domains()["domains"] for k in d.get("task_statements", {})}
