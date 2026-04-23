"""Tests for the MCP tool impl functions in cctape.mcp_tools.

These drive the pure Python impls directly against a seeded sqlite connection.
No MCP protocol transport is exercised here — that's orthogonal and expensive
to test. See test_etag.py for the app-level TestClient pattern if you need to
cover the mount itself.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from cctape import mcp_caller
from cctape.fts import backfill as fts_backfill
from cctape.fts import ensure_schema as fts_ensure_schema
from cctape.mcp_tools import (
    MAX_WINDOW_LIMIT,
    get_session_window_impl,
    search_transcripts_impl,
)
from cctape.storage import decompose_payload


def _init_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    schema_path = Path(__file__).parent.parent / "src" / "cctape" / "schema.sql"
    conn.executescript(schema_path.read_text())
    return conn


def _insert_session(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    cwd: str = "/work/a",
    git_branch: str | None = "main",
    title: str | None = None,
    started_at: str = "2026-04-20T12:00:00",
) -> None:
    conn.execute(
        "INSERT INTO sessions (session_id, cwd, git_branch, is_sidechain, started_at, title) "
        "VALUES (?, ?, ?, 0, ?, ?)",
        (session_id, cwd, git_branch, started_at, title),
    )


def _insert_request(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    messages: list[dict[str, Any]],
    timestamp: str,
) -> int:
    body = json.dumps({"model": "claude-opus-4-7", "messages": messages}).encode()
    decomposed = decompose_payload(conn, body)
    cur = conn.execute(
        """
        INSERT INTO requests (
            timestamp, headers, session_id,
            system_hash, tools_hash, message_hashes, extras, payload
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            timestamp,
            b"{}",  # headers placeholder; FTS doesn't touch this
            session_id,
            decomposed["system_hash"],
            decomposed["tools_hash"],
            decomposed["message_hashes"],
            decomposed["extras"],
            decomposed["payload"],
        ),
    )
    conn.commit()
    return int(cur.lastrowid or 0)


@pytest.fixture
def conn(tmp_path: Path):
    db_path = tmp_path / "test.db"
    c = _init_db(db_path)
    try:
        yield c
    finally:
        c.close()


def _seed_simple_session(
    conn: sqlite3.Connection,
    session_id: str = "sess-1",
    cwd: str = "/work/a",
) -> None:
    """Seed a session with 3 growing requests (stateless API replay).

    Messages:
      0: user "how do I implement widget frobbing"
      1: assistant "use the frobulator API"
      2: user "show me an example with cache_creation"
      3: assistant "here: <long example>"
    """
    _insert_session(conn, session_id, cwd=cwd)
    # Request 1 — just message 0.
    _insert_request(
        conn,
        session_id=session_id,
        messages=[
            {"role": "user", "content": "how do I implement widget frobbing"},
        ],
        timestamp="2026-04-20T12:00:00",
    )
    # Request 2 — messages 0, 1, 2.
    _insert_request(
        conn,
        session_id=session_id,
        messages=[
            {"role": "user", "content": "how do I implement widget frobbing"},
            {"role": "assistant", "content": "use the frobulator API"},
            {"role": "user", "content": "show me an example with cache_creation"},
        ],
        timestamp="2026-04-20T12:01:00",
    )
    # Request 3 — messages 0, 1, 2, 3 (the canonical transcript).
    _insert_request(
        conn,
        session_id=session_id,
        messages=[
            {"role": "user", "content": "how do I implement widget frobbing"},
            {"role": "assistant", "content": "use the frobulator API"},
            {"role": "user", "content": "show me an example with cache_creation"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "here: " + ("X" * 3000)},
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "run_example",
                        "input": {"code": "frobulate()"},
                    },
                ],
            },
        ],
        timestamp="2026-04-20T12:02:00",
    )
    # Backfill FTS index against all inserted blobs.
    fts_ensure_schema(conn)
    fts_backfill(conn)


# ------------------------ search_transcripts_impl ------------------------


def test_search_returns_hit_with_turn_offset(conn: sqlite3.Connection) -> None:
    _seed_simple_session(conn)
    hits = search_transcripts_impl(conn, "frobulator")
    assert len(hits) == 1
    hit = hits[0]
    assert hit["session_id"] == "sess-1"
    assert hit["total_turns"] == 4
    # "frobulator" first appears in message index 1 (assistant response).
    assert hit["first_hit_turn"] == 1
    assert hit["hit_count"] >= 1
    assert hit["cwd"] == "/work/a"


def test_search_cwd_filter_excludes_other_sessions(conn: sqlite3.Connection) -> None:
    _seed_simple_session(conn, session_id="match", cwd="/work/target")
    _seed_simple_session(conn, session_id="other", cwd="/work/elsewhere")
    hits = search_transcripts_impl(conn, "frobulator", cwd="/work/target")
    assert [h["session_id"] for h in hits] == ["match"]


def test_search_since_filter(conn: sqlite3.Connection) -> None:
    _seed_simple_session(conn)
    # Sessions in the seed go up to 2026-04-20T12:02:00. Filter after that.
    hits = search_transcripts_impl(conn, "frobulator", since=datetime(2026, 4, 21))
    assert hits == []


