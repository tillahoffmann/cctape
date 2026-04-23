"""End-to-end tests for the MCP self-exclusion correlation mechanism.

These tests drive fake Anthropic SSE responses through the real proxy streaming
path, then check that `mcp_caller` is populated correctly. The four bugs these
guard against:

1. **Hash normalization drift** — proxy and MCP handler must produce the same
   hash. Covered at unit level in test_mcp_tools.py plus a drift guard here.
2. **Consume-on-lookup** — parallel tool_use blocks with identical args all
   need to find the same cache entry. Here: real proxy + real args.
3. **`finally:` timing** — tool_use must be recorded BEFORE the chunk
   containing `content_block_stop` is yielded to the client, because the
   client fires the MCP call the moment it reads that event.
4. **SSE line splitting** — chunk boundaries landing on or around `\\n`
   must not corrupt the inline decoder.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Generator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from cctape import create_app, mcp_caller
from cctape.mcp_caller import _TOOL_DEFAULTS
from cctape.mcp_server import build_mcp


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "cctape.db"


@pytest.fixture(autouse=True)
def _reset_cache():
    mcp_caller.clear()
    yield
    mcp_caller.clear()


@pytest.fixture
def client(db_path: Path) -> Generator[TestClient, None, None]:
    with patch.dict(os.environ, {"CCTAPE_DB": str(db_path)}):
        with TestClient(create_app()) as c:
            yield c


# ---------------------------------------------------------------------------
# SSE stream builders
# ---------------------------------------------------------------------------


def _sse(events: list[tuple[str, dict[str, Any]]]) -> bytes:
    """Serialize a sequence of (event_name, data_dict) pairs to SSE bytes."""
    out = []
    for name, data in events:
        out.append(f"event: {name}\ndata: {json.dumps(data)}\n\n")
    return "".join(out).encode()


def _tool_use_stream(
    blocks: list[tuple[int, str, dict[str, Any]]],
    *,
    delta_split_size: int | None = None,
) -> bytes:
    """Build an SSE stream with one or more tool_use content blocks.

    Each tuple is (index, tool_name, arguments_dict). The arguments' JSON is
    optionally split into `delta_split_size`-byte chunks across multiple
    `input_json_delta` events, exercising the accumulator path.
    """
    events: list[tuple[str, dict[str, Any]]] = [
        (
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "model": "claude-opus-4-7",
                    "id": "msg_x",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "stop_reason": None,
                    "usage": {},
                },
            },
        ),
    ]
    for idx, name, args in blocks:
        events.append(
            (
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": idx,
                    "content_block": {
                        "type": "tool_use",
                        "id": f"toolu_{idx:02d}",
                        "name": name,
                        "input": {},
                    },
                },
            )
        )
        payload = json.dumps(args)
        if delta_split_size and delta_split_size > 0:
            pieces = [
                payload[i : i + delta_split_size]
                for i in range(0, len(payload), delta_split_size)
            ]
        else:
            pieces = [payload]
        for piece in pieces:
            events.append(
                (
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": idx,
                        "delta": {"type": "input_json_delta", "partial_json": piece},
                    },
                )
            )
        events.append(
            (
                "content_block_stop",
                {"type": "content_block_stop", "index": idx},
            )
        )
    events.append(
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use"},
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )
    )
    events.append(("message_stop", {"type": "message_stop"}))
    return _sse(events)


def _make_streaming_response(
    body: bytes, chunks: list[bytes] | None = None
) -> Response:
    """Build an httpx.Response whose `aiter_bytes` yields controlled chunks.

    If `chunks` is provided, those bytes are yielded in order. Otherwise the
    body is delivered in one chunk.
    """
    r = Response(
        200,
        content=b"",
        headers={
            "date": "Sat, 18 Apr 2026 19:56:51 GMT",
            "anthropic-organization-id": "org-test",
        },
    )
    pieces = chunks if chunks is not None else [body]

    async def _aiter(chunk_size: int | None = None) -> AsyncIterator[bytes]:
        for p in pieces:
            yield p

    r.aiter_bytes = _aiter  # type: ignore[assignment]
    return r


def _sample_request_body() -> bytes:
    # Minimal valid /v1/messages body. Parseable so the dedup path runs.
    return json.dumps(
        {
            "model": "claude-opus-4-7",
            "messages": [{"role": "user", "content": "hi"}],
        }
    ).encode()


def _post_proxy(
    client: TestClient,
    body_stream: bytes,
    *,
    chunks: list[bytes] | None = None,
    session_id: str | None = "sess-1",
) -> None:
    async def _send(*args, **kwargs):
        return _make_streaming_response(body_stream, chunks=chunks)

    headers = {}
    if session_id is not None:
        headers["x-claude-code-session-id"] = session_id
    with patch("httpx._client.AsyncClient.send", side_effect=_send):
        resp = client.post(
            "/proxy/v1/messages", content=_sample_request_body(), headers=headers
        )
        # Drain the body so the streaming generator runs its body() async-for
        # loop (and the `finally:` block). TestClient auto-drains, but be
        # explicit in case of future changes.
        assert resp.status_code == 200
        _ = resp.content


# ---------------------------------------------------------------------------
# End-to-end: proxy records tool_use via inline SSE parsing
# ---------------------------------------------------------------------------


def test_proxy_records_mcp_tool_use_inline(client: TestClient) -> None:
    stream = _tool_use_stream(
        [
            (0, "mcp__cctape__search_transcripts", {"query": "foo"}),
        ]
    )
    _post_proxy(client, stream)
    found = mcp_caller.lookup(
        "search_transcripts",
        {"query": "foo", "limit": 20, "cwd": None, "since": None},
    )
    assert found == "sess-1"


def test_proxy_ignores_non_cctape_tool_use(client: TestClient) -> None:
    """A Bash tool_use (not our MCP tool) must not populate the cache."""
    stream = _tool_use_stream(
        [
            (0, "Bash", {"command": "ls"}),
        ]
    )
    _post_proxy(client, stream)
    # Nothing should have been recorded.
    assert len(mcp_caller._cache) == 0


def test_proxy_tool_use_without_session_id_not_recorded(
    client: TestClient,
) -> None:
    """If the inbound request has no session header, record() is a no-op."""
    stream = _tool_use_stream(
        [
            (0, "mcp__cctape__search_transcripts", {"query": "foo"}),
        ]
    )
    _post_proxy(client, stream, session_id=None)
    assert len(mcp_caller._cache) == 0


# ---------------------------------------------------------------------------
# Bug #2: parallel tool_use blocks in one response
# ---------------------------------------------------------------------------


def test_proxy_parallel_tool_use_distinct_args(client: TestClient) -> None:
    """3 parallel tool_uses with DIFFERENT args each get their own entry,
    and all three lookups succeed (non-consuming cache)."""
    stream = _tool_use_stream(
        [
            (0, "mcp__cctape__search_transcripts", {"query": "a"}),
            (1, "mcp__cctape__search_transcripts", {"query": "b"}),
            (2, "mcp__cctape__search_transcripts", {"query": "c"}),
        ]
    )
    _post_proxy(client, stream)
    for q in ("a", "b", "c"):
        found = mcp_caller.lookup(
            "search_transcripts",
            {"query": q, "limit": 20, "cwd": None, "since": None},
        )
        assert found == "sess-1", f"lookup for {q!r} missed"


def test_proxy_parallel_tool_use_identical_args(client: TestClient) -> None:
    """3 parallel tool_uses with IDENTICAL args all resolve — non-consuming
    lookup is the key regression check for bug #2."""
    stream = _tool_use_stream(
        [
            (0, "mcp__cctape__search_transcripts", {"query": "dup"}),
            (1, "mcp__cctape__search_transcripts", {"query": "dup"}),
            (2, "mcp__cctape__search_transcripts", {"query": "dup"}),
        ]
    )
    _post_proxy(client, stream)
    # Three repeated lookups must all find the entry.
    args = {"query": "dup", "limit": 20, "cwd": None, "since": None}
    assert mcp_caller.lookup("search_transcripts", args) == "sess-1"
    assert mcp_caller.lookup("search_transcripts", args) == "sess-1"
    assert mcp_caller.lookup("search_transcripts", args) == "sess-1"


