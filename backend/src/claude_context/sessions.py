import json
import sqlite3
from datetime import datetime
from pathlib import Path

# Module-level so tests can monkeypatch to a temporary directory.
PROJECTS_DIR = Path("~/.claude/projects").expanduser()


def _read_metadata(path: Path) -> dict | None:
    try:
        with path.open() as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if (
                    isinstance(entry, dict)
                    and entry.get("sessionId")
                    and entry.get("cwd")
                ):
                    return entry
    except OSError:
        return None
    return None


def _upsert(conn: sqlite3.Connection, entry: dict) -> bool:
    session_id = entry.get("sessionId")
    cwd = entry.get("cwd")
    if not session_id or not cwd:
        return False
    timestamp = entry.get("timestamp")
    started_dt = None
    if isinstance(timestamp, str):
        try:
            started_dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            started_dt = None
    conn.execute(
        """
        INSERT INTO sessions (session_id, cwd, started_at, git_branch, is_sidechain)
        VALUES (:session_id, :cwd, :started_at, :git_branch, :is_sidechain)
        ON CONFLICT(session_id) DO UPDATE SET
            cwd = excluded.cwd,
            started_at = excluded.started_at,
            git_branch = excluded.git_branch,
            is_sidechain = excluded.is_sidechain
        """,
        {
            "session_id": session_id,
            "cwd": cwd,
            "started_at": started_dt,
            "git_branch": entry.get("gitBranch"),
            "is_sidechain": 1 if entry.get("isSidechain") else 0,
        },
    )
    return True


def sync_all(conn: sqlite3.Connection) -> None:
    if not PROJECTS_DIR.is_dir():
        return
    for path in PROJECTS_DIR.glob("*/*.jsonl"):
        entry = _read_metadata(path)
        if entry is not None:
            _upsert(conn, entry)
    conn.commit()


def ensure_known(conn: sqlite3.Connection, session_id: str | None) -> None:
    if not session_id or not PROJECTS_DIR.is_dir():
        return
    for path in PROJECTS_DIR.glob(f"*/{session_id}.jsonl"):
        entry = _read_metadata(path)
        if entry is not None and _upsert(conn, entry):
            conn.commit()
            return
