"""도메인 도구 4개 진입점(제출 규약). 구현은 도메인 폴더에 있다.

- search_docs → src/search, generate_quiz → src/quiz, grade_answer → src/grading, get_progress → src/progress
- ID 조합별 도구 목록(build_tools) → src/orchestration, 데이터 읽기 → src/question_bank·src/sessions

이 모듈은 이름을 다시 내보내기만 한다. 테스트에서 함수를 바꿔 끼울 때는 실제 정의 모듈을 대상으로 한다.
"""
from src.grading.service import build_grading_answer, normalize_answer, run_grade_answer
from src.orchestration.schemas import TOOLLESS_REFUSALS
from src.orchestration.service import TOOL_NAMES, allowed_tool_names, build_tools
from src.progress.service import compute_progress, run_get_progress
from src.question_bank.service import load_domains, load_questions, question_by_id
from src.quiz.service import _eligible, run_generate_quiz, set_seed
from src.retrieval import service as retriever
from src.search.prompts import GENERAL_LABEL
from src.search.schemas import GeneralAnswer, SearchAnswer
from src.search.service import (detect_domain, is_pre_grading_leak_request, llm_answer, llm_general_answer,
                                llm_rewrite_query, missing_identifier_terms, run_search_docs)
from src.sessions.service import db_path, get_pending, latest_attempt, record_grading, set_pending
from src.shared.context import REFUSAL_CODES, ToolContext
from src.shared.llm import get_llm
