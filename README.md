# 미니 PJT: CCAR-F 모의시험 Agent

## 무엇을 푸나
영어로 출제되는 CCAR-F(Claude Certified Architect – Foundations) 시험을 준비하는 한국어 사용자가, 공식 문서 근거가 붙은 모의문제를 한 문항씩 풀고 한국어 해설과 함께 바로 채점받을 방법이 없다는 문제를 푼다.

## 활용한 패턴 (Day 1~7)
- Day 1: LCEL chain (Pydantic 구조화 출력) — 문서 Q&A 답변을 `ChatPromptTemplate | 모델.with_structured_output(SearchAnswer)` 체인으로 만든다. 근거 부족 여부와 시험 범위 밖 여부를 구조화 필드로 받는다.
- Day 2: RAG — 공식 문서 스냅샷 36개를 제목 단위로 나눠 Chroma에 넣고, 한국어 질문을 영어 검색어로 바꿔 검색한다. 영역 필터와 유사도 기준값이 있고, 로컬 근거가 없으면 공식 사이트의 문서 목록(`llms.txt`)에서 페이지를 찾아 읽는다. 하이브리드 검색과 리랭킹은 쓰지 않았다.
- Day 3: ReAct (도구 자율 선택) — `create_agent`에 도구 4개(`search_docs`, `generate_quiz`, `grade_answer`, `get_progress`)를 붙여 모델이 고른다. ID 조합에 허용되지 않은 도구는 아예 넘기지 않는다.
- Day 4: MCP 서버 연동 — 도구 4개를 MCP 서버(`src/api/mcp_server.py`, stdio)로 노출한다. 규칙과 응답 형태는 HTTP 경로와 같다.
- Day 5: 가드레일 — 프롬프트 인젝션, 실제 시험 유출 문제 요청, 채점 전 정답·번역 요청, 타인 진도 요청을 고정 사유 코드 10개 중 하나로 거절한다. 채점 전 정답 요청은 LLM을 부르기 전에 코드가 먼저 막는다.
- Day 5: 미들웨어 — 모델 호출 재시도, 한 요청의 모델 호출 4회·도구 호출 2회 상한을 `create_agent` 미들웨어로 건다.
- Day 7: Observability · Trace — 요청마다 LLM·도구 호출 단계(시간, 모델, 토큰 수)를 `data/traces/`에 JSONL로 남긴다. `.env`에 LangSmith 설정을 넣으면 LangSmith에도 기록된다.
- Day 7: 평가 — 인-아웃 세트 20문항으로 통과율, 도구 호출 정확도, 거절 정확도, DB 쓰기 일치율을 자동 판정하고 라운드별 리포트를 남긴다. 문서 Q&A는 RAGAS 네 지표를 따로 쟀다(`evaluation/run_ragas.py`).

부분만 적용한 것: 도구 다중(도구는 4개지만 한 요청에 하나만 실행), 장기 메모리(시도 기록을 SQLite에 영구 저장, LangGraph Store는 쓰지 않음). 적용하지 않은 것: HITL, Multi-Agent Supervisor.

## 아키텍처
```
브라우저(web/index.html)          MCP 클라이언트
   │ 버튼: /quiz /grade /progress      │ stdio
   │ 질문 창: /query                    │
   ▼                                    ▼
src/api (FastAPI routes, MCP server)
   │
   ├─ /query ─▶ src/orchestration ── 채점 전 사전 거절(코드) ─▶ create_agent(도구 선택, 미들웨어)
   │                                                              │
   └─ 버튼 경로 · MCP: LLM 없이 도구 코드를 바로 호출 ─────────────┤
                                                                  ▼
        quiz(출제)   grading(채점·해설)   progress(진도)   search(문서 Q&A · 힌트 · 후속 질문)
           │              │                   │                 │  LLM은 여기서만 호출
           ▼              ▼                   ▼                 ▼
     question_bank    sessions(SQLite)    sessions       retrieval(Chroma · 웹 조회)
     (검수 문항 43개)  대기 문제·시도 기록                 공식 문서 스냅샷 36개
```
- 채점은 정답 키 비교, 해설은 검수된 문제 은행의 필드를 템플릿에 채운 것이라 LLM을 거치지 않는다.
- LLM은 `search_docs` 안의 네 곳(검색어 변환, 채점 전 개념 힌트, 채점 후 후속 질문, 문서 근거가 없을 때의 AI 일반 지식 답변)과 도구 선택에만 쓴다.
- 응답은 항상 `{answer, contexts, trace}`이고, `trace`에 호출한 도구, 채점 결과, 거절 사유, DB 쓰기 횟수, 근거 출처가 들어간다.
- 무엇을 왜 만드는지는 `SERVICE.md`, 어떻게 만드는지는 `CLAUDE.md`에 있다.

