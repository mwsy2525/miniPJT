"""모든 테스트의 공통 설정: 테스트는 오프라인으로 돌린다."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _offline_defaults(monkeypatch):
    """.env에서 웹 폴백이 켜져 있어도 테스트 중에는 끈다. 웹 폴백을 시험하는 테스트만 직접 켠다."""
    monkeypatch.setenv("WEB_FALLBACK_ENABLED", "false")