# ---------------------------------------------------------------------------
# Bug #3: timing — record must happen BEFORE chunk yielded to client
# ---------------------------------------------------------------------------


def test_proxy_records_during_stream_not_only_in_finally(
    client: TestClient,
) -> None:
    """Tool_use must be recorded as its `content_block_stop` arrives mid-stream,
    not deferred to the stream-close `finally:` block.

    Why this matters: the downstream client fires the MCP tool call the
    instant it reads `content_block_stop` from the proxied body. If recording
    only happens in `finally:` (after the whole stream closes), the MCP
    handler's lookup races against a still-empty cache.

    We test this by making the upstream stream hang AFTER the stop-event
    chunk but before end-of-stream. If recording is inline, it fires during
    the hang and `mcp_caller.lookup` succeeds. If recording is finally-only,
    the lookup fails because `finally:` has not yet run.
    """
    stream = _tool_use_stream(
        [
            (0, "mcp__cctape__search_transcripts", {"query": "timing"}),
        ]
    )
    stop_marker = b"event: content_block_stop"
    split = stream.index(stop_marker)
    # Find end of the stop event (the `\n\n` terminator after it).
    stop_end = stream.index(b"\n\n", split) + 2
    # Three chunks: pre-stop, stop-event-only, trailing.
    pre_chunk = stream[:split]
    stop_chunk = stream[split:stop_end]
    trail_chunk = stream[stop_end:]

    # Upstream holds this event between delivering `stop_chunk` and the
    # trailing chunk. While held, we assert the lookup already succeeds.
    pause = asyncio.Event()
    lookup_result: dict[str, Any] = {}

    async def _send(*args, **kwargs):
        r = Response(
            200,
            content=b"",
            headers={
                "date": "Sat, 18 Apr 2026 19:56:51 GMT",
                "anthropic-organization-id": "org-test",
            },
        )

        async def _aiter(chunk_size: int | None = None):
            yield pre_chunk
            yield stop_chunk
            # At this point the proxy's async-for has yielded both chunks
            # downstream. If record() was inline, the cache is populated
            # and lookup will succeed NOW. If record() only runs in
            # `finally:`, the cache is still empty.
            lookup_result["value"] = mcp_caller.lookup(
                "search_transcripts",
                {"query": "timing", "limit": 20, "cwd": None, "since": None},
            )
            pause.set()
            yield trail_chunk

        r.aiter_bytes = _aiter
        return r

    with patch("httpx._client.AsyncClient.send", side_effect=_send):
        resp = client.post(
            "/proxy/v1/messages",
            content=_sample_request_body(),
            headers={"x-claude-code-session-id": "sess-timing"},
        )
        assert resp.status_code == 200
        _ = resp.content

    assert lookup_result.get("value") == "sess-timing", (
        "Tool_use was not recorded inline — lookup failed while stream was "
        "still open. This means recording regressed to the `finally:` block, "
        "and the downstream MCP call will lose its caller attribution."
    )


