# RAGAS 평가 리포트 (문서 Q&A · search_docs)

- 실행 시각: 2026-11-12 17:17, 웹 폴백 off
- 답변·판정 모델: global.anthropic.claude-sonnet-4-6 (답변을 만든 모델과 판정 모델이 같다), 임베딩: amazon.titan-embed-text-v2:0
- 질문 15개 중 12개 채점. 검색 구절 없이 끝난 답변(거절, AI 일반 지식 답변)은 채점에서 뺐다.
- 정답(reference)은 스냅샷 구절을 바탕으로 AI가 쓴 것이고 사람이 검수하지 않았다. 질문은 문제 은행 문항과 같은 문서 구절에서 골랐다.

## 평균
- context_recall: 1.00 (n=12)
- context_precision: 0.76 (n=12)
- faithfulness: 0.96 (n=12)
- answer_relevancy: 0.72 (n=12)

## 질문별 점수
| id | context_recall | context_precision | faithfulness | answer_relevancy | 질문 |
|---|---|---|---|---|---|
| R01 | 1.00 | 0.92 | 1.00 | 0.63 | 에이전트 루프에서 Claude 응답의 stop_reason이 tool_use일 때와 end_turn일 때 각각 어떻게 해야 해? |
| R02 | 1.00 | 0.50 | 1.00 | 0.70 | Agent SDK에서 하위 에이전트(subagent)가 일을 마치면 부모 에이전트에게 무엇이 돌아와? |
| R03 | 1.00 | 0.25 | 1.00 | 0.67 | Agent SDK 훅으로 도구 출력을 Claude가 보기 전에 다른 값으로 바꾸려면 어떻게 해? |
| R04 | 1.00 | 0.75 | 1.00 | 0.79 | Agent SDK에서 세션을 fork하면 원래 세션은 어떻게 돼? |
| R06 | 1.00 | 0.92 | 0.82 | 0.63 | Agent SDK에서 코드 리뷰용 하위 에이전트가 파일을 수정하지 못하게 하려면 tools를 어떻게 설정해? |
| R07 | 1.00 | 0.33 | 1.00 | 0.71 | Claude Code에서 프로젝트별 개인 설정은 어느 파일에 둬야 해? |
| R08 | 1.00 | 0.75 | 1.00 | 0.70 | Claude Code에서 특정 경로의 파일을 작업할 때만 규칙이 적용되게 하려면 어떻게 해? |
| R09 | 1.00 | 1.00 | 1.00 | 0.74 | Claude Code의 plan mode에서는 Claude가 무엇을 하고 무엇을 하지 않아? |
| R10 | 1.00 | 0.75 | 1.00 | 0.69 | 구조화 출력(structured outputs)을 쓸 때 문자열 enum 값의 대소문자는 보장돼? |
| R11 | 1.00 | 0.92 | 0.88 | 0.72 | 구조화 출력 응답이 max_tokens 한도에 걸려 잘리면 어떻게 돼? 어떻게 대응해? |
| R12 | 1.00 | 1.00 | 1.00 | 0.77 | Message Batches API의 요금과 결과를 받는 시점은 어떻게 돼? |
| R14 | 1.00 | 1.00 | 0.89 | 0.91 | 긴 문서 여러 개를 프롬프트에 넣을 때 어떻게 구성해야 해? |

## 채점에서 뺀 질문
| id | 사유 | 질문 |
|---|---|---|
| R05 | ai_general_knowledge | 도구 정의에서 도구 성능에 가장 큰 영향을 주는 요소는 뭐야? |
| R13 | ai_general_knowledge | 프롬프트에 예시(few-shot)를 넣을 때 몇 개를 어떻게 넣는 게 좋아? |
| R15 | ai_general_knowledge | Claude Code에서 같은 이름의 스킬이 개인 폴더와 프로젝트 폴더에 둘 다 있으면 어느 것이 실행돼? |
