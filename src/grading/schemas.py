"""채점 도구 입력 스키마."""
from __future__ import annotations

from pydantic import BaseModel, Field


class GradeToolInput(BaseModel):
    answer: str = Field(description="사용자가 고른 보기. 'B인 것 같아'면 'B'만 넣는다. 복수 선택·E·숫자는 입력 그대로 넣는다")
