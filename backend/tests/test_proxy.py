import gzip
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Generator
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient
from httpx import Response

from cctape import create_app
from cctape.storage import decompress

ROOT = Path(__file__).parent


@pytest.fixture
def cctape_db(tmp_path: Path) -> Path:
    return tmp_path / ".cctape.db"


@pytest.fixture
def app(cctape_db: Path) -> Generator:
    with patch.dict(os.environ, {"CCTAPE_DB": str(cctape_db)}):
        yield create_app()


@pytest.fixture
def client(app) -> Generator:
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize("compress", [False, True])
def test_post_message(client: TestClient, cctape_db: Path, compress: bool) -> None:
    async def _send(*args, **kwargs) -> Response:
        sample_response = (ROOT / "sample_response.txt").read_text()
        headers = {
            "date": "Sat, 18 Apr 2026 19:56:51 GMT",
            "anthropic-organization-id": "org-abc123",
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
        assert response.status_code == 200
        assert b"is there something" in response.content
    mock_send.assert_called_once()

    with closing(sqlite3.connect(cctape_db)) as conn:
        (
            request_row_id,
            session_id,
            system_hash,
            tools_hash,
            message_hashes,
            legacy_payload,
        ) = conn.execute(
            "SELECT id, session_id, system_hash, tools_hash, message_hashes, payload FROM requests"
        ).fetchone()
        assert session_id == "testing-session-1234"
        # Parseable bodies take the dedup path: legacy payload column stays NULL,
        # and every referenced hash exists in `blobs`.
        assert legacy_payload is None
        assert isinstance(system_hash, bytes) and len(system_hash) == 32
        assert isinstance(tools_hash, bytes) and len(tools_hash) == 32
        # `message_hashes` is a BLOB of concatenated 32-byte digests.
        assert isinstance(message_hashes, bytes)
        assert len(message_hashes) > 0 and len(message_hashes) % 32 == 0
        hashes = [message_hashes[i : i + 32] for i in range(0, len(message_hashes), 32)]
        for digest in [system_hash, tools_hash, *hashes]:
            assert (
                conn.execute("SELECT 1 FROM blobs WHERE hash = ?", (digest,)).fetchone()
                is not None
            )

        (
            referenced_row_id,
            output_tokens,
            model,
            account_id,
            cache_5m,
            cache_1h,
            payload,
        ) = conn.execute(
            "SELECT request_row_id, output_tokens, model, account_id, "
            "cache_creation_5m_input_tokens, cache_creation_1h_input_tokens, payload "
            "FROM responses"
        ).fetchone()
        assert request_row_id == referenced_row_id
        assert output_tokens == 21
        assert model == "claude-opus-4-7"
        assert account_id == "org-abc123"
        # Sample response has cache_creation = {5m: 0, 1h: 42103}.
        assert cache_5m == 0
        assert cache_1h == 42103
        assert b"is there something" in decompress(payload)


def test_post_message_upstream_read_error_returns_502(client: TestClient) -> None:
    async def _send(*args, **kwargs):
        raise httpx.ReadError("upstream closed connection")

    sample_request = (ROOT / "sample_request.json").read_text()
    with patch("httpx._client.AsyncClient.send", side_effect=_send):
        response = client.post(
            "/proxy/v1/messages",
            content=sample_request,
            headers={"x-claude-code-session-id": "testing-session-502"},
        )
    assert response.status_code == 502
