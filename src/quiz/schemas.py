"""출제 도구 입력 스키마."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class QuizToolInput(BaseModel):
    domain: Optional[str] = Field(None, description="출제 영역 번호(1~5) 또는 이름. 지정이 없으면 null")
    scope: str = Field("exam", description="기본 exam. 사용자가 학습용 문제를 요청할 때만 study")
