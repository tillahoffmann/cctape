import os
from pathlib import Path
from typing import Generator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from cctape import create_app


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "cctape.db"


@pytest.fixture
def client(db_path: Path) -> Generator[TestClient, None, None]:
    with patch.dict(os.environ, {"CCTAPE_DB": str(db_path)}):
        with TestClient(create_app()) as client:
            yield client


def test_cacheable_endpoint_returns_etag(client: TestClient) -> None:
    r = client.get("/api/sessions")
    assert r.status_code == 200
    etag = r.headers.get("etag")
    assert etag is not None
    assert etag.startswith('"') and etag.endswith('"')
    assert r.headers.get("cache-control") == "no-cache"


def test_matching_etag_returns_304(client: TestClient) -> None:
    first = client.get("/api/sessions")
    etag = first.headers["etag"]
    second = client.get("/api/sessions", headers={"if-none-match": etag})
    assert second.status_code == 304
    assert second.headers["etag"] == etag
    assert second.content == b""


def test_stale_etag_returns_200_with_new_body(client: TestClient) -> None:
    stale = client.get("/api/sessions", headers={"if-none-match": '"deadbeef"'})
    assert stale.status_code == 200
    assert stale.headers.get("etag") is not None


def test_etag_changes_after_db_write(client: TestClient, db_path: Path) -> None:
    first = client.get("/api/sessions")
    etag_before = first.headers["etag"]
    # Touch the db file's mtime forward so the middleware sees a new ETag.
    # Using a future timestamp avoids flakiness on filesystems with coarse
    # mtime resolution.
    later = os.stat(db_path).st_mtime_ns + 10_000_000_000  # +10s
    os.utime(db_path, ns=(later, later))
    second = client.get("/api/sessions", headers={"if-none-match": etag_before})
    assert second.status_code == 200
    assert second.headers["etag"] != etag_before


def test_noncacheable_endpoint_has_no_etag(client: TestClient) -> None:
    # /api/config is intentionally excluded from the allowlist — it doesn't
    # depend on DB writes and doesn't need conditional-GET plumbing.
    r = client.get("/api/config")
    assert r.status_code == 200
    assert r.headers.get("etag") is None
