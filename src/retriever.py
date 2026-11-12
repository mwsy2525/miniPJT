"""RAG 파이프라인: 공식 문서 스냅샷 청크 생성, Chroma 인덱스 로드·재생성, 검색, 웹 폴백.

청크 규칙: 스냅샷 마크다운을 `##`·`###` 제목 단위로 나누고 chunk_id는 `{doc_id}#{제목 슬러그}`다.
비밀 값과 설정은 모두 .env(환경변수)에서 읽는다.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DOCS_DIR = ROOT / "data" / "docs"
MANIFEST_PATH = DOCS_DIR / "manifest.json"
_HEADING = re.compile(r"^(#{2,3})\s+(.+?)\s*$")
_MAX_EMBED_CHARS = 1800  # 임베딩 입력 길이 제한용 하위 조각 크기


def _env(name: str, default: str = "") -> str:
    """환경변수를 읽는다(없으면 기본값)."""
    return os.environ.get(name, default)


def chroma_dir() -> Path:
    """Chroma 영속 저장 경로를 돌려준다."""
    return Path(_env("CHROMA_DIR", str(ROOT / "data" / "chroma")))


def similarity_threshold() -> float:
    """근거 부족 판정 유사도 기준값(.env SIMILARITY_THRESHOLD)."""
    return float(_env("SIMILARITY_THRESHOLD", "0.35"))


def slugify(title: str) -> str:
    """제목을 chunk_id용 슬러그로 바꾼다(역슬래시 제거, 소문자, 영숫자 외는 '-')."""
    t = title.replace("\\", "").replace("`", "").lower()
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    return t or "section"


@lru_cache(maxsize=1)
def load_manifest() -> dict[str, Any]:
    """data/docs/manifest.json을 읽는다."""
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def doc_by_path(doc_path: str) -> dict[str, Any] | None:
    """doc_path로 manifest 문서 항목을 찾는다."""
    for d in load_manifest()["documents"]:
        if d["doc_path"] == doc_path:
            return d
    return None


def manifest_urls() -> set[str]:
    """manifest에 있는 원본 URL 집합."""
    return {d["url"] for d in load_manifest()["documents"]}


def split_markdown(text: str) -> list[tuple[str, str]]:
    """마크다운을 `##`·`###` 제목 단위로 나눠 (제목, 본문) 목록을 돌려준다. 코드 펜스 안의 '#'은 무시한다."""
    sections: list[tuple[str, list[str]]] = []
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        m = None if in_fence else _HEADING.match(line)
        if m:
            sections.append((m.group(2), [line]))
        elif sections:
            sections[-1][1].append(line)
    return [(title, "\n".join(lines).strip()) for title, lines in sections]


@lru_cache(maxsize=1)
def load_chunks() -> list[dict[str, Any]]:
    """모든 스냅샷 문서를 청크(chunk_id, doc_path, url, domain, text) 목록으로 만든다."""
    chunks: list[dict[str, Any]] = []
    for doc in load_manifest()["documents"]:
        path = DOCS_DIR / doc["doc_path"]
        if not path.exists():
            continue
        seen: dict[str, int] = {}
        for title, body in split_markdown(path.read_text(encoding="utf-8")):
            slug = slugify(title)
            seen[slug] = seen.get(slug, 0) + 1
            if seen[slug] > 1:
                slug = f"{slug}-{seen[slug]}"
            chunks.append({
                "chunk_id": f"{doc['doc_id']}#{slug}",
                "doc_path": doc["doc_path"],
                "url": doc["url"],
                "domain": int(doc["domains"][0]) if doc.get("domains") else 0,
                "domains": list(doc.get("domains", [])),
                "text": body,
            })
    return chunks


def get_chunk_text(chunk_id: str, doc_path: str | None = None) -> str | None:
    """chunk_id가 가리키는 원문 구절을 돌려준다(임베딩 없이 파일에서 직접 읽는다). 없으면 None."""
    for c in load_chunks():
        if c["chunk_id"] == chunk_id and (doc_path is None or c["doc_path"] == doc_path):
            return c["text"]
    return None


def _pieces(text: str) -> list[str]:
    """긴 청크를 임베딩 입력 길이에 맞게 조각낸다."""
    if len(text) <= _MAX_EMBED_CHARS:
        return [text]
    return [text[i:i + _MAX_EMBED_CHARS] for i in range(0, len(text), _MAX_EMBED_CHARS)]


def _signature() -> str:
    """manifest 내용과 임베딩 모델 ID로 인덱스 서명을 만든다."""
    raw = MANIFEST_PATH.read_bytes() + _env("EMBEDDING_MODEL_ID").encode() + b"|meta-v2"
    return hashlib.sha256(raw).hexdigest()


def get_embeddings():
    """BedrockEmbeddings를 만든다(LLM과 같은 자격증명, 모델 ID는 .env)."""
    from langchain_aws import BedrockEmbeddings

    return BedrockEmbeddings(model_id=_env("EMBEDDING_MODEL_ID"), region_name=_env("AWS_REGION") or None)


def get_vectorstore(embeddings=None, force: bool = False):
    """Chroma 벡터 저장소를 로드한다. 인덱스가 없거나 manifest·임베딩 모델이 바뀐 경우에만 다시 만든다."""
    from langchain_chroma import Chroma

    emb = embeddings or get_embeddings()
    cdir = chroma_dir()
    meta_file = cdir / "index_meta.json"
    sig = _signature()
    fresh = (
        not force
        and meta_file.exists()
        and json.loads(meta_file.read_text(encoding="utf-8")).get("signature") == sig
    )
    store = Chroma(collection_name="ccarf_docs", embedding_function=emb, persist_directory=str(cdir),
                   collection_metadata={"hnsw:space": "cosine"})
    if fresh:
        return store
    try:
        store.delete_collection()
    except Exception:
        pass
    store = Chroma(collection_name="ccarf_docs", embedding_function=emb, persist_directory=str(cdir),
                   collection_metadata={"hnsw:space": "cosine"})
    texts, metas, ids = [], [], []
    for c in load_chunks():
        for i, piece in enumerate(_pieces(c["text"])):
            texts.append(piece)
            metas.append({"chunk_id": c["chunk_id"], "doc_path": c["doc_path"], "url": c["url"],
                          "domain": c["domain"], **{f"d{n}": True for n in c["domains"]}})
            ids.append(f"{c['chunk_id']}::{i}")
    for i in range(0, len(texts), 64):
        store.add_texts(texts[i:i + 64], metadatas=metas[i:i + 64], ids=ids[i:i + 64])
    cdir.mkdir(parents=True, exist_ok=True)
    meta_file.write_text(json.dumps({"signature": sig}), encoding="utf-8")
    return store


def in_domain(doc_path: str, domain: int) -> bool:
    """doc_path 문서가 manifest상 해당 영역(domain 번호)에 속하는지."""
    d = doc_by_path(doc_path)
    return bool(d and domain in d.get("domains", []))


def search(query: str, k: int = 4, domain: int | None = None, store=None) -> list[dict[str, Any]]:
    """로컬 Chroma에서 검색한다. 결과는 {text, doc_path, chunk_id, url, score(코사인 유사도)} 목록이며 chunk_id 중복은 제거한다."""
    vs = store or get_vectorstore()
    flt = {f"d{domain}": True} if domain else None  # 문서가 여러 영역에 걸치면 해당 영역 플래그가 모두 True
    raw = vs.similarity_search_with_score(query, k=k * 3, filter=flt)
    hits: list[dict[str, Any]] = []
    seen: set[str] = set()
    for doc, dist in raw:
        cid = doc.metadata["chunk_id"]
        if domain and not in_domain(doc.metadata["doc_path"], domain):
            continue  # 영역 지정 시 그 영역 문서만(안전망)
        if cid in seen:
            continue
        seen.add(cid)
        full = get_chunk_text(cid) or doc.page_content
        hits.append({"text": full, "doc_path": doc.metadata["doc_path"], "chunk_id": cid,
                     "url": doc.metadata["url"], "score": 1.0 - float(dist)})
        if len(hits) >= k:
            break
    return hits


def web_fallback_enabled() -> bool:
    """웹 폴백 on/off 플래그(.env WEB_FALLBACK_ENABLED)."""
    return _env("WEB_FALLBACK_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def allowed_domains() -> list[str]:
    """웹 폴백 허용 도메인(.env WEB_ALLOWED_DOMAINS, 기본은 manifest의 allowed_domains)."""
    env = _env("WEB_ALLOWED_DOMAINS")
    if env:
        return [d.strip() for d in env.split(",") if d.strip()]
    return list(load_manifest().get("allowed_domains", []))


def url_allowed(url: str) -> bool:
    """URL의 호스트가 허용 도메인(또는 하위 도메인)인지 검사한다."""
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in allowed_domains())


def web_search(query: str, k: int = 3) -> list[dict[str, Any]]:
    """허용 도메인 공식 문서 페이지를 실시간 조회한다. 결과는 {text, url, doc_path: None, chunk_id: None}.

    저장하지 않는다. 비활성·키 없음·타임아웃·오류는 모두 빈 목록(=근거 부족)으로 처리한다.
    """
    if not web_fallback_enabled():
        return []
    api_key = _env("WEB_SEARCH_API_KEY")
    if not api_key:
        return []
    try:
        import httpx

        resp = httpx.post(
            _env("WEB_SEARCH_ENDPOINT", "https://api.tavily.com/search"),
            json={"api_key": api_key, "query": query, "max_results": k * 2,
                  "include_domains": allowed_domains()},
            timeout=float(_env("WEB_TIMEOUT_SECONDS", "8")),
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for r in results:
        url, text = r.get("url", ""), (r.get("content") or "").strip()
        if text and url_allowed(url):
            out.append({"text": text[:3000], "url": url, "doc_path": None, "chunk_id": None})
        if len(out) >= k:
            break
    return out
