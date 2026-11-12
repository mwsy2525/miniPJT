"""요청 하나를 처리한다: 채점 전 사전 거절 → create_agent로 도구 선택 → {answer, contexts, trace} 조립."""
from __future__ import annotations

import re
from typing import Any, Optional

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool

from src.grading import service as grading
from src.grading.schemas import GradeToolInput
from src.orchestration import prompts as P
from src.orchestration.schemas import TOOLLESS_REFUSALS, AgentReply
from src.progress import service as progress
from src.progress.schemas import ProgressToolInput
from src.quiz import service as quiz
from src.quiz.schemas import QuizToolInput
from src.search import service as search
from src.search.schemas import SearchToolInput
from src.sessions import service as sessions
from src.shared import llm
from src.shared.context import REFUSAL_CODES, ToolContext

TOOL_NAMES = ("search_docs", "generate_quiz", "grade_answer", "get_progress")


def allowed_tool_names(user_id: str | None, session_id: str | None) -> list[str]:
    """ID 조합별 허용 도구. ID 없음·session_id만은 RAG만, user_id만은 RAG+진도, 둘 다는 전부."""
    if user_id and session_id:
        return list(TOOL_NAMES)
    if user_id:
        return ["search_docs", "get_progress"]
    return ["search_docs"]


def build_tools(ctx: ToolContext) -> list[StructuredTool]:
    """ID 조합에 허용된 도구만 LangChain 도구로 만든다. 허용되지 않은 도구는 아예 만들지 않는다.

    도구는 return_direct=True라 결과(코드가 만든 응답)가 곧 최종 응답의 answer가 된다.
    """
    def busy(fn):
        """한 요청에서 도구는 한 번만 실행한다(병렬 호출 방지)."""
        def wrapper(*a, **k):
            if ctx.answer is not None:
                return ctx.answer
            return fn(*a, **k)
        return wrapper


    specs = {
        "search_docs": (busy(lambda question: search.run_search_docs(ctx, question)), SearchToolInput),
        "generate_quiz": (busy(lambda domain=None, scope="exam": quiz.run_generate_quiz(ctx, domain, scope)),
                          QuizToolInput),
        "grade_answer": (busy(lambda answer: grading.run_grade_answer(ctx, answer)), GradeToolInput),
        "get_progress": (busy(lambda: progress.run_get_progress(ctx)), ProgressToolInput),
    }
    tools = []
    for name in allowed_tool_names(ctx.user_id, ctx.session_id):
        fn, schema = specs[name]
        tools.append(StructuredTool.from_function(func=fn, name=name, description=P.TOOL_DESCRIPTIONS[name],
                                                  args_schema=schema, return_direct=True))
    return tools


def get_agent_llm():
    """에이전트용 LLM(테스트에서 대체 가능)."""
    return llm.get_llm()


def _validated_reason(ctx: ToolContext, reason: Optional[str]) -> Optional[str]:
    """도구 없는 거절의 사유 코드를 목록과 실제 상태로 다시 확인한다. 통과하지 못하면 None."""
    if reason not in TOOLLESS_REFUSALS:
        return None
    if reason == "ID 부족" and ctx.both_ids:
        return None
    if reason == "채점 전 해설 요청":
        pend = sessions.get_pending(ctx.session_id) if ctx.both_ids else None
        if not pend:
            return None
    return reason


_QUESTION_FORM = re.compile(r"[?？]|왜|뭐|무엇|알려|설명|번역|해석|why|what|which|translat|explain", re.I)


def _pre_grading_guard(ctx: ToolContext, question: str) -> bool:
    """대기 문제가 있는데 그 문제의 정답·보기 정오·번역을 묻는 질문인지 코드로 먼저 확인한다.

    'B가 맞는 것 같아'처럼 답안일 수 있는 입력은 질문 형태가 아니므로 걸러내지 않고 에이전트에 넘긴다.
    """
    if not ctx.both_ids:
        return False
    pend = sessions.get_pending(ctx.session_id)
    return bool(pend and pend["user_id"] == ctx.user_id
                and _QUESTION_FORM.search(question) and search.is_pre_grading_leak_request(question))


def run_query(question: str, user_id: Optional[str], session_id: Optional[str]) -> dict[str, Any]:
    """에이전트를 실행하고 {answer, contexts, trace}를 조립한다."""
    ctx = ToolContext(user_id=user_id, session_id=session_id)
    if _pre_grading_guard(ctx, question):  # LLM과 도구를 거치지 않고 바로 거절한다
        return build_response(ctx, P.REFUSAL_TEXT["채점 전 해설 요청"], "채점 전 해설 요청")
    pending = bool(sessions.get_pending(session_id)) if ctx.both_ids else False
    last = sessions.latest_attempt(session_id) if ctx.both_ids else None
    graded = bool(last and last.get("user_id") == user_id)
    status = (f"[요청 상태] user_id {'있음' if user_id else '없음'}, session_id {'있음' if session_id else '없음'}, "
              f"대기 문제 {'있음' if pending else '없음'}, 이 세션에서 방금 채점한 문항 {'있음' if graded else '없음'}")
    agent = create_agent(
        get_agent_llm(), build_tools(ctx),
        system_prompt=P.SYSTEM_PROMPT + "\n" + status,
        response_format=ToolStrategy(AgentReply),
    )
    out = agent.invoke({"messages": [HumanMessage(content=question)]})

    if ctx.answer is not None:
        answer, refusal = ctx.answer, ctx.refusal_reason
    else:
        reply = out.get("structured_response")
        if reply is not None:
            answer, refusal = reply.answer, _validated_reason(ctx, reply.refusal_reason)
            if refusal in P.REFUSAL_TEXT:
                answer = P.REFUSAL_TEXT[refusal]
        else:
            last = out["messages"][-1]
            answer, refusal = llm.message_text(last), None
    return build_response(ctx, answer, refusal)


def build_response(ctx: ToolContext, answer: Optional[str], refusal: Optional[str]) -> dict[str, Any]:
    """도구 실행 결과를 {answer, contexts, trace} 형태로 조립한다."""
    return {
        "answer": answer or "",
        "contexts": [str(c) for c in (ctx.contexts or [])],
        "trace": {
            "tool_calls": ctx.tool_calls,
            "problem_id": ctx.problem_id,
            "grading": ctx.grading,
            "refusal_reason": refusal if refusal in REFUSAL_CODES else None,
            "db_writes": ctx.db_writes,
            "sources": ctx.sources,
        },
    }