# ---------------------------------------------------------------------------
# Bug #4: SSE chunk boundaries
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "chunk_size",
    [1, 7, 64, 500, 10_000],
    ids=lambda n: f"chunk_{n}",
)
def test_proxy_sse_chunk_boundaries(client: TestClient, chunk_size: int) -> None:
    """Split the SSE stream at varied byte sizes. All must produce the same
    three cache entries (one per tool_use) with correct args.

    This is the regression test for bug #4 — the naive `split("\\n")` on a
    block ending in `\\n` produced a trailing empty string, which SSEDecoder
    interpreted as a blank-line event terminator and prematurely fired a
    partial event. The symptom was that the second of three sequential
    tool_use blocks lost its `content_block_start` data. Multiple tool_uses
    are required to expose this; a single block is not enough.
    """
    stream = _tool_use_stream(
        [
            (0, "mcp__cctape__search_transcripts", {"query": "alpha"}),
            (1, "mcp__cctape__search_transcripts", {"query": "beta"}),
            (2, "mcp__cctape__search_transcripts", {"query": "gamma"}),
        ],
        delta_split_size=3,  # also split each input_json across many deltas
    )
    chunks = [stream[i : i + chunk_size] for i in range(0, len(stream), chunk_size)]
    _post_proxy(client, stream, chunks=chunks)
    for q in ("alpha", "beta", "gamma"):
        found = mcp_caller.lookup(
            "search_transcripts",
            {"query": q, "limit": 20, "cwd": None, "since": None},
        )
        assert found == "sess-1", (
            f"chunk_size={chunk_size}: lookup for query={q!r} missed — "
            f"SSE chunk-boundary parsing is broken"
        )


