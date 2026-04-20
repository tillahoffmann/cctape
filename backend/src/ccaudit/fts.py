"""Full-text search over blob content.

Blobs store compressed JSON of Anthropic message components. We extract the
plain text portions (message text, tool inputs, tool results, thinking) and
index them in an FTS5 virtual table keyed by blob hash. A companion table
`session_blobs` maps each indexed blob to the sessions that reference it,
which lets a search query jump from a text hit back to sessions.

All public entry points are defensive: any failure inside FTS indexing is
swallowed and logged so the proxy ingest path never breaks because of a
bug here.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from .storage import _split_hashes, decompress

logger = logging.getLogger(__name__)

# Per-blob cap on indexed text, in bytes of UTF-8. See README / analysis in
# commit history — 10 KB keeps the FTS index to ~6 MB on typical DBs while
# preserving the searchable head of every tool result.
TEXT_CAP = 10_000


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create FTS tables if they don't exist.

    Idempotent: safe to run on every startup. This is the migration path for
    databases that predate the FTS feature — schema.sql already creates these
    tables for fresh databases.
    """
    conn.executescript(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS blob_fts USING fts5(
            text,
            hash UNINDEXED,
            tokenize = "unicode61 tokenchars '_'"
        );
        CREATE TABLE IF NOT EXISTS session_blobs (
            "session_id" TEXT NOT NULL,
            "hash" BLOB NOT NULL,
            PRIMARY KEY (session_id, hash)
        );
        CREATE INDEX IF NOT EXISTS session_blobs_hash ON session_blobs(hash);
        """
    )
    conn.commit()


def _extract_text(obj: Any) -> str:
    """Walk a decoded message/content structure and return concatenated text.

    Recognizes Anthropic content-block shapes: text, tool_use (stringified
    input), tool_result (recurse into content), thinking. Unknown shapes fall
    through to a generic recursion over dict values / list items, which picks
    up free-form string fields without crashing on new schema additions.
    """
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        parts: list[str] = []
        block_type = obj.get("type")
        if block_type == "text" and isinstance(obj.get("text"), str):
            return obj["text"]
        if block_type == "thinking" and isinstance(obj.get("thinking"), str):
            return obj["thinking"]
        if block_type == "tool_use":
            inp = obj.get("input")
            if inp is not None:
                try:
                    return json.dumps(inp, separators=(",", ":"))
                except (TypeError, ValueError):
                    return ""
            return ""
        if block_type == "tool_result":
            return _extract_text(obj.get("content", ""))
        # Fallback: recurse into content/message shapes we don't recognize.
        if "content" in obj:
            parts.append(_extract_text(obj["content"]))
        else:
            for v in obj.values():
                parts.append(_extract_text(v))
        return " ".join(p for p in parts if p)
    if isinstance(obj, list):
        return " ".join(p for p in (_extract_text(item) for item in obj) if p)
    return ""


def extract_blob_text(data: bytes) -> str:
    """Decompress a blob payload and extract indexable text, capped to TEXT_CAP."""
    try:
        parsed = json.loads(decompress(data))
    except (OSError, ValueError):
        return ""
    text = _extract_text(parsed).strip()
    if len(text.encode("utf-8")) > TEXT_CAP:
        # Truncate on UTF-8 byte boundary; encode/decode with 'ignore' handles
        # a cut through a multibyte sequence.
        text = text.encode("utf-8")[:TEXT_CAP].decode("utf-8", errors="ignore")
    return text


def index_blob(conn: sqlite3.Connection, digest: bytes, data: bytes) -> None:
    """Extract text from a single blob and insert into blob_fts if not present.

    No-op if the hash is already indexed (idempotent). Errors are logged and
    swallowed so callers on the hot path can't be broken by indexing bugs.
    """
    try:
        existing = conn.execute(
            "SELECT 1 FROM blob_fts WHERE hash = ? LIMIT 1", (digest,)
        ).fetchone()
        if existing:
            return
        text = extract_blob_text(data)
        if not text:
            # Still insert an empty row so we don't re-scan this blob on every
            # ingest — hash presence is our "already processed" marker.
            text = ""
        conn.execute("INSERT INTO blob_fts (text, hash) VALUES (?, ?)", (text, digest))
    except Exception:
        logger.exception("failed to index blob %s", digest.hex() if digest else "?")


def index_request_blobs(
    conn: sqlite3.Connection,
    session_id: str | None,
    system_hash: bytes | None,
    tools_hash: bytes | None,
    message_hashes: bytes | None,
) -> None:
    """Index every blob referenced by a new request and map them to the session.

    Called from the proxy ingest path after the request row is committed.
    Wrapped top-to-bottom in try/except so a bug here cannot fail a proxy
    insert — at worst, new rows are not searchable until the next backfill.
    """
    try:
        digests: list[bytes] = []
        if system_hash:
            digests.append(system_hash)
        if tools_hash:
            digests.append(tools_hash)
        digests.extend(_split_hashes(message_hashes))
        if not digests:
            return
        for digest in digests:
            row = conn.execute(
                "SELECT data FROM blobs WHERE hash = ?", (digest,)
            ).fetchone()
            if row is None:
                continue
            index_blob(conn, digest, row[0])
            if session_id:
                conn.execute(
                    "INSERT OR IGNORE INTO session_blobs (session_id, hash) VALUES (?, ?)",
                    (session_id, digest),
                )
    except Exception:
        logger.exception("failed to index request blobs")


def backfill(conn: sqlite3.Connection) -> tuple[int, int]:
    """Index any blobs not yet in blob_fts and fill session_blobs from requests.

    Returns (blobs_indexed, session_pairs_added). Idempotent: only processes
    blobs/pairs that are missing, so repeat calls are cheap.
    """
    blobs_indexed = 0
    for digest, data in conn.execute(
        """
        SELECT b.hash, b.data FROM blobs b
        WHERE NOT EXISTS (SELECT 1 FROM blob_fts f WHERE f.hash = b.hash)
        """
    ).fetchall():
        index_blob(conn, digest, data)
        blobs_indexed += 1

    # Populate session_blobs from existing requests. The GROUP BY collapses
    # repeated (session, hash) pairs from conversation replay.
    pairs: set[tuple[str, bytes]] = set()
    for session_id, system_hash, tools_hash, message_hashes in conn.execute(
        """
        SELECT session_id, system_hash, tools_hash, message_hashes
        FROM requests
        WHERE session_id IS NOT NULL
        """
    ).fetchall():
        if system_hash:
            pairs.add((session_id, system_hash))
        if tools_hash:
            pairs.add((session_id, tools_hash))
        for h in _split_hashes(message_hashes):
            pairs.add((session_id, h))

    before = conn.execute("SELECT COUNT(*) FROM session_blobs").fetchone()[0]
    conn.executemany(
        "INSERT OR IGNORE INTO session_blobs (session_id, hash) VALUES (?, ?)",
        pairs,
    )
    after = conn.execute("SELECT COUNT(*) FROM session_blobs").fetchone()[0]
    conn.commit()
    return blobs_indexed, after - before


def _to_fts_query(query: str) -> str:
    """Convert a free-text query into a safe FTS5 MATCH expression.

    FTS5 treats many characters as operators (-, :, quotes, parens, AND, OR,
    NOT). Rather than teaching users that grammar, split the input on
    whitespace and wrap each word in double quotes so every token is a
    literal. Multiple quoted tokens are ANDed implicitly.
    """
    tokens = []
    for word in query.split():
        # Strip double quotes from the user input, then re-wrap. Any other
        # character is fine inside a quoted FTS5 string.
        cleaned = word.replace('"', "").strip()
        if cleaned:
            tokens.append(f'"{cleaned}"')
    return " ".join(tokens)


def search_sessions(
    conn: sqlite3.Connection, query: str, limit: int = 50
) -> list[dict[str, Any]]:
    """Search indexed text and return sessions grouped by best-matching blob.

    Results are ordered by bm25 rank of the top matching blob per session.
    Each row includes a snippet of matched text for result display.
    """
    fts_query = _to_fts_query(query)
    if not fts_query:
        return []
    # snippet(): col 0, 10-token context, <mark> tags, "…" ellipsis.
    rows = conn.execute(
        """
        WITH hits AS (
            SELECT
                f.hash AS hash,
                snippet(blob_fts, 0, '<mark>', '</mark>', '…', 10) AS snippet,
                bm25(blob_fts) AS rank
            FROM blob_fts f
            WHERE blob_fts MATCH :query
        ),
        session_hits AS (
            SELECT
                sb.session_id AS session_id,
                h.hash AS hash,
                h.snippet AS snippet,
                h.rank AS rank,
                ROW_NUMBER() OVER (
                    PARTITION BY sb.session_id ORDER BY h.rank ASC
                ) AS rn
            FROM hits h
            JOIN session_blobs sb ON sb.hash = h.hash
        )
        SELECT sh.session_id, sh.snippet, sh.rank, s.title, s.cwd, s.git_branch
        FROM session_hits sh
        LEFT JOIN sessions s ON s.session_id = sh.session_id
        WHERE sh.rn = 1
        ORDER BY sh.rank ASC
        LIMIT :limit
        """,
        {"query": fts_query, "limit": limit},
    ).fetchall()
    return [
        {
            "session_id": session_id,
            "snippet": snippet,
            "rank": rank,
            "title": title,
            "cwd": cwd,
            "git_branch": git_branch,
        }
        for session_id, snippet, rank, title, cwd, git_branch in rows
    ]
