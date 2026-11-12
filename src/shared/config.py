"""공통 설정: 프로젝트 루트 경로, .env 로드, 환경변수 읽기."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


def env(name: str, default: str = "") -> str:
    """환경변수를 읽는다(없으면 기본값)."""
    return os.environ.get(name, default)