## 실행 방법
```bash
# 1. 의존성 설치 (둘 중 하나)
uv sync
pip install -r requirements.txt

# 2. 환경 변수: .env.example을 .env로 복사하고 AWS 자격증명, BEDROCK_MODEL_ID, EMBEDDING_MODEL_ID를 채운다
cp .env.example .env

# 3. 서버 실행 → http://127.0.0.1:8000/ 에서 시험 화면
uv run uvicorn src.agent:app --host 127.0.0.1 --port 8000

# 4. API 호출 예
curl -X POST http://127.0.0.1:8000/query -H "Content-Type: application/json" \
  -d '{"question": "문제 내줘", "user_id": "u1", "session_id": "s1"}'

# 5. 테스트(오프라인, Bedrock 불필요)와 자체 평가(Bedrock 필요)
uv run pytest -q
uv run python evaluation/run_eval.py          # evaluation/history/run{NN}_report.md 생성 (--as-round2: round2_report.md도 갱신)
uv run python evaluation/run_eval.py --offline # 가짜 모델로 평가 스크립트만 점검

# 6. MCP 서버(stdio)
uv run python -m src.api.mcp_server

# 7. Docker (GitHub Actions에서 빌드와 컨테이너 기동을 확인: .github/workflows/docker-build.yml)
docker build -t ccarf-mock-exam .
docker run --rm -p 8000:8000 --env-file .env ccarf-mock-exam
```
- 처음 문서 검색을 할 때 `data/chroma/`에 인덱스를 만든다(Bedrock 임베딩 호출).
- 개발에 쓴 모델은 Bedrock의 Claude Sonnet 4.6(답변), Claude Haiku 4.5(검색어 변환), Titan Text Embeddings V2다.

## RAGAS 평가 결과
문서 Q&A(`search_docs`)용 질문 15개(`evaluation/ragas_queries.csv`)로 쟀다. 15개 중 12개가 문서 근거로 답했고, 아래는 그 12개의 평균이다(`evaluation/ragas_report.md`).
- context_recall: 1.00
- context_precision: 0.76
- faithfulness: 0.96
- answer_relevancy: 0.72

읽을 때 주의할 점:
- 나머지 3개(R05, R13, R15)는 검색이 맞는 구절을 찾지 못해 AI 일반 지식 답변으로 넘어갔고, 채점에서 빠졌다. 이 3개를 검색 실패로 치면 문서 근거로 답한 비율은 12/15(80%)다. context_recall 1.00은 검색에 성공한 질문만의 값이다.
- 정답(reference)은 스냅샷 구절을 보고 AI가 썼고 사람이 검수하지 않았다. 질문은 문제 은행 문항과 같은 문서 구절에서 골랐다.
- 답변을 만든 모델과 판정 모델이 같다(Claude Sonnet 4.6).
- 출제, 채점, 진도는 LLM과 검색을 거치지 않아 RAGAS 대상이 아니다.
- 실행 방법은 `evaluation/run_ragas.py` 머리말에 있다. ragas가 이 프로젝트의 Python 3.14와 맞지 않아 채점 단계는 Python 3.12 격리 환경에서 돌린다.

## 인-아웃 세트 통과율 (자체 평가)
- 1차 (첫 실측, `evaluation/round1_report.md`): 18 / 20 통과
- 2차 (개선 후 최신 실측, `evaluation/round2_report.md`): 20 / 20 통과
- 개선폭: +2 건 (주요 개선 사항: 채점 후 후속 질문이 도구를 부르지 않던 문제, 프롬프트 인젝션에 거절 사유 코드를 남기지 않던 문제)

실측은 모두 14번 돌렸다. 1차는 1회차, 2차는 14회차 결과이고, 모든 회차의 리포트는 `evaluation/history/run{NN}_report.md`에 있다. 1~4회차는 Claude Sonnet 4.5, 7회차부터는 Sonnet 4.6이다. 5·6회차는 Bedrock 일일 토큰 한도로 HTTP 502가 섞여 결과가 유효하지 않고, 8회차는 원본 리포트를 잃어 요약만 남겼다. 1차와 2차 사이에 문제 은행이 10문항에서 43문항으로 늘었고 모델도 바뀌었으므로, 두 수치는 같은 조건의 비교가 아니다.

합격 기준(SERVICE.md 5번)과 14회차 결과: 전체 통과 100%(기준 85%), 도구 호출 정확도 100%(90%), negative·guardrail 8/8(100%), 정상 질문 거절 0건(0건), edge 금지어 위반 0건, DB 쓰기 횟수 일치 100%.

