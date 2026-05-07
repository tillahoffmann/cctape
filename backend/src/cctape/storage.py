"""Content-addressed storage for request payload components.

The Anthropic `/v1/messages` request body is heavily redundant across a session:
`tools` is often identical across every request, `system` differs only by a small
hash field, and successive requests share a growing message prefix. We decompose
each request into (`system`, `tools`, individual messages, `extras`) and intern
each component by sha256 into the `blobs` table.

Per-request message lists used to be stored as a flat 32-byte-per-entry blob
(`message_hashes`), but in a typical archive 99%+ of those hashes repeat earlier
slots in the same session. Messages are now indirected through
`session_message_dict`, a per-session table that assigns each distinct hash a
small integer index; requests store a varint-packed sequence of those indices in
`message_refs`. Sessionless rows (rare) still use the flat 32-byte form, in
`message_refs_inline`.
"""

import gzip
import hashlib
import json
import sqlite3
from typing import Any

_MISSING = object()
_HASH_SIZE = 32  # raw sha256 digest
# Marker used by block-level interning. A blob whose JSON contains
# `{"__cctape_ref__": "<hex>"}` dicts is a "ref blob"; readers transparently
# inline the referenced sub-blob via `_resolve_refs`. The sentinel name avoids
# any chance of colliding with Anthropic API content.
_REF_KEY = "__cctape_ref__"


def compress(data: bytes) -> bytes:
    # gzip at level 6: ~same ratio as bz2-9 on this workload, 5-10x faster.
    # mtime=0 keeps output deterministic so identical bodies hash identically.
    return gzip.compress(data, compresslevel=6, mtime=0)


def decompress(data: bytes) -> bytes:
    return gzip.decompress(data)


def _canonical(value: Any) -> bytes:
    # sort_keys + compact separators so semantically-equal values hash identically.
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _strip_cache_control(value: Any) -> Any:
    # cache_control hints toggle on/off across requests for the same content,
    # producing duplicate blobs. Drop them before hashing — the proxy still
    # forwards the original body upstream, so the API sees the hints intact.
    if isinstance(value, list):
        return [_strip_cache_control(v) for v in value]
    if isinstance(value, dict):
        return {
            k: _strip_cache_control(v) for k, v in value.items() if k != "cache_control"
        }
    return value


def _intern(conn: sqlite3.Connection, value: Any) -> bytes | None:
    """Insert `value` into `blobs` if missing and return its raw sha256 digest.

    Returns None if `value` is the `_MISSING` sentinel so callers can distinguish
    "field absent" from "field explicitly null".
    """
    if value is _MISSING:
        return None
    value = _strip_cache_control(value)
    data = _canonical(value)
    digest = hashlib.sha256(data).digest()
    conn.execute(
        "INSERT OR IGNORE INTO blobs (hash, data) VALUES (?, ?)",
        (digest, compress(data)),
    )
    return digest


def _intern_blocks(conn: sqlite3.Connection, value: Any) -> bytes | None:
    """Intern a list value element-by-element, leaving `__cctape_ref__` markers.

    Used for the `system` field of an Anthropic request. The system array's
    first element is the per-request billing header (always unique), but
    elements 1+ are nearly identical across all requests in a session — block
    interning collapses those to a single blob each. Returns None for the
    `_MISSING` sentinel; falls back to `_intern` for non-list values.
    """
    if value is _MISSING:
        return None
    value = _strip_cache_control(value)
    if not isinstance(value, list) or not value:
        return _intern(conn, value)
    refs = [{_REF_KEY: _intern(conn, block).hex()} for block in value]  # type: ignore[union-attr]
    return _intern(conn, refs)


def _load_blob(conn: sqlite3.Connection, digest: bytes) -> bytes:
    row = conn.execute("SELECT data FROM blobs WHERE hash = ?", (digest,)).fetchone()
    if row is None:
        raise KeyError(f"blob {digest.hex()} missing")
    return decompress(row[0])


# Sub-blobs are stored via `_intern` (flat), so a single ref-resolution step is
# always enough. This bound catches accidental cycles or nested-ref bugs without
# adding cost on the happy path.
_MAX_REF_DEPTH = 4


def _resolve_refs(conn: sqlite3.Connection, value: Any, depth: int = 0) -> Any:
    """Recursively replace `{__cctape_ref__: <hex>}` markers with their content.

    Block-level interning stores list elements as separate blobs and leaves a
    ref-marker dict in their place. This helper transparently rehydrates the
    original structure on read. A no-op for blobs that don't contain any
    markers (i.e. non-block-interned values).
    """
    if depth > _MAX_REF_DEPTH:
        raise ValueError(f"ref depth exceeded {_MAX_REF_DEPTH}; nested-ref bug?")
    if isinstance(value, dict):
        if len(value) == 1 and _REF_KEY in value:
            digest = bytes.fromhex(value[_REF_KEY])
            return _load_value(conn, digest, depth=depth + 1)
        return {k: _resolve_refs(conn, v, depth) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_refs(conn, v, depth) for v in value]
    return value