def test_search_empty_query_returns_nothing(conn: sqlite3.Connection) -> None:
    _seed_simple_session(conn)
    assert search_transcripts_impl(conn, "") == []


def test_search_limit_capped(conn: sqlite3.Connection) -> None:
    _seed_simple_session(conn)
    # Even asking for 10000, we never return more than MAX_SEARCH_LIMIT.
    hits = search_transcripts_impl(conn, "frobulator", limit=10000)
    assert len(hits) <= 100


# ------------------------ get_session_window_impl ------------------------


def test_window_returns_bounded_slice(conn: sqlite3.Connection) -> None:
    _seed_simple_session(conn)
    out = get_session_window_impl(conn, "sess-1", start_turn=0, limit=2)
    assert out["total_turns"] == 4
    assert len(out["turns"]) == 2
    assert out["turns"][0]["index"] == 0
    assert out["turns"][0]["role"] == "user"
    assert "frobbing" in out["turns"][0]["text"]
    assert out["turns"][1]["index"] == 1
    assert out["turns"][1]["role"] == "assistant"


def test_window_start_past_end_is_empty(conn: sqlite3.Connection) -> None:
    _seed_simple_session(conn)
    out = get_session_window_impl(conn, "sess-1", start_turn=99, limit=5)
    assert out["turns"] == []
    assert out["total_turns"] == 4


def test_window_truncates_long_text_by_default(conn: sqlite3.Connection) -> None:
    _seed_simple_session(conn)
    # Turn 3 has 3000 X's + a tool_call; default truncate should chop it.
    out = get_session_window_impl(conn, "sess-1", start_turn=3, limit=1)
    assert len(out["turns"]) == 1
    turn = out["turns"][0]
    assert "[truncated" in turn["text"]
    assert turn["tool_calls"] == [
        {"name": "run_example", "input": {"code": "frobulate()"}}
    ]


def test_window_truncate_false_returns_full_text(conn: sqlite3.Connection) -> None:
    _seed_simple_session(conn)
    out = get_session_window_impl(conn, "sess-1", start_turn=3, limit=1, truncate=False)
    turn = out["turns"][0]
    assert "[truncated" not in turn["text"]
    assert turn["text"].count("X") == 3000


def test_window_rejects_limit_over_cap(conn: sqlite3.Connection) -> None:
    _seed_simple_session(conn)
    with pytest.raises(ValueError):
        get_session_window_impl(
            conn, "sess-1", start_turn=0, limit=MAX_WINDOW_LIMIT + 1
        )


def test_window_rejects_zero_limit(conn: sqlite3.Connection) -> None:
    _seed_simple_session(conn)
    with pytest.raises(ValueError):
        get_session_window_impl(conn, "sess-1", start_turn=0, limit=0)


def test_window_rejects_negative_start(conn: sqlite3.Connection) -> None:
    _seed_simple_session(conn)
    with pytest.raises(ValueError):
        get_session_window_impl(conn, "sess-1", start_turn=-1, limit=5)


def test_window_unknown_session_raises(conn: sqlite3.Connection) -> None:
    with pytest.raises(KeyError):
        get_session_window_impl(conn, "does-not-exist", start_turn=0, limit=5)


def test_search_excludes_session(conn: sqlite3.Connection) -> None:
    _seed_simple_session(conn, session_id="own")
    _seed_simple_session(conn, session_id="other", cwd="/work/b")
    # Without exclusion both sessions surface.
    hits = search_transcripts_impl(conn, "frobulator")
    assert {h["session_id"] for h in hits} == {"own", "other"}
    # With exclusion, "own" drops out.
    hits = search_transcripts_impl(conn, "frobulator", exclude_session_id="own")
    assert [h["session_id"] for h in hits] == ["other"]


def test_mcp_caller_record_and_lookup_with_defaults() -> None:
    """Proxy sees only explicit keys; MCP handler sees defaults filled in.

    Normalization should produce the same hash so the caller can be identified.
    """
    mcp_caller.clear()
    # Proxy side: model emitted just {"query": "x"} in tool_use input_json.
    mcp_caller.record("search_transcripts", {"query": "x"}, "sess-A")
    # MCP side: FastMCP fills in limit=20, cwd=None, since=None before calling us.
    found = mcp_caller.lookup(
        "search_transcripts",
        {"query": "x", "limit": 20, "cwd": None, "since": None},
    )
    assert found == "sess-A"


def test_mcp_caller_non_consuming_supports_parallel_calls() -> None:
    """Parallel identical tool_use blocks all land on the same entry."""
    mcp_caller.clear()
    mcp_caller.record("search_transcripts", {"query": "x"}, "sess-A")
    args = {"query": "x", "limit": 20, "cwd": None, "since": None}
    # Three concurrent MCP handler calls with identical args (parallel
    # tool_use from one model response) all find the caller.
    assert mcp_caller.lookup("search_transcripts", args) == "sess-A"
    assert mcp_caller.lookup("search_transcripts", args) == "sess-A"
    assert mcp_caller.lookup("search_transcripts", args) == "sess-A"


