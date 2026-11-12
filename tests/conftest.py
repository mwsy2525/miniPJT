"""모든 테스트의 공통 설정: 테스트는 오프라인으로 돌린다."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _offline_defaults(monkeypatch, tmp_path):
    """.env에서 웹 폴백이 켜져 있어도 테스트 중에는 끈다. 웹 폴백을 시험하는 테스트만 직접 켠다."""
    monkeypatch.setenv("WEB_FALLBACK_ENABLED", "false")
    monkeypatch.setenv("TRACE_LOG_DIR", str(tmp_path / "traces"))  # 테스트 트레이스는 임시 폴더에만 남긴다
    monkeypatch.setenv("LANGSMITH_TRACING", "false")  # 테스트는 LangSmith로 아무것도 보내지 않는다
    monkeypatch.delenv("FAST_MODEL_ID", raising=False)  # 빠른 모델도 get_llm mock을 거치게 한다
