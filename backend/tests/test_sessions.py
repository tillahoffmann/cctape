import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from claude_context import create_app
from claude_context import sessions as sessions_module


@pytest.fixture
def projects_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "projects"
    d.mkdir()
    monkeypatch.setattr(sessions_module, "PROJECTS_DIR", d)
    return d


def _write(
    projects_dir: Path,
    session_id: str,
    *,
    cwd: str = "/tmp",
    git_branch: str | None = "main",
    is_sidechain: bool = False,
    project_dir: str = "-tmp",
    timestamp: str = "2026-04-19T00:00:00.000Z",
) -> None:
    pdir = projects_dir / project_dir
    pdir.mkdir(exist_ok=True)
    lines = [
        {"type": "file-history-snapshot"},
        {
            "type": "user",
            "sessionId": session_id,
            "cwd": cwd,
            "gitBranch": git_branch,
            "isSidechain": is_sidechain,
            "timestamp": timestamp,
        },
    ]
    (pdir / f"{session_id}.jsonl").write_text(
        "\n".join(json.dumps(e) for e in lines) + "\n"
    )


def test_sync_all_on_startup_populates_sessions(
    tmp_path: Path, projects_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(projects_dir, "sess-a", cwd="/work/a", git_branch="main")
    _write(
        projects_dir,
        "sess-b",
        cwd="/work/b",
        git_branch=None,
        is_sidechain=True,
        project_dir="-work-b",
    )

    db = tmp_path / "db.sqlite"
    monkeypatch.setenv("CLAUDE_CONTEXT_DB", str(db))
    with TestClient(create_app()):
        pass

    with closing(sqlite3.connect(db)) as conn:
        rows = dict(
            (sid, (cwd, git_branch, is_sidechain))
            for sid, cwd, git_branch, is_sidechain in conn.execute(
                "SELECT session_id, cwd, git_branch, is_sidechain FROM sessions"
            )
        )
    assert rows == {
        "sess-a": ("/work/a", "main", 0),
        "sess-b": ("/work/b", None, 1),
    }


def test_ensure_known_picks_up_new_session_on_proxy_request(
    tmp_path: Path, projects_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "db.sqlite"
    monkeypatch.setenv("CLAUDE_CONTEXT_DB", str(db))

    with TestClient(create_app()) as client:
        _write(projects_dir, "sess-late", cwd="/late")
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
            "SELECT cwd FROM sessions WHERE session_id = ?", ("sess-late",)
        ).fetchone()
    assert row == ("/late",)


def test_ensure_known_skips_when_no_transcript(
    tmp_path: Path, projects_dir: Path, monkeypatch: pytest.MonkeyPatch
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
