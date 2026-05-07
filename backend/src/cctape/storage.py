"""Content-addressed storage for request payload components.

The Anthropic `/v1/messages` request body is heavily redundant across a session:
`tools` is often identical across every request, `system` differs only by a small
hash field, and successive requests share a growing message prefix. We decompose
each request into (`system`, `tools`, individual messages, `extras`) and intern
each component by sha256 into the `blobs` table. Requests store only the hashes,
so a message that appears in N requests is stored once.
"""

import gzip
import hashlib
import json
import sqlite3
from typing import Any

_MISSING = object()
_HASH_SIZE = 32  # raw sha256 digest


def compress(data: bytes) -> bytes:
    # gzip at level 6: ~same ratio as bz2-9 on this workload, 5-10x faster.
    # mtime=0 keeps output deterministic so identical bodies hash identically.
    return gzip.compress(data, compresslevel=6, mtime=0)


def decompress(data: bytes) -> bytes:
    return gzip.decompress(data)


def _canonical(value: Any) -> bytes:
    # sort_keys + compact separators so semantically-equal values hash identically.
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _intern(conn: sqlite3.Connection, value: Any) -> bytes | None:
    """Insert `value` into `blobs` if missing and return its raw sha256 digest.

    Returns None if `value` is the `_MISSING` sentinel so callers can distinguish
    "field absent" from "field explicitly null".
    """
    if value is _MISSING:
        return None
    data = _canonical(value)
    digest = hashlib.sha256(data).digest()
    conn.execute(
        "INSERT OR IGNORE INTO blobs (hash, data) VALUES (?, ?)",
        (digest, compress(data)),
    )
    return digest


def _load_blob(conn: sqlite3.Connection, digest: bytes) -> bytes:
    row = conn.execute("SELECT data FROM blobs WHERE hash = ?", (digest,)).fetchone()
    if row is None:
        raise KeyError(f"blob {digest.hex()} missing")
    return decompress(row[0])


def _split_hashes(packed: bytes | None) -> list[bytes]:
    if not packed:
        return []
    return [packed[i : i + _HASH_SIZE] for i in range(0, len(packed), _HASH_SIZE)]


def decompose_payload(conn: sqlite3.Connection, body: bytes) -> dict[str, Any]:
    """Parse `body`, intern its components, and return dedup column values.

    Raises `ValueError` if the body isn't a JSON object. Callers should catch and
    fall back to storing the raw bytes in `requests.payload`.
    """
    parsed = json.loads(body)
    if not isinstance(parsed, dict):
        raise ValueError("request body is not a JSON object")

    system_hash = _intern(conn, parsed.pop("system", _MISSING))
    tools_hash = _intern(conn, parsed.pop("tools", _MISSING))
    messages = parsed.pop("messages", [])
    if not isinstance(messages, list):
        raise ValueError("`messages` is not a list")
    # `message_hashes` stores digests back-to-back in message order. Splitting in
    # fixed 32-byte strides is cheaper than parsing JSON and trims ~75% vs hex.
    message_hashes = b"".join(_intern(conn, m) or b"" for m in messages)

    extras_blob = compress(_canonical(parsed)) if parsed else None

    return {
        "system_hash": system_hash,
        "tools_hash": tools_hash,
        "message_hashes": message_hashes,
        "extras": extras_blob,
        "payload": None,
    }


def reconstruct_payload(
    conn: sqlite3.Connection,
    system_hash: bytes | None,
    tools_hash: bytes | None,
    message_hashes: bytes | None,
    extras: bytes | None,
    payload: bytes | None,
) -> dict[str, Any] | list[Any] | None:
    """Reassemble a request payload from its deduplicated pieces.

    Falls back to decoding `payload` when no dedup columns are populated (rows
    that failed to parse at insert time).
    """
    if (
        message_hashes is None
        and system_hash is None
        and tools_hash is None
        and extras is None
    ):
        if payload is None:
            return None
        try:
            return json.loads(decompress(payload))
        except (ValueError, OSError):
            return None

    result: dict[str, Any] = json.loads(decompress(extras)) if extras else {}
    if system_hash is not None:
        result["system"] = json.loads(_load_blob(conn, system_hash))
    if tools_hash is not None:
        result["tools"] = json.loads(_load_blob(conn, tools_hash))
    if message_hashes is not None:
        result["messages"] = [
            json.loads(_load_blob(conn, h)) for h in _split_hashes(message_hashes)
        ]
    return result


def first_message(
    conn: sqlite3.Connection, message_hashes: bytes | None, payload: bytes | None
) -> dict[str, Any] | None:
    """Return the first message of a request, loading only what's necessary."""
    hashes = _split_hashes(message_hashes)
    if hashes:
        return json.loads(_load_blob(conn, hashes[0]))
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
    conn: sqlite3.Connection, message_hashes: bytes | None
) -> dict[str, Any] | None:
    """Return the last message of a request via a single blob lookup.

    Anthropic API requests carry the entire conversation prefix in `messages`,
    so for a session's transcript view we only need the *new* message at the
    tail of each request. This avoids the O(N²) cost of rehydrating every
    prior message for every turn.
    """
    if not message_hashes or len(message_hashes) < _HASH_SIZE:
        return None
    last_hash = message_hashes[-_HASH_SIZE:]
    return json.loads(_load_blob(conn, last_hash))
