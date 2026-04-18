import gzip
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Generator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from claude_context import create_app

ROOT = Path(__file__).parent


@pytest.fixture
def claude_context_db(tmp_path: Path) -> Path:
    return tmp_path / ".claude-context.db"


@pytest.fixture
def app(claude_context_db: Path) -> Generator:
    with patch.dict(os.environ, {"CLAUDE_CONTEXT_DB": str(claude_context_db)}):
        yield create_app()


@pytest.fixture
def client(app) -> Generator:
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize("compress", [False, True])
def test_post_message(
    client: TestClient, claude_context_db: Path, compress: bool
) -> None:
    async def _send(*args, **kwargs) -> Response:
        sample_response = (ROOT / "sample_response.txt").read_text()
        headers = {
            "date": "Sat, 18 Apr 2026 19:56:51 GMT",
        }
        if compress:
            headers["content-encoding"] = "gzip"
            sample_response = gzip.compress(sample_response.encode())

        return Response(200, content=sample_response, headers=headers)

    sample_request = (ROOT / "sample_request.json").read_text()
    with patch("httpx._client.AsyncClient.send", side_effect=_send) as mock_send:
        response = client.post(
            "/proxy/v1/messages",
            content=sample_request,
            headers={"x-claude-code-session-id": "testing-session-1234"},
        )
        assert b"is there something" in response.content
    mock_send.assert_called_once()

    with closing(sqlite3.connect(claude_context_db)) as conn:
        (request_row_id, session_id) = conn.execute(
            "SELECT id, session_id FROM requests"
        ).fetchone()
        assert session_id == "testing-session-1234"
        referenced_row_id, output_tokens = conn.execute(
            "SELECT request_row_id, output_tokens FROM responses"
        ).fetchone()
        assert request_row_id == referenced_row_id
        assert output_tokens == 21
