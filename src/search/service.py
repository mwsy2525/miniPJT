"""search_docs: 한국어 질문 → 영어 검색어 → 공식 문서 RAG 답변. 런타임 LLM 네 곳은 모두 이 모듈에 있다."""
from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from src.question_bank import service as bank
from src.retrieval import service as retriever
from src.search import prompts as P
from src.search.schemas import GeneralAnswer, SearchAnswer
from src.sessions import service as sessions
from src.shared import llm
from src.shared.config import env as _env
from src.shared.context import ToolContext

GENERAL_LABEL = P.GENERAL_LABEL


def llm_rewrite_query(question: str) -> str:
    """[LLM 1] 한국어 질문을 영어 검색어로 바꾼다(실패하면 원문)."""
    try:
        msg = llm.get_llm().invoke([SystemMessage(content=P.REWRITE_SYSTEM),
                                    HumanMessage(content=f"<text>{question}</text>")])
        out = llm.message_text(msg).strip()
    except Exception:
        return question
    # 검색어가 아니라 대화문이 나오면(한글·여러 줄·너무 긴 출력) 원문으로 검색한다
    if not out or "\n" in out or len(out) > 200 or re.search(r"[가-힣]", out):
        return question
    return out


def llm_answer(question: str, passages: list[str], mode: str, extra: str, oos_topics: list[str]) -> SearchAnswer:
    """[LLM 2·3] 구절 기반 한국어 답변을 구조화 출력으로 만든다. mode는 general/hint/followup."""
    rules = P.ANSWER_RULES + [P.OOS_RULE + "; ".join(oos_topics)]
    if mode == "hint":
        rules.append(P.HINT_RULE)
    if mode == "followup":
        rules.append(P.FOLLOWUP_RULE)
    body = f"[QUESTION]\n{question}\n\n[CONTEXT]\n{extra or '(없음)'}\n\n[PASSAGES]\n" + "\n---\n".join(passages)
    model = llm.get_llm().with_structured_output(SearchAnswer)
    return model.invoke([SystemMessage(content="\n".join(rules)), HumanMessage(content=body)])


def llm_general_answer(question: str, extra: str = "") -> GeneralAnswer:
    """[LLM 4] 문서 근거가 없는 Claude·Anthropic 질문에 AI 일반 지식으로 답한다. 실패하면 unknown으로 본다."""
    try:
        model = llm.get_llm().with_structured_output(GeneralAnswer)
        facts = "\n".join(f"- {x}" for x in bank.load_domains().get("exam_facts", []))
        body = ("[QUESTION]\n" + question + "\n\n[CONTEXT]\n" + (extra or "(없음)")
                + "\n\n[EXAM FACTS]\n" + (facts or "(없음)"))
        return model.invoke([SystemMessage(content="\n".join(P.GENERAL_RULES)), HumanMessage(content=body)])
    except Exception:
        return GeneralAnswer(unknown=True)


def _general_fallback(ctx: ToolContext, question: str, mode: str, extra: str) -> str | None:
    """문서 근거가 없을 때 AI 일반 지식으로 답한다. 채점 전 힌트에서는 쓰지 않고, 모르면 None을 돌려준다."""
    if mode == "hint":
        return None
    g = llm_general_answer(question, extra)
    if g.unknown or not g.answer.strip():
        return None
    ctx.tool_calls[-1]["args"]["answer_basis"] = "ai_general_knowledge"  # trace에 근거 종류를 남긴다
    return ctx.finish(P.GENERAL_LABEL + "\n" + g.answer)


_CHOICE_TOKEN = re.compile(r"(?<![A-Za-z0-9_])[A-D](?![A-Za-z0-9_])")
_ASK_WORDS = re.compile(r"보기|선택지|option|choice|맞|틀|정답|오답|왜|correct|wrong|right|answer", re.I)
_TRANSLATE = re.compile(r"번역|해석|translat", re.I)
_ANSWER_REQ = re.compile(r"정답|답이\s*뭐|correct answer|answer is", re.I)


def is_pre_grading_leak_request(question: str) -> bool:
    """대기 문제에 대한 정답·특정 보기 정오·번역 요청인지 휴리스틱으로 검사한다(안전망)."""
    return bool((_CHOICE_TOKEN.search(question) and _ASK_WORDS.search(question))
                or _TRANSLATE.search(question) or _ANSWER_REQ.search(question))


_IDENT_TERM = re.compile(r"(?<![\w-])-{0,2}[A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)+")


def missing_identifier_terms(question: str, passages: list[str]) -> list[str]:
    """질문 속 식별자형 용어(하이픈·밑줄로 이은 이름, --플래그) 중 구절 어디에도 없는 것을 돌려준다."""
    def norm(s: str) -> str:  # tool_use·tool-use·tool use를 같은 말로 본다
        return re.sub(r"[-_\s]+", " ", s.lower())
    joined = norm(" ".join(passages))
    terms = {t.lstrip("-").lower() for t in _IDENT_TERM.findall(question)}
    return sorted(t for t in terms if norm(t) not in joined)


_DOMAIN_NUM = re.compile(r"(?:domain|도메인|영역)\s*([1-5])(?!\d)", re.I)


def detect_domain(question: str) -> int | None:
    """질문에서 영역 지정('Domain 2', '도메인 2', '영역 2', 영역 이름)을 찾아 번호를 돌려준다. 없으면 None."""
    m = _DOMAIN_NUM.search(question)
    if m:
        return int(m.group(1))
    low = question.lower()
    for d in bank.load_domains()["domains"]:
        if d["name"].lower() in low:
            return d["id"]
    return None


