"""Correlate inbound MCP tool calls back to the Claude Code session that made them.

When the proxy observes Anthropic emitting a `tool_use` for one of our own
`mcp__cctape__*` tools, it records a hash of the (tool_name, arguments) pair
alongside the session_id of the request that asked for it. When that MCP call
then arrives at our server, the handler hashes the incoming (name, arguments)
the same way and looks up the originating session_id — so the tool can
exclude that session from search results ("don't surface my own transcript
back at me").

No persistence: an in-memory LRU is enough. Entries are short-lived (MCP calls
follow tool_use within seconds) and the cache lives for the lifetime of the
process.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from typing import Any

# How long a recorded (hash -> session_id) entry is valid for. The MCP call
# normally follows the triggering tool_use within seconds, but we use a wide
# window to comfortably cover retries and long-running tool batches.
_TTL_SECONDS = 300.0

# Default values for each MCP tool's arguments. The proxy only sees keys the
# model explicitly emitted; the MCP handler gets defaults pre-filled by
# FastMCP. Normalizing both sides against this table lets them hash to the
# same key regardless of which keys were explicit in the tool_use input_json.
#
# Keep in sync with the tool signatures in mcp_server.py. Missing tool names
# fall back to "hash the raw arguments" — correct for tools with no defaults.
_TOOL_DEFAULTS: dict[str, dict[str, Any]] = {
    "search_transcripts": {
        "limit": 20,
        "cwd": None,
        "since": None,
    },
    "get_session_window": {
        "include_tool_results": True,
        "truncate": True,
    },
}


def _normalize(name: str, arguments: Any) -> dict[str, Any]:
    """Return arguments with tool-specific defaults applied.

    Not-a-dict or unknown tools pass through unchanged (wrapped in a dict for
    the non-dict case). Unknown keys are preserved — we want the hash to
    differ when the model emits an argument we don't know about.
    """
    if not isinstance(arguments, dict):
        return {"__raw__": arguments}
    defaults = _TOOL_DEFAULTS.get(name, {})
    merged = dict(defaults)
    merged.update(arguments)
    return merged


# Small cap: MCP tool calls happen one at a time per session, and entries are
# consumed within seconds. 256 covers a dozen concurrent sessions with room
# to spare.
_MAX_ENTRIES = 256

_cache: "OrderedDict[bytes, tuple[str, float]]" = OrderedDict()
_lock = threading.Lock()


def _canonical_hash(name: str, arguments: Any) -> bytes:
    """sha256 of name + canonical JSON of normalized arguments.

    Normalization fills in tool defaults so the proxy side (seeing only
    explicitly-emitted keys) and the MCP side (seeing kwargs with defaults
    pre-applied) produce the same hash. `sort_keys` + compact separators
    make the output stable regardless of original key order.
    """
    normalized = _normalize(name, arguments)
    try:
        payload = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        payload = repr(normalized)
    return hashlib.sha256(f"{name}\x00{payload}".encode()).digest()


def record(name: str, arguments: Any, session_id: str) -> None:
    """Remember that `session_id` asked Anthropic to invoke (name, arguments).

    Overwriting the existing entry is correct: if two sessions emit the same
    (name, arguments) the most-recent one wins. The model emitting a batch of
    N identical parallel tool_use blocks from one session just re-records
    the same entry N times — harmless.
    """
    if not session_id:
        return
    key = _canonical_hash(name, arguments)
    now = time.monotonic()
    with _lock:
        _cache[key] = (session_id, now)
        _cache.move_to_end(key)
        # Opportunistic eviction: drop expired entries from the front and
        # cap total size. Since the OrderedDict is insertion-ordered, the
        # front is the oldest.
        while _cache:
            _, ts = next(iter(_cache.values()))
            if now - ts <= _TTL_SECONDS and len(_cache) <= _MAX_ENTRIES:
                break
            _cache.popitem(last=False)


def lookup(name: str, arguments: Any) -> str | None:
    """Return the session_id that triggered this MCP call, if we've seen it.

    Non-consuming: parallel tool_use blocks from the same session all find
    the same entry. Entries time out after `_TTL_SECONDS` so a stale match
    cannot leak across long-separated calls.
    """
    key = _canonical_hash(name, arguments)
    now = time.monotonic()
    with _lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        session_id, ts = entry
        if now - ts > _TTL_SECONDS:
            _cache.pop(key, None)
            return None
        return session_id


def clear() -> None:
    """Test helper — not used by production code."""
    with _lock:
        _cache.clear()
