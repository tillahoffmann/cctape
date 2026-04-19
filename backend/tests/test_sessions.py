import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from claude_context import create_app
from claude_context import sessions as sessions_module


@pytest.fixture
def sessions_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "sessions"
    d.mkdir()
    monkeypatch.setattr(sessions_module, "SESSIONS_DIR", d)
    return d


def _write(sessions_dir: Path, pid: int, **extra) -> None:
    payload = {"pid": pid, "cwd": "/tmp", "startedAt": 1775588305379, **extra}
    (sessions_dir / f"{pid}.json").write_text(json.dumps(payload))


def test_sync_all_on_startup_populates_sessions(
    tmp_path: Path, sessions_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(
        sessions_dir,
        1001,
        sessionId="sess-a",
        kind="interactive",
        entrypoint="claude-vscode",
    )
    _write(sessions_dir, 1002, sessionId="sess-b")

    db = tmp_path / "db.sqlite"
    monkeypatch.setenv("CLAUDE_CONTEXT_DB", str(db))
    with TestClient(create_app()):
        pass

    with closing(sqlite3.connect(db)) as conn:
        rows = dict(
            (sid, (pid, kind, entrypoint))
            for sid, pid, kind, entrypoint in conn.execute(
                "SELECT session_id, pid, kind, entrypoint FROM sessions"
            )
        )
    assert rows == {
        "sess-a": (1001, "interactive", "claude-vscode"),
        "sess-b": (1002, None, None),
    }


def test_ensure_known_picks_up_new_session_on_proxy_request(
    tmp_path: Path, sessions_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "db.sqlite"
    monkeypatch.setenv("CLAUDE_CONTEXT_DB", str(db))

    # Session appears after startup — mimics a Claude launched later.
    with TestClient(create_app()) as client:
        _write(sessions_dir, 1234, sessionId="sess-late")
        from unittest.mock import patch

        from httpx import Response

        with patch(
            "httpx._client.AsyncClient.send",
            side_effect=lambda *a, **k: Response(200, content=b""),
        ):
            client.post(
                "/proxy/v1/messages",
                content=b"{}",
                headers={"x-claude-code-session-id": "sess-late"},
            )

    with closing(sqlite3.connect(db)) as conn:
        row = conn.execute(
            "SELECT pid FROM sessions WHERE session_id = ?", ("sess-late",)
        ).fetchone()
    assert row == (1234,)


def test_ensure_known_skips_when_no_pid_file(
    tmp_path: Path, sessions_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "db.sqlite"
    monkeypatch.setenv("CLAUDE_CONTEXT_DB", str(db))

    with TestClient(create_app()) as client:
        from unittest.mock import patch

        from httpx import Response

        with patch(
            "httpx._client.AsyncClient.send",
            side_effect=lambda *a, **k: Response(200, content=b""),
        ):
            client.post(
                "/proxy/v1/messages",
                content=b"{}",
                headers={"x-claude-code-session-id": "orphan"},
            )

    with closing(sqlite3.connect(db)) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE session_id = ?", ("orphan",)
        ).fetchone()
    assert row == (0,)
