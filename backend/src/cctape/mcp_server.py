"""MCP server: exposes cctape's archive over Model Context Protocol.

Mounted on the main FastAPI app at `/mcp` via the streamable-HTTP transport.
Tools are thin wrappers over the pure impls in `mcp_tools.py`.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any, Callable

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette

from . import mcp_caller
from .mcp_tools import (
    MAX_SEARCH_LIMIT,
    MAX_WINDOW_LIMIT,
    get_session_window_impl,
    search_transcripts_impl,
)

ConnGetter = Callable[[], sqlite3.Connection]


def build_mcp(conn_getter: ConnGetter) -> tuple[FastMCP, Starlette]:
    """Construct the FastMCP instance and its mountable Starlette ASGI app.

    `conn_getter` is called on every tool invocation to fetch the shared
    sqlite3 connection from the FastAPI app state. A getter (rather than a
    direct connection) keeps this module importable before the lifespan has
    set up the connection, and makes unit testing trivial.

    Instructions, descriptions, and parameter docs here are user-facing — they
    appear to the AI agent consuming the MCP server and shape how it uses the
    tools. Be explicit about windowing.
    """
    # streamable_http_path='/' so we can mount at /mcp and the full URL stays
    # /mcp (rather than /mcp-server/mcp).
    mcp = FastMCP(
        "cctape",
        instructions=(
            "Search and read cctape's archive of past Claude Code sessions. "
            "Always start with `search_transcripts` to locate a session and a "
            "`first_hit_turn` offset, then read a small window with "
            "`get_session_window`. Never request more than a few dozen turns "
            "at once — full sessions can be thousands of messages."
        ),
        streamable_http_path="/",
    )

    @mcp.tool()
    def search_transcripts(
        query: str,
        limit: int = 20,
        cwd: str | None = None,
        since: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search archived Claude Code sessions by text.

        Returns session-level hits ranked by FTS bm25. Each hit includes
        `first_hit_turn` (where to start reading) and `total_turns` (the size
        of the full transcript, for picking a window). `snippet` is a short
        excerpt with <mark> tags around matched terms.

        Query semantics: whitespace-separated tokens are OR'd with prefix
        matching. A session ranks higher when more of your tokens co-occur
        in the same message. Pick 3-6 descriptive keywords — operators in
        the input are treated literally, so don't quote or add AND/OR. If a
        search returns no hits, the archive genuinely does not contain
        those terms; iterating through synonyms rarely helps.

        Args:
            query: Search terms. Example: "proxy deadlock migration" finds
                blobs containing any of those prefixes, ranked by overlap.
            limit: Max results to return (default 20, max 100).
            cwd: Optional filter — only return sessions whose working
                directory matches exactly.
            since: Optional ISO-8601 datetime — only return sessions active
                after this instant.
        """
        since_dt: datetime | None = None
        if since is not None:
            try:
                since_dt = datetime.fromisoformat(since)
            except ValueError as exc:
                raise ValueError(f"invalid `since` (expected ISO-8601): {exc}") from exc
        limit = max(1, min(int(limit), MAX_SEARCH_LIMIT))
        exclude = mcp_caller.lookup(
            "search_transcripts",
            {"query": query, "limit": limit, "cwd": cwd, "since": since},
        )
        return search_transcripts_impl(
            conn_getter(),
            query,
            limit=limit,
            cwd=cwd,
            since=since_dt,
            exclude_session_id=exclude,
        )

    @mcp.tool()
    def get_session_window(
        session_id: str,
        start_turn: int,
        limit: int,
        include_tool_results: bool = True,
        truncate: bool = True,
    ) -> dict[str, Any]:
        """Read a bounded slice of a session's transcript.

        Turns are flattened messages — `{index, role, text, tool_calls}` —
        where `text` concatenates text blocks (and tool results when
        included), and `tool_calls` lists `tool_use` blocks. `thinking` blocks
        are dropped.

        Args:
            session_id: Session identifier, e.g. from `search_transcripts`.
            start_turn: 0-based turn index to start at. Use `first_hit_turn`
                from a search result (backing off a few turns for context).
            limit: Number of turns to return (required, max 50).
            include_tool_results: When true, fold tool_result blocks into the
                containing turn's `text`. When false, drop them entirely —
                useful for scanning conversation flow without tool noise.
            truncate: When true (default), cap long text blocks at 2000 chars
                with a `[truncated N chars]` marker. Set false if you need
                the full text of a specific tool result.
        """
        if limit < 1:
            raise ValueError("limit must be >= 1")
        if limit > MAX_WINDOW_LIMIT:
            raise ValueError(f"limit exceeds max ({MAX_WINDOW_LIMIT})")
        try:
            return get_session_window_impl(
                conn_getter(),
                session_id,
                start_turn=start_turn,
                limit=limit,
                include_tool_results=include_tool_results,
                truncate=truncate,
            )
        except KeyError as exc:
            raise ValueError(str(exc)) from exc

    return mcp, mcp.streamable_http_app()