def test_mcp_caller_different_args_do_not_collide() -> None:
    mcp_caller.clear()
    mcp_caller.record("search_transcripts", {"query": "foo"}, "sess-A")
    mcp_caller.record("search_transcripts", {"query": "bar"}, "sess-B")
    assert (
        mcp_caller.lookup(
            "search_transcripts",
            {"query": "foo", "limit": 20, "cwd": None, "since": None},
        )
        == "sess-A"
    )
    assert (
        mcp_caller.lookup(
            "search_transcripts",
            {"query": "bar", "limit": 20, "cwd": None, "since": None},
        )
        == "sess-B"
    )


def test_mcp_caller_ttl_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    """Entries older than `_TTL_SECONDS` must not be returned by lookup."""
    mcp_caller.clear()
    t = [1000.0]
    monkeypatch.setattr(mcp_caller.time, "monotonic", lambda: t[0])
    mcp_caller.record("search_transcripts", {"query": "stale"}, "sess-A")
    # Advance just past the TTL.
    t[0] += mcp_caller._TTL_SECONDS + 1.0
    assert (
        mcp_caller.lookup(
            "search_transcripts",
            {"query": "stale", "limit": 20, "cwd": None, "since": None},
        )
        is None
    )
    # And the stale entry was dropped on lookup (no leak).
    assert len(mcp_caller._cache) == 0


def test_mcp_caller_lru_cap_enforced() -> None:
    """Beyond `_MAX_ENTRIES` records, oldest entries get evicted."""
    mcp_caller.clear()
    cap = mcp_caller._MAX_ENTRIES
    # Record cap+10 distinct entries; first 10 should be gone.
    for i in range(cap + 10):
        mcp_caller.record("search_transcripts", {"query": f"q{i}"}, f"sess-{i}")
    assert len(mcp_caller._cache) <= cap
    # The earliest entry should have been evicted.
    assert (
        mcp_caller.lookup(
            "search_transcripts",
            {"query": "q0", "limit": 20, "cwd": None, "since": None},
        )
        is None
    )
    # The latest should still be present.
    assert (
        mcp_caller.lookup(
            "search_transcripts",
            {"query": f"q{cap + 9}", "limit": 20, "cwd": None, "since": None},
        )
        == f"sess-{cap + 9}"
    )


def test_mcp_caller_malformed_arguments_dont_crash() -> None:
    """Non-dict arguments (shouldn't happen from proxy, but be defensive)
    must not raise — cache miss is the correct behavior."""
    mcp_caller.clear()
    # Not a dict — goes through _normalize's fallback path.
    mcp_caller.record("search_transcripts", "notadict", "sess-A")  # type: ignore[arg-type]
    # Lookup with dict args won't match the non-dict fallback hash.
    assert (
        mcp_caller.lookup(
            "search_transcripts",
            {"query": "x", "limit": 20, "cwd": None, "since": None},
        )
        is None
    )


def test_search_transcripts_handler_uses_mcp_caller(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The FastMCP-registered search_transcripts handler must call
    `mcp_caller.lookup` and pass the returned session_id to the impl as
    `exclude_session_id`. Guards against handler wiring regressions."""
    from cctape.mcp_server import build_mcp

    _seed_simple_session(conn, session_id="caller-sess")
    _seed_simple_session(conn, session_id="other-sess", cwd="/work/b")

    mcp_caller.clear()
    # Pretend the proxy already saw caller-sess emit a matching tool_use.
    mcp_caller.record("search_transcripts", {"query": "frobulator"}, "caller-sess")

    mcp, _ = build_mcp(lambda: conn)
    registered = mcp._tool_manager.get_tool("search_transcripts")
    assert registered is not None
    hits = registered.fn(query="frobulator")
    ids = {h["session_id"] for h in hits}
    # caller-sess must be excluded from its own results.
    assert "caller-sess" not in ids
    assert "other-sess" in ids


def test_window_exclude_tool_results(conn: sqlite3.Connection) -> None:
    _insert_session(conn, "s", cwd="/x")
    _insert_request(
        conn,
        session_id="s",
        messages=[
            {"role": "user", "content": "go"},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "echo",
                        "input": {"x": 1},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "content": "result text",
                    },
                    {"type": "text", "text": "ok continue"},
                ],
            },
        ],
        timestamp="2026-04-20T12:00:00",
    )
    fts_ensure_schema(conn)
    fts_backfill(conn)

    out = get_session_window_impl(
        conn, "s", start_turn=0, limit=5, include_tool_results=False
    )
    # Third turn's tool_result is filtered; only the trailing text remains.
    third = out["turns"][2]
    assert "result text" not in third["text"]
    assert "ok continue" in third["text"]

    out_with = get_session_window_impl(
        conn, "s", start_turn=0, limit=5, include_tool_results=True
    )
    assert "result text" in out_with["turns"][2]["text"]
