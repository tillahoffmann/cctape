import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

# Module-level so tests can monkeypatch to a temporary directory.
SESSIONS_DIR = Path("~/.claude/sessions").expanduser()


def _iter_entries() -> list[dict]:
    if not SESSIONS_DIR.is_dir():
        return []
    entries: list[dict] = []
    for path in SESSIONS_DIR.glob("*.json"):
        try:
            entries.append(json.loads(path.read_text()))
        except (OSError, ValueError):
            continue
    return entries


def _upsert(conn: sqlite3.Connection, entry: dict) -> bool:
    session_id = entry.get("sessionId")
    pid = entry.get("pid")
    if not session_id or not pid:
        return False
    started_at = entry.get("startedAt")
    started_dt = (
        datetime.fromtimestamp(started_at / 1000, tz=UTC)
        if isinstance(started_at, (int, float))
        else None
    )
    conn.execute(
        """
        INSERT INTO sessions (session_id, pid, cwd, started_at, kind, entrypoint)
        VALUES (:session_id, :pid, :cwd, :started_at, :kind, :entrypoint)
        ON CONFLICT(session_id) DO UPDATE SET
            pid = excluded.pid,
            cwd = excluded.cwd,
            started_at = excluded.started_at,
            kind = excluded.kind,
            entrypoint = excluded.entrypoint
        """,
        {
            "session_id": session_id,
            "pid": pid,
            "cwd": entry.get("cwd"),
            "started_at": started_dt,
            "kind": entry.get("kind"),
            "entrypoint": entry.get("entrypoint"),
        },
    )
    return True


def sync_all(conn: sqlite3.Connection) -> None:
    for entry in _iter_entries():
        _upsert(conn, entry)
    conn.commit()


def ensure_known(conn: sqlite3.Connection, session_id: str | None) -> None:
    if not session_id:
        return
    row = conn.execute(
        "SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    if row:
        return
    for entry in _iter_entries():
        if entry.get("sessionId") == session_id and _upsert(conn, entry):
            conn.commit()
            return
