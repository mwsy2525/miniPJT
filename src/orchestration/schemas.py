"""도구 없이 답할 때 LLM이 내는 구조화 출력 스키마."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

# 도구 없이 LLM이 구조화 출력으로 고를 수 있는 사유(코드가 한 번 더 검증한다)
TOOLLESS_REFUSALS = ["ID 부족", "범위 밖 질문", "유출 문제 요청", "채점 전 해설 요청", "타인 진도 요청"]


class AgentReply(BaseModel):
    """도구 없이 답할 때 LLM이 내는 구조화 출력(거절이면 사유 코드를 고른다)."""

    answer: str = Field(description="사용자에게 보여 줄 한국어 답변(거절이면 이유와 다음 행동 안내)")
    refusal_reason: Optional[str] = Field(
        None, description="거절이면 다음 중 하나, 아니면 null: " + ", ".join(TOOLLESS_REFUSALS))
