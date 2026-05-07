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
import re
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
            content='',
            tokenize = "unicode61 tokenchars '_'"
        );
        CREATE TABLE IF NOT EXISTS fts_hash (
            "rowid" INTEGER PRIMARY KEY,
            "hash" BLOB NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS session_blobs (
            "session_id" TEXT NOT NULL,
            "hash" BLOB NOT NULL,
            PRIMARY KEY (session_id, hash)
        );
        CREATE INDEX IF NOT EXISTS session_blobs_hash ON session_blobs(hash);
        CREATE TABLE IF NOT EXISTS fts_meta (
            "key" TEXT PRIMARY KEY,
            "value" INTEGER NOT NULL
        );
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

    Contentless FTS can't store the hash itself, so we allocate a rowid via
    fts_hash (which dedupes on `hash`) and use that same rowid in blob_fts.
    """
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO fts_hash (hash) VALUES (?)", (digest,)
        )
        if cur.rowcount == 0:
            # Already indexed.
            return
        rowid = cur.lastrowid
        text = extract_blob_text(data) or ""
        conn.execute("INSERT INTO blob_fts (rowid, text) VALUES (?, ?)", (rowid, text))
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
        WHERE NOT EXISTS (SELECT 1 FROM fts_hash fh WHERE fh.hash = b.hash)
        """
    ).fetchall():
        index_blob(conn, digest, data)
        blobs_indexed += 1

    # Watermark: only scan requests appended since the last backfill.
    # index_request_blobs already inserts session_blobs pairs on the proxy
    # ingest path, so steady-state there's nothing to do here. Without this,
    # backfill rebuilt the full pair set on every startup (8s+ on large DBs).
    last_id = conn.execute(
        "SELECT value FROM fts_meta WHERE key = 'session_blobs_max_request_id'"
    ).fetchone()
    last_id = last_id[0] if last_id else 0
    max_id_row = conn.execute("SELECT MAX(id) FROM requests").fetchone()
    max_id = max_id_row[0] if max_id_row and max_id_row[0] is not None else 0

    # Populate session_blobs from requests newer than the watermark. The set
    # collapses repeated (session, hash) pairs from conversation replay.
    pairs: set[tuple[str, bytes]] = set()
    for session_id, system_hash, tools_hash, message_hashes in conn.execute(
        """
        SELECT session_id, system_hash, tools_hash, message_hashes
        FROM requests
        WHERE session_id IS NOT NULL AND id > ?
        """,
        (last_id,),
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
    conn.execute(
        "INSERT OR REPLACE INTO fts_meta (key, value) VALUES "
        "('session_blobs_max_request_id', ?)",
        (max_id,),
    )
    conn.commit()
    return blobs_indexed, after - before


def _to_fts_query(query: str) -> str:
    """Convert a free-text query into a safe FTS5 MATCH expression.

    FTS5 treats many characters as operators (-, :, quotes, parens, AND, OR,
    NOT). Rather than teaching users that grammar, split the input on
    whitespace and wrap each word in double quotes so every token is a
    literal. Each token gets a trailing `*` so it matches as a prefix —
    searching "ide_diagnostic" should find "ide_diagnostics" too.

    Tokens are OR'd: a blob matches if ANY token appears. bm25 then ranks
    multi-token hits above single-token hits, so a precise query still
    surfaces its best match first — but broader queries ("stale hung
    process debug") no longer silently return empty just because no single
    blob contains every word.
    """
    tokens = []
    for word in query.split():
        # Strip double quotes from the user input, then re-wrap. Any other
        # character is fine inside a quoted FTS5 string.
        cleaned = word.replace('"', "").strip()
        if cleaned:
            tokens.append(f'"{cleaned}"*')
    return " OR ".join(tokens)


_SNIPPET_CONTEXT_CHARS = 60


def _build_snippet(text: str, terms: list[str]) -> str:
    """Extract a short highlighted snippet around the first match of any term.

    Replaces FTS5's snippet() for contentless indexes: finds the earliest
    case-insensitive hit of any query term, takes a ±60-char window, and
    wraps every matching occurrence with <mark>...</mark>. Token-boundary
    behaviour differs from FTS5's tokenizer-aware snippet but the output is
    close enough for UI display.
    """
    if not text or not terms:
        return ""
    low = text.lower()
    first = min((i for i in (low.find(t.lower()) for t in terms) if i >= 0), default=-1)
    if first < 0:
        return ""
    start = max(0, first - _SNIPPET_CONTEXT_CHARS)
    end = min(len(text), first + _SNIPPET_CONTEXT_CHARS)
    window = text[start:end]
    for term in terms:
        window = re.sub(
            re.escape(term),
            lambda m: f"<mark>{m.group(0)}</mark>",
            window,
            flags=re.IGNORECASE,
        )
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return prefix + window + suffix


def search_sessions(
    conn: sqlite3.Connection, query: str, limit: int = 50
) -> list[dict[str, Any]]:
    """Search indexed text and return sessions grouped by best-matching blob.

    Results are ordered by bm25 rank of the top matching blob per session.
    Snippets are rebuilt in Python by re-extracting text from the matching
    blob — contentless FTS doesn't store the text column.
    """
    fts_query = _to_fts_query(query)
    if not fts_query:
        return []
    # Contentless FTS cannot return UNINDEXED columns, so we join its rowid
    # to fts_hash to recover the blob digest.
    rows = conn.execute(
        """
        WITH hits AS (
            SELECT f.rowid AS rid, bm25(blob_fts) AS rank
            FROM blob_fts f
            WHERE blob_fts MATCH :query
        ),
        hit_hashes AS (
            SELECT fh.hash AS hash, h.rank AS rank
            FROM hits h JOIN fts_hash fh ON fh.rowid = h.rid
        ),
        session_hits AS (
            SELECT
                sb.session_id AS session_id,
                hh.hash AS hash,
                hh.rank AS rank,
                ROW_NUMBER() OVER (
                    PARTITION BY sb.session_id ORDER BY hh.rank ASC
                ) AS rn,
                COUNT(*) OVER (PARTITION BY sb.session_id) AS hit_count
            FROM hit_hashes hh
            JOIN session_blobs sb ON sb.hash = hh.hash
        )
        SELECT sh.session_id, sh.hash, sh.rank, sh.hit_count,
               s.title, s.cwd, s.git_branch
        FROM session_hits sh
        LEFT JOIN sessions s ON s.session_id = sh.session_id
        WHERE sh.rn = 1
        ORDER BY sh.rank ASC
        LIMIT :limit
        """,
        {"query": fts_query, "limit": limit},
    ).fetchall()

    terms = [w for w in query.split() if w]
    results = []
    for session_id, digest, rank, hit_count, title, cwd, git_branch in rows:
        blob_row = conn.execute(
            "SELECT data FROM blobs WHERE hash = ?", (digest,)
        ).fetchone()
        snippet_text = extract_blob_text(blob_row[0]) if blob_row else ""
        results.append(
            {
                "session_id": session_id,
                "snippet": _build_snippet(snippet_text, terms),
                "rank": rank,
                "hit_count": hit_count,
                "title": title,
                "cwd": cwd,
                "git_branch": git_branch,
            }
        )
    return results
