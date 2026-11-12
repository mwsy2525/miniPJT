"""MCP 서버: 도메인 도구 4개를 MCP 도구로 노출한다. 실행은 `python -m src.api.mcp_server`(stdio).

규칙은 POST /query·버튼 경로와 같다. 출제·채점·진도는 LLM 없이 도구 코드를 바로 부르고,
응답은 {answer, contexts, trace} 형태다. ID가 빠지면 도구 코드가 `ID 부족`으로 거절한다.
"""
from __future__ import annotations

from typing import Any, Optional

from mcp.server.mcpserver import MCPServer

from src.grading import service as grading_service
from src.orchestration import service as orchestration
from src.progress import service as progress_service
from src.quiz import service as quiz_service
from src.search import service as search_service
from src.shared.context import ToolContext

server = MCPServer("ccarf-mock-exam",
                   instructions="CCAR-F 모의시험 도구. 출제 → 채점 순서로 쓰고, 채점 전에는 정답과 해설을 주지 않는다.")


def _result(ctx: ToolContext) -> dict[str, Any]:
    """도구 실행 결과를 {answer, contexts, trace}로 조립한다."""
    return orchestration.build_response(ctx, ctx.answer, ctx.refusal_reason)


@server.tool()
def generate_quiz(user_id: str, session_id: str, domain: Optional[int] = None) -> dict[str, Any]:
    """검수된 문제 은행에서 영어 모의문제 한 문항을 출제한다. domain은 1~5 또는 생략(전체)."""
    ctx = ToolContext(user_id=user_id or None, session_id=session_id or None)
    quiz_service.run_generate_quiz(ctx, domain, "exam")
    return _result(ctx)


@server.tool()
def grade_answer(user_id: str, session_id: str, answer: str) -> dict[str, Any]:
    """세션의 대기 문제를 정답 키와 비교해 채점하고 검수 해설 세트를 돌려준다. answer는 A~D."""
    ctx = ToolContext(user_id=user_id or None, session_id=session_id or None)
    grading_service.run_grade_answer(ctx, answer)
    return _result(ctx)


@server.tool()
def get_progress(user_id: str) -> dict[str, Any]:
    """user_id 본인의 영역별 정답률과 오답 목록을 돌려준다."""
    ctx = ToolContext(user_id=user_id or None)
    progress_service.run_get_progress(ctx)
    return _result(ctx)


@server.tool()
def search_docs(question: str, user_id: Optional[str] = None, session_id: Optional[str] = None) -> dict[str, Any]:
    """Claude·Anthropic 공식 문서 근거로 한국어 답변을 만든다. 대기 문제가 있으면 정답 없는 개념 힌트로 답한다."""
    ctx = ToolContext(user_id=user_id or None, session_id=session_id or None)
    search_service.run_search_docs(ctx, question)
    return _result(ctx)


if __name__ == "__main__":
    server.run()