## 트라이앤에러 회고
- 시도했지만 실패한 접근 · 왜 실패했는가
  - 거절 안내문을 LLM이 쓰게 함: 거절하면서 "정답은"이라는 금지 문구를 쓰는 회차가 나왔다(4회차).
  - 프롬프트 규칙만으로 "문서에 없는 플래그는 근거 부족으로 거절"시키기: Sonnet 4.6은 "그런 플래그는 없다"고 단정해 답했고, 규칙을 더 넣어도 그대로였다(7·8회차).
  - 채점 전 질문을 모두 LLM 판단에 맡김: 정답 요청인데 문서 검색 도구를 먼저 부르거나, 반대로 개념 질문까지 거절했다.
  - 검색어 변환에 질문 문장만 넘김: "이 문제의 개념 설명해줘" 같은 질문이 막연한 검색어가 되어 문항에 따라 근거 부족으로 끝났다.
  - 웹 검색 API(Tavily)로 웹 조회: API 키가 필요해 쓰지 않기로 했다.
  - 외부 문제집 76문항 그대로 가져오기: 58문항은 공식 샘플과 같거나, 기존 문항과 겹치거나, 스냅샷 근거가 없거나, 문서 내용과 어긋나 제외했다.
- 최종 채택한 접근 · 왜 그것으로 갔는가
  - 반드시 지켜야 하는 것은 코드로 정한다: 채점, 해설, 거절 안내문, 채점 전 정답 요청 차단, 문서에 없는 식별자의 근거 부족 판정. 모델이 바뀌어도 결과가 같아야 하기 때문이다.
  - 화면 버튼(출제, 채점, 진도)은 LLM 없는 전용 경로로 처리한다. 의도가 정해진 동작에 LLM 호출 비용과 지연을 쓸 이유가 없다.
  - 웹 조회는 공식 사이트의 문서 목록(`llms.txt`)을 읽는 방식으로 키 없이 한다.
  - 문제 은행은 스냅샷 원문 구절이 정답을 직접 뒷받침하는 문항만 넣는다.
- 남은 한계 · 향후 개선 방향
  - 검색이 약하다. RAGAS 질문 15개 중 3개에서 맞는 구절을 찾지 못했다(검색어가 문서 표현과 다르거나, 맞는 구절의 유사도가 기준값 0.35에 못 미침). 벡터 검색 하나뿐이라 하이브리드 검색과 리랭킹을 넣는 것이 다음 개선이다. context_precision 0.76도 관계없는 구절이 함께 검색된다는 뜻이다.
  - RAGAS 정답은 사람이 검수하지 않았고 판정 모델이 답변 모델과 같아, 점수가 실제보다 좋게 나왔을 수 있다.
  - 문제 은행 43문항이 가이드 비중과 맞지 않는다(Domain 1~5: 8/6/10/7/12, 목표 14/9/10/10/7). 출제가 무작위라 Domain 5가 비중보다 자주 나온다.
  - 문제 은행 검수는 근거 구절 대조까지는 했지만, 문항 내용을 사람이 하나씩 읽고 승인한 것은 아니다(일괄 승인).
  - 인증이 없어 user_id를 그대로 믿는다. 공개 운영 전에 로그인과 호출 제한이 필요하다.
  - 평가 세트가 20문항이고 고정 시드 1회 실행이라, 모델의 흔들림을 충분히 보지 못한다.
  - Docker 이미지는 GitHub Actions에서 빌드하고, 자격증명 없이 띄워 화면과 출제·채점·진도 경로만 확인했다. 컨테이너 안에서 Bedrock을 쓰는 경로(`/query`)는 확인하지 않았다.

## 핵심 코드 위치
- `src/agent.py:5` — 진입점(`uvicorn src.agent:app`). 메인 에이전트 구현은 `src/orchestration/service.py:110`(`run_query`), `:122`(`create_agent` 구성), `:39`(ID별 도구 목록), `:89`(채점 전 사전 거절)
- `src/tools.py` — 도메인 도구 진입점. 구현은 `src/quiz/service.py:73`(출제), `src/grading/service.py:46`(채점·해설), `src/progress/service.py:35`(진도), `src/search/service.py:115`(문서 Q&A·힌트·후속 질문)
- `src/retriever.py` — RAG 파이프라인 진입점. 구현은 `src/retrieval/service.py:77`(청크 생성), `:124`(Chroma 인덱스), `:173`(검색), `:270`(웹 조회)
- `src/api/routes.py:27` — `POST /query`와 버튼 경로, `src/api/mcp_server.py` — MCP 서버
- `src/shared/tracing.py` — 트레이스 기록, `evaluation/run_eval.py` — 자체 평가
