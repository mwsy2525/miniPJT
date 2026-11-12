"""세션별 대기 문제와 시도 기록을 SQLite에 읽고 쓴다. 쓰기 함수는 DB 쓰기 수를 돌려준다."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.shared.config import ROOT, env as _env


def db_path() -> Path:
    """SQLite 경로(.env PROGRESS_DB_PATH, 기본 data/progress.db)."""
    return Path(_env("PROGRESS_DB_PATH", str(ROOT / "data" / "progress.db")))


def _connect() -> sqlite3.Connection:
    """DB에 연결하고 스키마를 보장한다."""
    p = db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS pending(session_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,"
        " problem_id TEXT NOT NULL, ts TEXT NOT NULL);"
        "CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,"
        " session_id TEXT NOT NULL, question_id TEXT NOT NULL, chosen TEXT NOT NULL,"
        " correct INTEGER NOT NULL, ts TEXT NOT NULL);"
    )
    return conn


def _now() -> str:
    """UTC ISO 시각."""
    return datetime.now(timezone.utc).isoformat()


def get_pending(session_id: str) -> dict[str, Any] | None:
    """세션의 대기 문제를 읽는다."""
    with closing(_connect()) as c:
        r = c.execute("SELECT * FROM pending WHERE session_id=?", (session_id,)).fetchone()
        return dict(r) if r else None


def set_pending(user_id: str, session_id: str, problem_id: str) -> int:
    """대기 문제를 저장·교체한다. DB 쓰기 수(1)를 돌려준다."""
    with closing(_connect()) as c, c:
        c.execute("INSERT OR REPLACE INTO pending(session_id,user_id,problem_id,ts) VALUES(?,?,?,?)",
                  (session_id, user_id, problem_id, _now()))
    return 1


def record_grading(user_id: str, session_id: str, question_id: str, chosen: str, correct: bool) -> int:
    """시도 기록 저장과 대기 문제 삭제를 한 트랜잭션으로 처리한다. DB 쓰기 수(2)를 돌려준다."""
    with closing(_connect()) as c, c:
        c.execute("INSERT INTO attempts(user_id,session_id,question_id,chosen,correct,ts) VALUES(?,?,?,?,?,?)",
                  (user_id, session_id, question_id, chosen, int(correct), _now()))
        c.execute("DELETE FROM pending WHERE session_id=?", (session_id,))
    return 2


def latest_attempt(session_id: str) -> dict[str, Any] | None:
    """세션의 가장 최근 시도 기록."""
    with closing(_connect()) as c:
        r = c.execute("SELECT * FROM attempts WHERE session_id=? ORDER BY id DESC LIMIT 1",
                      (session_id,)).fetchone()
        return dict(r) if r else None


def user_attempts(user_id: str) -> list[dict[str, Any]]:
    """user_id 본인의 시도 기록(오래된 순)."""
    with closing(_connect()) as c:
        return [dict(r) for r in c.execute("SELECT * FROM attempts WHERE user_id=? ORDER BY id", (user_id,))]
