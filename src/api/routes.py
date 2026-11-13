"""FastAPI 앱과 경로. POST /query는 에이전트를, 화면 버튼 경로는 LLM 없이 도구 코드를 바로 부른다."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from src.api.schemas import GradeRequest, ProgressRequest, QueryRequest, QueryResponse, QuizRequest
from src.grading import service as grading_service
from src.orchestration import service as orchestration
from src.progress import service as progress_service
from src.quiz import service as quiz_service
from src.shared.config import ROOT, env
from src.shared.context import ToolContext

app = FastAPI(title="CCAR-F 모의시험 Agent")
WEB_INDEX = ROOT / "web" / "index.html"


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """시험지형 웹 화면(web/index.html)을 돌려준다. 버튼은 /quiz·/grade·/progress, 질문 창은 /query를 호출한다."""
    return FileResponse(WEB_INDEX, media_type="text/html")


@app.post("/query", response_model=QueryResponse)
def query(req: QueryRequest) -> dict[str, Any]:
    """POST /query: question(필수), user_id?, session_id? → {answer, contexts, trace}.

    모델 설정(.env BEDROCK_MODEL_ID)이 없으면 Bedrock을 부르지 않고 503으로 원인을 알린다(버튼 경로는 그대로 동작).
    """
    if not env("BEDROCK_MODEL_ID").strip():
        raise HTTPException(status_code=503, detail="ai_not_configured: BEDROCK_MODEL_ID와 AWS 자격증명(.env)이 없어 AI 질문을 처리할 수 없습니다.")
    try:
        return orchestration.run_query(req.question, req.user_id, req.session_id)
    except HTTPException:
        raise
    except Exception as exc:  # Bedrock·임베딩 오류 등
        raise HTTPException(status_code=502, detail=f"upstream error: {type(exc).__name__}") from exc


# 화면 버튼 전용 경로. 의도가 정해진 동작이라 LLM을 거치지 않고 도구 코드를 바로 부른다.
# 응답 형태와 규칙(검수 문항만 출제, 결정적 채점, 본인 진도만)은 POST /query와 같다.
@app.post("/quiz", response_model=QueryResponse)
def quiz(req: QuizRequest) -> dict[str, Any]:
    """POST /quiz: LLM 없이 generate_quiz를 실행한다."""
    ctx = ToolContext(user_id=req.user_id, session_id=req.session_id)
    quiz_service.run_generate_quiz(ctx, req.domain, "exam", req.exclude)
    return orchestration.build_response(ctx, ctx.answer, ctx.refusal_reason)


@app.post("/grade", response_model=QueryResponse)
def grade(req: GradeRequest) -> dict[str, Any]:
    """POST /grade: LLM 없이 grade_answer를 실행한다."""
    ctx = ToolContext(user_id=req.user_id, session_id=req.session_id)
    grading_service.run_grade_answer(ctx, req.answer)
    return orchestration.build_response(ctx, ctx.answer, ctx.refusal_reason)


@app.post("/progress", response_model=QueryResponse)
def progress(req: ProgressRequest) -> dict[str, Any]:
    """POST /progress: LLM 없이 get_progress를 실행한다."""
    ctx = ToolContext(user_id=req.user_id)
    progress_service.run_get_progress(ctx)
    return orchestration.build_response(ctx, ctx.answer, ctx.refusal_reason)
