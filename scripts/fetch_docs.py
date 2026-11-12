"""공식 문서 스냅샷을 수동으로 갱신하는 스크립트.

data/docs/manifest.json의 문서마다 원본 URL 뒤에 .md를 붙인 마크다운 버전을 내려받아
data/docs/{doc_path}에 저장하고 fetched_at을 갱신한다. 런타임에는 쓰지 않는다.

사용법: python scripts/fetch_docs.py [doc_id ...]
"""

import datetime
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DOCS_DIR = Path(__file__).resolve().parent.parent / "data" / "docs"
MANIFEST = DOCS_DIR / "manifest.json"
TIMEOUT_SEC = 30
USER_AGENT = "ccarf-docs-snapshot/1.0"


def fetch_markdown(url: str) -> str:
    """원본 URL의 마크다운 버전(.md)을 내려받아 텍스트로 돌려준다."""
    req = urllib.request.Request(url + ".md", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
        return resp.read().decode("utf-8")


def main(only: set[str]) -> int:
    """manifest의 문서를 내려받아 저장하고, 실패한 문서 수를 돌려준다."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    allowed = set(manifest["allowed_domains"])
    today = datetime.date.today().isoformat()
    failed = 0
    for doc in manifest["documents"]:
        if only and doc["doc_id"] not in only:
            continue
        host = urllib.parse.urlparse(doc["url"]).hostname
        if host not in allowed:
            print(f"SKIP {doc['doc_id']}: 허용 도메인 아님 ({host})")
            failed += 1
            continue
        try:
            text = fetch_markdown(doc["url"])
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"FAIL {doc['doc_id']}: {exc}")
            failed += 1
            continue
        out = DOCS_DIR / doc["doc_path"]
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        doc["fetched_at"] = today
        print(f"OK   {doc['doc_id']} ({len(text):,} chars)")
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return failed


if __name__ == "__main__":
    sys.exit(1 if main(set(sys.argv[1:])) else 0)
