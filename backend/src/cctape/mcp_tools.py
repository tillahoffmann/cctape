"""Pure-Python implementations of the MCP tools.

Kept separate from `mcp_server.py` so they can be unit-tested against a plain
sqlite3 connection without going through the MCP transport. The MCP handlers
in `mcp_server.py` are thin wrappers over these functions.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from .fts import _to_fts_query, search_sessions
from .storage import _split_hashes, decompress, reconstruct_payload

# Hard caps enforced server-side. Agents must choose an explicit window; the
# caps protect them from blowing their own context on one bad call.
MAX_SEARCH_LIMIT = 100
MAX_WINDOW_LIMIT = 50
DEFAULT_TRUNCATE_CHARS = 2000


def _last_request_hashes(
    conn: sqlite3.Connection, session_id: str
) -> tuple[bytes | None, bytes | None] | None:
    """Return (message_hashes, payload) for the last request in a session.

    The Anthropic API is stateless: each request body contains the whole
    conversation-so-far in `messages`. The LAST request therefore carries the
    most complete transcript — that's what we use as the "turn list" of the
    session. Returns None if the session has no requests.
    """
    row = conn.execute(
        "SELECT message_hashes, payload FROM requests "
        "WHERE session_id = ? ORDER BY timestamp DESC, id DESC LIMIT 1",
        (session_id,),
    ).fetchone()
    return row


def _turn_count(conn: sqlite3.Connection, session_id: str) -> int:
    """Number of messages in the most-recent request's `messages` list.

    Equivalent to the count of visible turns in the session transcript.
    Falls back to 0 if the session has no parseable requests.
    """
    row = _last_request_hashes(conn, session_id)
    if row is None:
        return 0
    message_hashes, payload = row
    hashes = _split_hashes(message_hashes)
    if hashes:
        return len(hashes)
    # Legacy / unparseable row: reconstruct to count.
    if payload is None:
        return 0
    try:
        body = json.loads(decompress(payload))
    except (ValueError, OSError):
        return 0
    if isinstance(body, dict):
        msgs = body.get("messages") or []
        return len(msgs) if isinstance(msgs, list) else 0
    return 0


def _first_hit_turn(
    conn: sqlite3.Connection, session_id: str, digests: list[bytes]
) -> int | None:
    """Find the first turn index in the session's transcript containing any of `digests`.

    "Turn index" = offset into the last request's `messages` list (the
    canonical transcript). Returns None if no digest matches.
    """
    if not digests:
        return None
    row = _last_request_hashes(conn, session_id)
    if row is None:
        return None
    message_hashes, _ = row
    hashes = _split_hashes(message_hashes)
    if not hashes:
        return None
    digest_set = set(digests)
    for idx, h in enumerate(hashes):
        if h in digest_set:
            return idx
    return None


def search_transcripts_impl(
    conn: sqlite3.Connection,
    query: str,
    limit: int = 20,
    cwd: str | None = None,
    since: datetime | None = None,
    exclude_session_id: str | None = None,
) -> list[dict[str, Any]]:
    """Search archived transcripts. Returns session-level hits with jump-off info.

    `exclude_session_id` filters out a specific session — used to hide the
    caller's own current session from its own search results.
    """
    limit = max(1, min(int(limit), MAX_SEARCH_LIMIT))
    # Overfetch so post-filters still leave `limit` results where possible.
    raw_limit = limit
    if cwd is not None or since is not None or exclude_session_id is not None:
        raw_limit = min(limit * 4, MAX_SEARCH_LIMIT * 4)
    raw = search_sessions(conn, query, limit=raw_limit)

    # Need blob digests to compute first_hit_turn. search_sessions already
    # looked them up but doesn't return them, so re-derive via the FTS5 MATCH
    # per session. Simpler alternative: ask search_sessions for the best
    # digest — but that would widen its contract. Instead, find ALL matching
    # blobs for each session and hand them to _first_hit_turn.
    fts_query = _to_fts_query(query)
    digest_rows = (
        conn.execute(
            """
            SELECT sb.session_id, fh.hash
            FROM blob_fts f
            JOIN fts_hash fh ON fh.rowid = f.rowid
            JOIN session_blobs sb ON sb.hash = fh.hash
            WHERE blob_fts MATCH :q
            """,
            {"q": fts_query},
        ).fetchall()
        if fts_query
        else []
    )
    digests_by_session: dict[str, list[bytes]] = {}
    for session_id, digest in digest_rows:
        digests_by_session.setdefault(session_id, []).append(digest)

    # Pull session metadata (started_at, last_timestamp) for filtering and output.
    if raw:
        session_ids = [r["session_id"] for r in raw]
        placeholders = ",".join("?" * len(session_ids))
        meta_rows = conn.execute(
            f"""
            SELECT s.session_id, s.started_at,
                   (SELECT MAX(timestamp) FROM requests WHERE session_id = s.session_id)
                       AS last_timestamp
            FROM sessions s
            WHERE s.session_id IN ({placeholders})
            """,
            session_ids,
        ).fetchall()
        meta = {sid: (started_at, last_ts) for sid, started_at, last_ts in meta_rows}
    else:
        meta = {}

    def _dt(value: Any) -> datetime | None:
        if value is None or isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return None

    results: list[dict[str, Any]] = []
    for row in raw:
        session_id = row["session_id"]
        if exclude_session_id is not None and session_id == exclude_session_id:
            continue
        if cwd is not None and row.get("cwd") != cwd:
            continue
        started_at, last_ts = meta.get(session_id, (None, None))
        last_dt = _dt(last_ts)
        if since is not None and (last_dt is None or last_dt <= since):
            continue
        results.append(
            {
                "session_id": session_id,
                "snippet": row["snippet"],
                "hit_count": row["hit_count"],
                "first_hit_turn": _first_hit_turn(
                    conn, session_id, digests_by_session.get(session_id, [])
                ),
                "total_turns": _turn_count(conn, session_id),
                "title": row["title"],
                "cwd": row["cwd"],
                "git_branch": row["git_branch"],
                "started_at": _dt(started_at),
            }
        )
        if len(results) >= limit:
            break
    return results


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    dropped = len(text) - max_chars
    return text[:max_chars] + f"…[truncated {dropped} chars]"


def _flatten_message(
    message: Any,
    *,
    include_tool_results: bool,
    truncate: bool,
) -> dict[str, Any] | None:
    """Flatten an Anthropic message into {role, text, tool_calls}.

    Returns None if the message has no meaningful content after filtering.
    `text` is the concatenation of text blocks and (optionally) tool_result
    bodies separated by delimiters. `tool_calls` is a list of
    {name, input} dicts. `thinking` blocks are dropped.
    """
    if not isinstance(message, dict):
        return None
    role = message.get("role")
    content = message.get("content")
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []

    def _append_text(s: str) -> None:
        if not s:
            return
        text_parts.append(_truncate(s, DEFAULT_TRUNCATE_CHARS) if truncate else s)

    if isinstance(content, str):
        _append_text(content)
    elif isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                t = block.get("text")
                if isinstance(t, str):
                    _append_text(t)
            elif btype == "tool_use":
                tool_calls.append(
                    {"name": block.get("name"), "input": block.get("input")}
                )
            elif btype == "tool_result":
                if not include_tool_results:
                    continue
                # Tool results can be a string or a list of text/image blocks.
                inner = block.get("content", "")
                inner_text = ""
                if isinstance(inner, str):
                    inner_text = inner
                elif isinstance(inner, list):
                    pieces: list[str] = []
                    for sub in inner:
                        if isinstance(sub, dict) and sub.get("type") == "text":
                            st = sub.get("text")
                            if isinstance(st, str):
                                pieces.append(st)
                    inner_text = "\n".join(pieces)
                if inner_text:
                    tid = block.get("tool_use_id")
                    header = f"\n[tool_result{f' tool_use_id={tid}' if tid else ''}]\n"
                    _append_text(header + inner_text)
            # thinking, image, etc. intentionally dropped.

    if not text_parts and not tool_calls:
        return None
    return {
        "role": role,
        "text": "\n".join(p for p in text_parts if p),
        "tool_calls": tool_calls,
    }


def get_session_window_impl(
    conn: sqlite3.Connection,
    session_id: str,
    start_turn: int,
    limit: int,
    include_tool_results: bool = True,
    truncate: bool = True,
) -> dict[str, Any]:
    """Return a bounded slice of a session's turns.

    Raises KeyError if the session doesn't exist. Returns an empty turns list
    (with accurate `total_turns`) if `start_turn` is past the end.
    """
    if limit < 1:
        raise ValueError("limit must be >= 1")
    if limit > MAX_WINDOW_LIMIT:
        raise ValueError(f"limit exceeds max ({MAX_WINDOW_LIMIT})")
    if start_turn < 0:
        raise ValueError("start_turn must be >= 0")

    meta_row = conn.execute(
        "SELECT cwd, git_branch, title, started_at FROM sessions WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    (request_count,) = conn.execute(
        "SELECT COUNT(*) FROM requests WHERE session_id = ?", (session_id,)
    ).fetchone()
    if meta_row is None and request_count == 0:
        raise KeyError(f"session not found: {session_id}")
    total_turns = _turn_count(conn, session_id)
    cwd, git_branch, title, started_at = (
        meta_row if meta_row else (None, None, None, None)
    )

    if isinstance(started_at, str):
        try:
            started_at = datetime.fromisoformat(started_at)
        except ValueError:
            started_at = None

    # Pull the last request — its `messages` is the canonical transcript.
    last = conn.execute(
        """
        SELECT system_hash, tools_hash, message_hashes, extras, payload
        FROM requests
        WHERE session_id = ?
        ORDER BY timestamp DESC, id DESC
        LIMIT 1
        """,
        (session_id,),
    ).fetchone()

    turns: list[dict[str, Any]] = []
    if last is not None:
        system_hash, tools_hash, message_hashes, extras, payload = last
        try:
            body = reconstruct_payload(
                conn, system_hash, tools_hash, message_hashes, extras, payload
            )
        except (ValueError, KeyError):
            body = None
        messages = body.get("messages") if isinstance(body, dict) else None
        if isinstance(messages, list):
            window = messages[start_turn : start_turn + limit]
            for offset, message in enumerate(window):
                flattened = _flatten_message(
                    message,
                    include_tool_results=include_tool_results,
                    truncate=truncate,
                )
                if flattened is None:
                    continue
                flattened["index"] = start_turn + offset
                turns.append(flattened)

    return {
        "turns": turns,
        "total_turns": total_turns,
        "session": {
            "cwd": cwd,
            "git_branch": git_branch,
            "title": title,
            "started_at": started_at,
        },
    }