def _append_refs(answer: str, sources: list[dict[str, Any]]) -> str:
    """답변 끝에 근거 URL을 붙인다."""
    urls = list(dict.fromkeys(s["url"] for s in sources if s.get("url")))
    return answer + ("\n\n참고 문서:\n" + "\n".join(f"- {u}" for u in urls) if urls else "")


def run_search_docs(ctx: ToolContext, question: str) -> str:
    """공식 문서 RAG로 한국어 답변을 만든다. 대기 문제가 있으면 개념 힌트, 채점 직후면 후속 질문 맥락을 붙인다."""
    args = ctx.record_call("search_docs", {"question": question})

    pending = sessions.get_pending(ctx.session_id) if ctx.both_ids else None
    hint = bool(pending and pending["user_id"] == ctx.user_id)
    followup_q = None
    if ctx.both_ids and not pending:
        last = sessions.latest_attempt(ctx.session_id)
        if last and last["user_id"] == ctx.user_id:
            followup_q = bank.question_by_id(last["question_id"])
    mode = "hint" if hint else ("followup" if followup_q else "general")

    if hint:
        pq = bank.question_by_id(pending["problem_id"])
        ctx.problem_id = pending["problem_id"]
        if is_pre_grading_leak_request(question):
            return ctx.finish("채점 전에는 정답, 보기별 정오, 문제·보기 번역과 해설을 알려 드릴 수 없습니다. "
                              "답을 A~D로 제출하시면 채점 후 검수 해설을 모두 보여 드립니다. "
                              "막힌 개념은 개념 질문으로 물어봐 주세요.", "채점 전 해설 요청")
    else:
        pq = None
    if followup_q:
        ctx.problem_id = followup_q["id"]

    query_en = llm_rewrite_query(question)
    args["search_query_en"] = query_en

    blocked = {e["chunk_id"] for e in pq["evidence"]} if pq else set()
    if not blocked and pending:  # 다른 user_id의 대기 문제도 근거 구절은 새지 않게 거른다
        other = bank.question_by_id(pending["problem_id"])
        blocked = {e["chunk_id"] for e in other["evidence"]} if other else set()
    top_k = int(_env("RETRIEVAL_TOP_K", "4"))
    domain = detect_domain(question)
    if domain:
        args["domain"] = domain
    hits = [h for h in retriever.search(query_en, k=top_k + len(blocked), domain=domain)
            if h["chunk_id"] not in blocked][:top_k]
    threshold = retriever.similarity_threshold()
    good = [h for h in hits if h["score"] >= threshold]
    if not good and not domain:  # 영역 지정 질문은 영역 소속을 확인할 수 없는 웹 구절을 쓰지 않는다
        good = [h for h in retriever.web_search(query_en) if h.get("text")]  # 근거 부족일 때만 웹 폴백

    extra = ""
    ev_hits: list[dict[str, Any]] = []
    if followup_q:
        for e in followup_q["evidence"]:
            t = retriever.get_chunk_text(e["chunk_id"], e["doc_path"])
            if t and (not domain or retriever.in_domain(e["doc_path"], domain)):
                d = retriever.doc_by_path(e["doc_path"])
                ev_hits.append({"text": t, "doc_path": e["doc_path"], "chunk_id": e["chunk_id"],
                                "url": d["url"] if d else None})
        ctx_q = {k: followup_q[k] for k in ("stem_en", "choices_en", "intent", "trap", "why_correct", "why_wrong",
                                            "learning_points")}
        # 채점이 끝난 문항이므로 사용자가 고른 답과 정답을 함께 넘긴다("내가 고른 보기"를 짚어 답하게 함)
        ctx_q.update(user_chosen=last["chosen"], correct_answer=followup_q["answer"])
        extra = json.dumps(ctx_q, ensure_ascii=False)
    elif hint:
        extra = json.dumps({"stem_en": pq["stem_en"], "choices_en": pq["choices_en"], "intent": pq["intent"]},
                           ensure_ascii=False)

    used = ev_hits + [h for h in good if h["chunk_id"] not in {x["chunk_id"] for x in ev_hits}]
    if not used:
        general = _general_fallback(ctx, question, mode, extra)
        if general is not None:
            return general
        return ctx.finish("공식 문서 스냅샷(과 웹 폴백)에서 이 질문의 근거를 찾지 못했습니다(근거 부족). "
                          "질문을 더 구체적으로 바꾸거나 Claude·Anthropic 공식 문서 주제로 물어봐 주세요.", "근거 부족")

    # 질문이 이름으로 가리킨 기능·플래그가 구절에 아예 없으면 LLM 판단 없이 코드가 근거 부족으로 본다
    missing = missing_identifier_terms(question, [h["text"] for h in used]) if mode == "general" else []
    if missing:
        args["missing_terms"] = missing
    result = None if missing else llm_answer(question, [h["text"] for h in used], mode, extra,
                                             bank.load_domains().get("out_of_scope_topics", []))
    if result is None or result.insufficient:
        general = _general_fallback(ctx, question, mode, extra)
        if general is not None:
            return general
        return ctx.finish("검색된 구절이 질문에 답하지 못해 근거가 부족합니다. 질문을 구체화해 다시 물어봐 주세요.",
                          "근거 부족")
    ctx.contexts = [h["text"] for h in used]
    ctx.sources = [{"url": h.get("url"), "doc_path": h.get("doc_path"), "chunk_id": h.get("chunk_id")} for h in used]
    text = result.answer
    if result.out_of_scope_topic:
        text = P.OOS_LABEL + text
    if mode == "hint":
        text = P.HINT_LABEL + "\n" + text
    elif mode == "followup":
        text = P.FOLLOWUP_LABEL + "\n" + text
    return ctx.finish(_append_refs(text, ctx.sources))
