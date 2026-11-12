"""문제 은행 검사(Seed AC5): 검수 문항의 필수 필드, 근거·참고 문서 연결, 영역별 최소 문항 수."""
from __future__ import annotations

from src import tools as T
from src.retrieval import service as retriever

FIELDS = ["id", "domain", "scope", "task_statement", "reviewed", "stem_en", "choices_en", "answer", "stem_ko",
          "choices_ko", "intent", "trap", "why_correct", "why_wrong", "learning_points", "references", "evidence",
          "reviewed_by", "reviewed_at"]
MVP_PER_DOMAIN = 2  # 제출 기준: 근거 부족이 아닌 영역마다 scope=exam 검수 문항 2개 이상


def _reviewed():
    return [q for q in T.load_questions() if q.get("reviewed") is True]


def test_ids_are_unique():
    ids = [q["id"] for q in T.load_questions()]
    assert len(ids) == len(set(ids))


def test_reviewed_items_have_all_fields():
    """reviewed=true 문항은 필드가 모두 채워져 있다(scenario만 null 허용)."""
    for q in _reviewed():
        for f in FIELDS:
            assert q.get(f) not in (None, "", [], {}), (q["id"], f)
        assert "scenario" in q and (q["scenario"] is None or 1 <= q["scenario"] <= 6), q["id"]
        assert set(q["choices_en"]) == set(q["choices_ko"]) == set("ABCD"), q["id"]
        assert q["answer"] in "ABCD" and len(q["answer"]) == 1, q["id"]
        assert set(q["why_wrong"]) == set("ABCD") - {q["answer"]}, q["id"]  # 오답 보기 3개 모두
        assert all(r.get("url") and r.get("title") and r.get("note") for r in q["references"]), q["id"]


def test_scope_and_task_statement():
    """exam 문항은 exam_domains.yaml에 있는 Task Statement에만 연결된다."""
    valid = {str(k) for d in T.load_domains()["domains"] for k in d["task_statements"]}
    for q in _reviewed():
        assert q["scope"] in {"exam", "study"}, q["id"]
        if q["scope"] == "exam":
            assert str(q["task_statement"]) in valid, q["id"]
            assert str(q["task_statement"]).split(".")[0] == str(q["domain"]), q["id"]


def test_references_and_evidence_point_to_snapshot():
    """참고 URL은 manifest 안에만 있고, 근거 구절 chunk_id는 실제 스냅샷 구절이다."""
    urls = retriever.manifest_urls()
    chunks = {(c["doc_path"], c["chunk_id"]) for c in retriever.load_chunks()}
    for q in _reviewed():
        for r in q["references"]:
            assert r["url"] in urls, (q["id"], r["url"])
        for e in q["evidence"]:
            assert (e["doc_path"], e["chunk_id"]) in chunks, (q["id"], e)


def test_each_domain_has_minimum_exam_items():
    """근거 부족이 아닌 영역마다 scope=exam 검수 문항이 MVP 기준 이상이다."""
    counts: dict[int, int] = {}
    for q in _reviewed():
        if q["scope"] == "exam":
            counts[q["domain"]] = counts.get(q["domain"], 0) + 1
    for d in T.load_domains()["domains"]:
        if not d.get("evidence_gap"):
            assert counts.get(d["id"], 0) >= MVP_PER_DOMAIN, (d["id"], counts)