def test_proxy_sse_chunk_between_event_and_data(client: TestClient) -> None:
    """Pathological boundary that triggered bug #4 in production: a chunk
    ends immediately after `event: content_block_start\\n` and before the
    corresponding `data: ...` line. A naive `split("\\n")` on the block
    yields a trailing empty string, which SSEDecoder treats as an
    end-of-event blank-line — firing the event prematurely with no data.

    The next `data: ...` line then gets attached to the wrong event name,
    and the tool_use info is lost. The fix is to only feed complete
    lines (between real `\\n` boundaries) to the decoder.

    The check runs MID-STREAM so only the inline SSE parser can satisfy
    it — the `finally:` block has a defensive second decoder that would
    mask the inline bug if we checked post-stream.
    """
    stream = _tool_use_stream(
        [
            (0, "mcp__cctape__search_transcripts", {"query": "alpha"}),
            (1, "mcp__cctape__search_transcripts", {"query": "beta"}),
        ]
    )
    marker = b"event: content_block_start\n"
    first = stream.index(marker)
    second = stream.index(marker, first + len(marker))
    boundary = second + len(marker)  # cut right after the 2nd event: line's \n

    # Split so the FINAL content_block_stop has its own chunk boundary —
    # lets us check the cache between "stop event delivered" and "stream
    # closed" (which is when `finally:` would run).
    last_stop = stream.rindex(b"event: content_block_stop")
    last_stop_end = stream.index(b"\n\n", last_stop) + 2
    chunks = [
        stream[:boundary],
        stream[boundary:last_stop_end],
        stream[last_stop_end:],
    ]

    lookup_results: dict[str, Any] = {}

    async def _send(*args, **kwargs):
        r = Response(
            200,
            content=b"",
            headers={
                "date": "Sat, 18 Apr 2026 19:56:51 GMT",
                "anthropic-organization-id": "org-test",
            },
        )

        async def _aiter(chunk_size: int | None = None):
            yield chunks[0]
            yield chunks[1]
            # Between last-stop chunk and trailing: finally: block hasn't
            # run yet. Only inline parsing could have populated cache.
            for q in ("alpha", "beta"):
                lookup_results[q] = mcp_caller.lookup(
                    "search_transcripts",
                    {"query": q, "limit": 20, "cwd": None, "since": None},
                )
            yield chunks[2]

        r.aiter_bytes = _aiter
        return r

    with patch("httpx._client.AsyncClient.send", side_effect=_send):
        resp = client.post(
            "/proxy/v1/messages",
            content=_sample_request_body(),
            headers={"x-claude-code-session-id": "sess-1"},
        )
        assert resp.status_code == 200
        _ = resp.content

    for q in ("alpha", "beta"):
        assert lookup_results.get(q) == "sess-1", (
            f"mid-stream lookup for {q!r} failed — inline SSE parser is "
            f"corrupting input at the between-event-and-data chunk boundary"
        )


# ---------------------------------------------------------------------------
# Bug #1: _TOOL_DEFAULTS drift guard
# ---------------------------------------------------------------------------


def test_tool_defaults_match_registered_signatures() -> None:
    """Every registered MCP tool's optional parameters must appear in
    `_TOOL_DEFAULTS` with matching defaults. Adding a tool or changing a
    default without updating the table silently breaks correlation — this
    test fails the build instead."""
    mcp, _ = build_mcp(lambda: None)  # type: ignore[arg-type]

    async def _collect():
        return await mcp.list_tools()

    tools = asyncio.run(_collect())
    tool_callables = {}
    for t in tools:
        registered = mcp._tool_manager.get_tool(t.name)
        assert registered is not None, f"tool {t.name!r} missing from manager"
        tool_callables[t.name] = registered.fn
    for name, fn in tool_callables.items():
        sig = inspect.signature(fn)
        expected = {}
        for param_name, param in sig.parameters.items():
            if param.default is inspect.Parameter.empty:
                continue  # required, not subject to default-fill
            expected[param_name] = param.default
        actual = _TOOL_DEFAULTS.get(name)
        assert actual is not None, (
            f"tool {name!r} is registered but missing from _TOOL_DEFAULTS in mcp_caller.py"
        )
        assert actual == expected, (
            f"tool {name!r} defaults drifted: signature has {expected}, "
            f"_TOOL_DEFAULTS has {actual}. "
            f"Update _TOOL_DEFAULTS in mcp_caller.py so proxy-side hashing "
            f"matches handler-side hashing."
        )
