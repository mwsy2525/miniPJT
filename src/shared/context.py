"""한 요청의 ID와 도구 실행 결과를 모으는 ToolContext, 고정 거절 사유 코드 10개."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

REFUSAL_CODES = [
    "ID 부족", "user_id 불일치", "범위 밖 질문", "근거 부족", "유출 문제 요청",
    "유효하지 않은 답안", "채점 전 해설 요청", "타인 진도 요청", "대기 문제 없음", "출제 문항 없음",
]


@dataclass
class ToolContext:
    """한 요청의 ID와 도구 실행 결과(응답 조립용)를 모은다."""

    user_id: Optional[str] = None
    session_id: Optional[str] = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    answer: Optional[str] = None
    contexts: list[str] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    problem_id: Optional[str] = None
    grading: Optional[dict[str, str]] = None
    refusal_reason: Optional[str] = None
    db_writes: int = 0

    @property
    def both_ids(self) -> bool:
        """user_id와 session_id가 모두 있는가."""
        return bool(self.user_id and self.session_id)

    def record_call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """tool_calls에 호출을 남기고 args 딕셔너리를 돌려준다(이후 보강 가능)."""
        args = dict(args)
        self.tool_calls.append({"name": name, "args": args})
        return args

    def finish(self, answer: str, refusal: str | None = None) -> str:
        """도구 결과를 확정한다(사유 코드는 목록 안의 값만 인정)."""
        self.answer = answer
        self.refusal_reason = refusal if refusal in REFUSAL_CODES else None
        return answer
