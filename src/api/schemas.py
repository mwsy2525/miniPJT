"""POST /query·/quiz·/grade·/progress 요청 스키마와 고정 응답 스키마(answer, contexts, trace)."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator


class QueryRequest(BaseModel):
    """요청 본문. question은 필수이며 비어 있거나 2000자를 넘거나 문자열이 아니면 422."""

    model_config = ConfigDict(extra="ignore")
    question: StrictStr = Field(max_length=2000)
    user_id: Optional[StrictStr] = None
    session_id: Optional[StrictStr] = None

    @field_validator("question")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        """공백뿐인 question을 거부한다."""
        if not v.strip():
            raise ValueError("question must not be empty")
        return v

    @field_validator("user_id", "session_id")
    @classmethod
    def _blank_to_none(cls, v: Optional[str]) -> Optional[str]:
        """빈 ID 문자열은 ID 없음으로 본다."""
        return v.strip() or None if v is not None else None


class Source(BaseModel):
    """근거 출처."""

    url: Optional[str] = None
    doc_path: Optional[str] = None
    chunk_id: Optional[str] = None


class ToolCall(BaseModel):
    """호출된 도구 이름과 인자."""

    name: str
    args: dict[str, Any]


class Grading(BaseModel):
    """채점 요약."""

    chosen: str
    correct: str


class Trace(BaseModel):
    """고정 trace 스키마."""

    tool_calls: list[ToolCall]
    problem_id: Optional[str]
    grading: Optional[Grading]
    refusal_reason: Optional[str]
    db_writes: int
    sources: list[Source]


class QueryResponse(BaseModel):
    """고정 응답 스키마(answer, contexts, trace)."""

    answer: str
    contexts: list[str]
    trace: Trace


class QuizRequest(BaseModel):
    """화면 버튼용 출제 요청. domain은 1~5 또는 null, exclude는 이번 세트에서 이미 낸 문항 ID."""

    user_id: StrictStr = Field(min_length=1)
    session_id: StrictStr = Field(min_length=1)
    domain: Optional[int] = Field(None, ge=1, le=5)
    exclude: list[StrictStr] = Field(default_factory=list, max_length=200)


class GradeRequest(BaseModel):
    """화면 버튼용 채점 요청."""

    user_id: StrictStr = Field(min_length=1)
    session_id: StrictStr = Field(min_length=1)
    answer: StrictStr = Field(max_length=20)


class ProgressRequest(BaseModel):
    """화면 버튼용 진도 요청."""

    user_id: StrictStr = Field(min_length=1)