def _load_value(conn: sqlite3.Connection, digest: bytes, depth: int = 0) -> Any:
    """Load a blob, JSON-decode it, and resolve any block refs.

    Equivalent to `json.loads(_load_blob(...))` for blobs that don't contain
    `__cctape_ref__` markers; for blobs that do, fetches and inlines the
    referenced block content.
    """
    raw = _load_blob(conn, digest)
    return _resolve_refs(conn, json.loads(raw), depth)


def collect_ref_digests(value: Any) -> list[bytes]:
    """Return every `__cctape_ref__` digest reachable from `value`.

    Used by FTS indexing to discover block sub-blobs so they can be associated
    with the right session, and by GC to walk the live set.
    """
    out: list[bytes] = []

    def _walk(v: Any) -> None:
        if isinstance(v, dict):
            if len(v) == 1 and _REF_KEY in v:
                out.append(bytes.fromhex(v[_REF_KEY]))
                return
            for vv in v.values():
                _walk(vv)
        elif isinstance(v, list):
            for vv in v:
                _walk(vv)

    _walk(value)
    return out


def _split_hashes(packed: bytes | None) -> list[bytes]:
    """Split a flat 32-byte-per-entry hash blob.

    Used for the legacy `message_hashes` column (pre-v2 schema) and for the
    sessionless `message_refs_inline` column. Returns [] for None/empty input.
    """
    if not packed:
        return []
    return [packed[i : i + _HASH_SIZE] for i in range(0, len(packed), _HASH_SIZE)]


def _encode_varints(values: list[int]) -> bytes:
    """LEB128-pack a list of non-negative integers."""
    out = bytearray()
    for v in values:
        if v < 0:
            raise ValueError("varint values must be non-negative")
        while v >= 0x80:
            out.append((v & 0x7F) | 0x80)
            v >>= 7
        out.append(v & 0x7F)
    return bytes(out)


def _decode_varints(packed: bytes) -> list[int]:
    out: list[int] = []
    v = 0
    shift = 0
    for byte in packed:
        v |= (byte & 0x7F) << shift
        if byte & 0x80:
            shift += 7
        else:
            out.append(v)
            v = 0
            shift = 0
    if shift:
        raise ValueError("truncated varint sequence")
    return out


def load_message_hashes(
    conn: sqlite3.Connection,
    *,
    session_id: str | None,
    message_refs: bytes | None,
    message_refs_inline: bytes | None,
) -> list[bytes]:
    """Return the ordered list of message-blob hashes for a request row.

    Resolves either the per-session-dictionary form (`message_refs` + a
    `session_message_dict` lookup) or the inline 32-byte form
    (`message_refs_inline`, used for sessionless rows). Returns [] if the
    request has no message list (legacy `payload`-only rows, or in-flight
    rows mid-write).
    """
    if message_refs_inline:
        return _split_hashes(message_refs_inline)
    if not message_refs:
        return []
    if session_id is None:
        # Defensive: a packed-refs row with no session can't be resolved.
        # Writers route sessionless rows through the inline column, so this
        # shouldn't happen in practice.
        return []
    indices = _decode_varints(message_refs)
    if not indices:
        return []
    rows = conn.execute(
        f"SELECT idx, hash FROM session_message_dict "
        f"WHERE session_id = ? AND idx IN ({','.join('?' * len(indices))})",
        (session_id, *indices),
    ).fetchall()
    by_idx = {idx: h for idx, h in rows}
    return [by_idx[i] for i in indices if i in by_idx]


