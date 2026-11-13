"""개인정보(PII) 가림: 모델에 보내는 입력과 요청 트레이스에 이메일·카드 번호·휴대전화 번호를 남기지 않는다."""
from __future__ import annotations

import re

# 에이전트 미들웨어(PIIMiddleware)와 트레이스 기록이 같은 규칙을 쓴다.
KR_PHONE = r"(?<!\d)01[016789][-\s.]?\d{3,4}[-\s.]?\d{4}(?!\d)"
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
_PHONE = re.compile(KR_PHONE)


def mask(text: str | None) -> str | None:
    """문자열 속 이메일·카드 번호·휴대전화 번호를 [REDACTED_*]로 바꾼다."""
    if not text:
        return text
    text = _EMAIL.sub("[REDACTED_EMAIL]", text)
    text = _PHONE.sub("[REDACTED_PHONE]", text)
    return _CARD.sub("[REDACTED_CREDIT_CARD]", text)


def agent_middleware() -> list:
    """모델에 보내는 사용자 입력에서 개인정보를 가리는 미들웨어 목록."""
    from langchain.agents.middleware import PIIMiddleware

    return [
        PIIMiddleware("email", strategy="redact"),
        PIIMiddleware("credit_card", strategy="mask"),
        PIIMiddleware("kr_phone", detector=KR_PHONE, strategy="redact"),
    ]
