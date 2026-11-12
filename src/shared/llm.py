"""공통 Bedrock 클라이언트: 채팅 모델(ChatBedrockConverse)과 임베딩(BedrockEmbeddings), 응답 텍스트 추출.

모델 ID와 리전은 .env에서 읽고, 호출량 제한 같은 일시 오류는 AWS SDK의 adaptive 재시도로 흡수한다.
"""
from __future__ import annotations

from typing import Any

from src.shared.config import env as _env

_RETRIES = {"mode": "adaptive", "max_attempts": 8}


def get_llm():
    """ChatBedrockConverse(temperature=0)를 만든다. 모델 ID와 리전은 .env에서 읽는다."""
    from botocore.config import Config
    from langchain_aws import ChatBedrockConverse

    return ChatBedrockConverse(model=_env("BEDROCK_MODEL_ID"), region_name=_env("AWS_REGION") or None,
                               temperature=0, config=Config(retries=_RETRIES))


def get_embeddings():
    """BedrockEmbeddings를 만든다(LLM과 같은 자격증명, 모델 ID는 .env)."""
    from botocore.config import Config
    from langchain_aws import BedrockEmbeddings

    return BedrockEmbeddings(model_id=_env("EMBEDDING_MODEL_ID"), region_name=_env("AWS_REGION") or None,
                             config=Config(retries=_RETRIES))


def message_text(msg: Any) -> str:
    """LLM 응답 메시지에서 문자열을 꺼낸다."""
    c = getattr(msg, "content", msg)
    if isinstance(c, list):
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in c)
    return str(c)
