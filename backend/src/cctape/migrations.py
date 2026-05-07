"""Schema migration runner.

Migrations are idempotent and applied at startup. Each one bumps `meta.schema_version`
once it commits. Skipped if `schema_version` >= the migration's index.

Migrations may take a long time on large pre-existing databases. The runner prints
progress to stderr so users running `uvx cctape` interactively know why startup is
slow on the first launch after upgrading.
"""

from __future__ import annotations

import sqlite3
import sys
import time
from collections.abc import Callable

_SCHEMA_VERSION_KEY = "schema_version"


def _ensure_meta(conn: sqlite3.Connection) -> None:
    conn.execute(
        'CREATE TABLE IF NOT EXISTS meta ("key" TEXT PRIMARY KEY, "value" TEXT NOT NULL)'
    )


def _current_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT value FROM meta WHERE key = ?", (_SCHEMA_VERSION_KEY,)
    ).fetchone()
    return int(row[0]) if row else 0


def _set_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        (_SCHEMA_VERSION_KEY, str(version)),
    )


def _stderr(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _migration_v1_cache_creation_split(conn: sqlite3.Connection) -> None:
    """Split responses.cache_creation_input_tokens into 5m/1h columns.

    Pre-split rows are attributed to the 5m bucket (the old default TTL).
    Idempotent: only runs the ALTER if the new column doesn't already exist,
    so DBs that already had this applied via the legacy probe-based migration
    in main.py before the runner existed are no-ops.
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(responses)").fetchall()}
    if "cache_creation_5m_input_tokens" in cols:
        return
    conn.execute(
        "ALTER TABLE responses ADD COLUMN cache_creation_5m_input_tokens INTEGER"
    )
    conn.execute(
        "ALTER TABLE responses ADD COLUMN cache_creation_1h_input_tokens INTEGER"
    )
    if "cache_creation_input_tokens" in cols:
        conn.execute(
            "UPDATE responses SET cache_creation_5m_input_tokens = "
            "cache_creation_input_tokens"
        )
        conn.execute("ALTER TABLE responses DROP COLUMN cache_creation_input_tokens")


def _migration_v2_message_refs(conn: sqlite3.Connection) -> None:
    """Replace `requests.message_hashes` with a per-session hash dictionary.

    Each session's distinct message hashes are stored once in `session_message_dict`,
    indexed by order-of-first-appearance. Each request stores `message_refs`, a
    varint-packed sequence of those indices. Sessionless requests (rare) keep the
    full 32-byte digests in `message_refs_inline`. Saves ~93% on the column on a
    typical archive that has heavy prefix-replay across requests.
    """
    from .storage import _encode_varints, _split_hashes

    req_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(requests)").fetchall()
    }
    if "message_refs" not in req_cols:
        conn.execute("ALTER TABLE requests ADD COLUMN message_refs BLOB")
    if "message_refs_inline" not in req_cols:
        conn.execute("ALTER TABLE requests ADD COLUMN message_refs_inline BLOB")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS session_message_dict (
            "session_id" TEXT NOT NULL,
            "idx" INTEGER NOT NULL,
            "hash" BLOB NOT NULL,
            PRIMARY KEY (session_id, idx)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS session_message_dict_lookup
        ON session_message_dict(session_id, hash)
        """
    )

    # Backfill is needed only if the legacy column still exists.
    if "message_hashes" not in req_cols:
        return

    total = conn.execute(
        "SELECT COUNT(*) FROM requests WHERE message_hashes IS NOT NULL "
        "AND message_refs IS NULL AND message_refs_inline IS NULL"
    ).fetchone()[0]
    if total == 0:
        # Nothing to backfill; safe to drop the legacy column now.
        conn.execute("ALTER TABLE requests DROP COLUMN message_hashes")
        return

    _stderr(f"cctape: migrating database — packing {total:,} request rows...")
    started = time.monotonic()

    # Process per session so we can build each session's dict without holding
    # all sessions in memory. Sessionless rows go through a separate pass.
    session_ids: list[str] = [
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT session_id FROM requests "
            "WHERE session_id IS NOT NULL AND message_hashes IS NOT NULL "
            "AND message_refs IS NULL"
        )
    ]
    processed = 0
    last_report = started
    for sid in session_ids:
        # Pull existing dict assignments (in case a previous partial run wrote some).
        idx_for: dict[bytes, int] = {
            h: i
            for i, h in conn.execute(
                "SELECT idx, hash FROM session_message_dict WHERE session_id = ?",
                (sid,),
            )
        }
        next_idx = max(idx_for.values()) + 1 if idx_for else 0

        rows = conn.execute(
            "SELECT id, message_hashes FROM requests "
            "WHERE session_id = ? AND message_hashes IS NOT NULL "
            "AND message_refs IS NULL ORDER BY id",
            (sid,),
        ).fetchall()

        new_dict_entries: list[tuple[str, int, bytes]] = []
        request_updates: list[tuple[bytes, int]] = []
        for req_id, mh in rows:
            indices: list[int] = []
            for h in _split_hashes(mh):
                idx = idx_for.get(h)
                if idx is None:
                    idx = next_idx
                    next_idx += 1
                    idx_for[h] = idx
                    new_dict_entries.append((sid, idx, h))
                indices.append(idx)
            request_updates.append((_encode_varints(indices), req_id))
            processed += 1

        with conn:
            if new_dict_entries:
                conn.executemany(
                    "INSERT INTO session_message_dict (session_id, idx, hash) "
                    "VALUES (?, ?, ?)",
                    new_dict_entries,
                )
            conn.executemany(
                "UPDATE requests SET message_refs = ?, message_hashes = NULL "
                "WHERE id = ?",
                request_updates,
            )

        now = time.monotonic()
        if now - last_report >= 2.0:
            _stderr(f"cctape: migrating database — {processed:,} / {total:,}")
            last_report = now

    # Sessionless rows: copy raw hashes into message_refs_inline.
    sessionless = conn.execute(
        "SELECT id, message_hashes FROM requests "
        "WHERE session_id IS NULL AND message_hashes IS NOT NULL "
        "AND message_refs_inline IS NULL"
    ).fetchall()
    if sessionless:
        with conn:
            conn.executemany(
                "UPDATE requests SET message_refs_inline = ?, message_hashes = NULL "
                "WHERE id = ?",
                [(mh, rid) for rid, mh in sessionless],
            )
        processed += len(sessionless)

    # Sanity: every row that previously had message_hashes now has refs or inline.
    remaining = conn.execute(
        "SELECT COUNT(*) FROM requests WHERE message_hashes IS NOT NULL"
    ).fetchone()[0]
    assert remaining == 0, f"{remaining} rows still hold message_hashes after backfill"

    conn.execute("ALTER TABLE requests DROP COLUMN message_hashes")

    elapsed = time.monotonic() - started
    _stderr(f"cctape: migration complete — {processed:,} rows in {elapsed:.1f}s")

    # Recover the space immediately. VACUUM cannot run inside a transaction;
    # the runner commits after each migration so this is safe here.
    conn.execute("VACUUM")


_MIGRATIONS: list[Callable[[sqlite3.Connection], None]] = [
    _migration_v1_cache_creation_split,
    _migration_v2_message_refs,
]


def run(conn: sqlite3.Connection) -> None:
    """Apply any unapplied migrations to `conn`. Idempotent."""
    _ensure_meta(conn)
    current = _current_version(conn)
    target = len(_MIGRATIONS)
    if current >= target:
        return
    for version in range(current + 1, target + 1):
        migration = _MIGRATIONS[version - 1]
        migration(conn)
        with conn:
            _set_version(conn, version)
