"""search_docs의 LLM 구조화 출력 스키마와 도구 입력 스키마."""
from __future__ import annotations

from pydantic import BaseModel, Field


class SearchAnswer(BaseModel):
    """search_docs 답변 생성용 구조화 출력."""

    answer: str = Field(description="한국어 답변(핵심 용어·API 이름은 영어 유지)")
    insufficient: bool = Field(False, description="제공된 구절이 질문에 답하지 못하거나, 질문 속 기능·용어가 구절에 없으면 true")
    out_of_scope_topic: bool = Field(False, description="질문이 시험 출제 범위 밖 주제 목록에 해당하면 true")


class GeneralAnswer(BaseModel):
    """공식 문서 근거가 없을 때 AI 일반 지식 답변용 구조화 출력."""

    answer: str = Field("", description="한국어 답변(핵심 용어·API 이름은 영어 유지)")
    unknown: bool = Field(False, description="확실히 알지 못하거나 질문 속 기능·용어가 실제로 있는지 모르면 true")


class SearchToolInput(BaseModel):
    question: str = Field(description="사용자의 학습 질문 원문(한국어 가능)")
