import os
from pathlib import Path
from typing import Generator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from cctape import create_app


@pytest.fixture
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    db_path = tmp_path / "cctape.db"
    with patch.dict(os.environ, {"CCTAPE_DB": str(db_path)}):
        with TestClient(create_app()) as client:
            yield client


def test_spa_deep_link_serves_index(client: TestClient) -> None:
    r = client.get("/setup")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert '<div id="root"></div>' in r.text or "<html" in r.text.lower()


def test_spa_nested_deep_link_serves_index(client: TestClient) -> None:
    r = client.get("/sessions/some-id")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_api_404_stays_404(client: TestClient) -> None:
    r = client.get("/api/does-not-exist")
    assert r.status_code == 404


def test_proxy_404_stays_404(client: TestClient) -> None:
    r = client.get("/proxy/does-not-exist")
    assert r.status_code == 404
