"""공통 Bedrock 클라이언트: 채팅 모델(ChatBedrockConverse)과 임베딩(BedrockEmbeddings), 응답 텍스트 추출.

모델 ID와 리전은 .env에서 읽고, 호출량 제한 같은 일시 오류는 AWS SDK의 adaptive 재시도로 흡수한다.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from src.shared.config import env as _env

_RETRIES = {"mode": "adaptive", "max_attempts": 8}


@lru_cache(maxsize=4)
def _chat(model: str, region: str):
    """모델·리전별 ChatBedrockConverse를 한 번만 만들어 재사용한다(요청마다 클라이언트를 새로 만들지 않음)."""
    from botocore.config import Config
    from langchain_aws import ChatBedrockConverse

    return ChatBedrockConverse(model=model, region_name=region or None, temperature=0, config=Config(retries=_RETRIES))


def get_llm():
    """ChatBedrockConverse(temperature=0)를 돌려준다. 모델 ID와 리전은 .env에서 읽는다."""
    return _chat(_env("BEDROCK_MODEL_ID"), _env("AWS_REGION"))


def get_fast_llm():
    """짧은 변환 작업(검색어 변환)용 모델. .env FAST_MODEL_ID가 없으면 get_llm()과 같다."""
    fast = _env("FAST_MODEL_ID")
    return _chat(fast, _env("AWS_REGION")) if fast else get_llm()


@lru_cache(maxsize=2)
def _embeddings(model: str, region: str):
    """모델·리전별 BedrockEmbeddings를 한 번만 만들어 재사용한다."""
    from botocore.config import Config
    from langchain_aws import BedrockEmbeddings

    return BedrockEmbeddings(model_id=model, region_name=region or None, config=Config(retries=_RETRIES))


def get_embeddings():
    """BedrockEmbeddings를 돌려준다(LLM과 같은 자격증명, 모델 ID는 .env)."""
    return _embeddings(_env("EMBEDDING_MODEL_ID"), _env("AWS_REGION"))


def message_text(msg: Any) -> str:
    """LLM 응답 메시지에서 문자열을 꺼낸다."""
    c = getattr(msg, "content", msg)
    if isinstance(c, list):
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in c)
    return str(c)