def _intern_session_messages(
    conn: sqlite3.Connection,
    session_id: str,
    digests: list[bytes],
) -> list[int]:
    """Assign session-local indices to each digest, inserting new dict entries.

    Idempotent: existing (session_id, hash) → idx pairs are reused. New hashes
    are appended at MAX(idx)+1. Returns the indices in input order (with
    repeats preserved).
    """
    if not digests:
        return []
    distinct = list({d: None for d in digests})
    placeholders = ",".join("?" * len(distinct))
    existing = {
        h: idx
        for idx, h in conn.execute(
            f"SELECT idx, hash FROM session_message_dict "
            f"WHERE session_id = ? AND hash IN ({placeholders})",
            (session_id, *distinct),
        )
    }
    missing = [d for d in distinct if d not in existing]
    if missing:
        next_idx_row = conn.execute(
            "SELECT COALESCE(MAX(idx), -1) + 1 FROM session_message_dict "
            "WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        next_idx = next_idx_row[0] if next_idx_row else 0
        new_rows = []
        for d in missing:
            existing[d] = next_idx
            new_rows.append((session_id, next_idx, d))
            next_idx += 1
        conn.executemany(
            "INSERT INTO session_message_dict (session_id, idx, hash) VALUES (?, ?, ?)",
            new_rows,
        )
    return [existing[d] for d in digests]


def decompose_payload(
    conn: sqlite3.Connection, body: bytes, session_id: str | None = None
) -> dict[str, Any]:
    """Parse `body`, intern its components, and return dedup column values.

    Raises `ValueError` if the body isn't a JSON object. Callers should catch and
    fall back to storing the raw bytes in `requests.payload`.

    `session_id` controls how messages are stored: a present session id routes
    through the per-session dictionary (`message_refs`); a missing one falls back
    to inline 32-byte digests (`message_refs_inline`).
    """
    parsed = json.loads(body)
    if not isinstance(parsed, dict):
        raise ValueError("request body is not a JSON object")

    system_hash = _intern_blocks(conn, parsed.pop("system", _MISSING))
    tools_hash = _intern(conn, parsed.pop("tools", _MISSING))
    messages = parsed.pop("messages", [])
    if not isinstance(messages, list):
        raise ValueError("`messages` is not a list")

    digests = [_intern(conn, m) or b"" for m in messages]

    message_refs: bytes | None = None
    message_refs_inline: bytes | None = None
    if session_id is not None:
        indices = _intern_session_messages(conn, session_id, digests)
        message_refs = _encode_varints(indices)
    else:
        message_refs_inline = b"".join(digests)

    extras_blob = compress(_canonical(parsed)) if parsed else None

    return {
        "system_hash": system_hash,
        "tools_hash": tools_hash,
        "message_refs": message_refs,
        "message_refs_inline": message_refs_inline,
        "extras": extras_blob,
        "payload": None,
    }


def reconstruct_payload(
    conn: sqlite3.Connection,
    system_hash: bytes | None,
    tools_hash: bytes | None,
    extras: bytes | None,
    payload: bytes | None,
    *,
    session_id: str | None,
    message_refs: bytes | None,
    message_refs_inline: bytes | None,
) -> dict[str, Any] | list[Any] | None:
    """Reassemble a request payload from its deduplicated pieces.

    Falls back to decoding `payload` when no dedup columns are populated (rows
    that failed to parse at insert time).
    """
    has_dedup = (
        system_hash is not None
        or tools_hash is not None
        or message_refs is not None
        or message_refs_inline is not None
        or extras is not None
    )
    if not has_dedup:
        if payload is None:
            return None
        try:
            return json.loads(decompress(payload))
        except (ValueError, OSError):
            return None

    result: dict[str, Any] = json.loads(decompress(extras)) if extras else {}
    if system_hash is not None:
        result["system"] = _load_value(conn, system_hash)
    if tools_hash is not None:
        result["tools"] = _load_value(conn, tools_hash)
    hashes = load_message_hashes(
        conn,
        session_id=session_id,
        message_refs=message_refs,
        message_refs_inline=message_refs_inline,
    )
    if hashes or message_refs is not None or message_refs_inline is not None:
        result["messages"] = [_load_value(conn, h) for h in hashes]
    return result


def first_message(
    conn: sqlite3.Connection,
    *,
    session_id: str | None,
    message_refs: bytes | None,
    message_refs_inline: bytes | None,
    payload: bytes | None,
) -> dict[str, Any] | None:
    """Return the first message of a request, loading only what's necessary."""
    hashes = load_message_hashes(
        conn,
        session_id=session_id,
        message_refs=message_refs,
        message_refs_inline=message_refs_inline,
    )
    if hashes:
        return _load_value(conn, hashes[0])
    if payload is None:
        return None
    try:
        data = json.loads(decompress(payload))
    except (ValueError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    msgs = data.get("messages") or []
    return msgs[0] if msgs else None


def last_message(
    conn: sqlite3.Connection,
    *,
    session_id: str | None,
    message_refs: bytes | None,
    message_refs_inline: bytes | None,
) -> dict[str, Any] | None:
    """Return the last message of a request via a single blob lookup.

    Anthropic API requests carry the entire conversation prefix in `messages`,
    so for a session's transcript view we only need the *new* message at the
    tail of each request. This avoids the O(N²) cost of rehydrating every
    prior message for every turn.
    """
    hashes = load_message_hashes(
        conn,
        session_id=session_id,
        message_refs=message_refs,
        message_refs_inline=message_refs_inline,
    )
    if not hashes:
        return None
    return _load_value(conn, hashes[-1])
