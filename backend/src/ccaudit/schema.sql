CREATE TABLE blobs (
    "hash" BLOB PRIMARY KEY,       -- raw 32-byte sha256 digest
    "data" BLOB NOT NULL            -- gzip-compressed canonical JSON
);

CREATE TABLE requests (
    "id" INTEGER PRIMARY KEY,
    "timestamp" DATETIME NOT NULL,
    "headers" BLOB NOT NULL,        -- gzip-compressed JSON

    -- The session id is present in the header, but this facilitates easy grouping.
    "session_id" TEXT,

    -- Deduplicated payload components. `system` and `tools` are each stored as a
    -- single blob; `message_hashes` is the concatenation of 32-byte digests in
    -- message order. `extras` is a gzip-compressed JSON object holding any other
    -- top-level request fields (model, max_tokens, thinking, etc.).
    "system_hash" BLOB REFERENCES blobs(hash),
    "tools_hash" BLOB REFERENCES blobs(hash),
    "message_hashes" BLOB,
    "extras" BLOB,

    -- Fallback for bodies that could not be parsed as JSON. Set only when the
    -- deduplicated columns above are unusable; NULL for normal requests.
    "payload" BLOB
);
CREATE INDEX requests_session_id ON requests(session_id);

CREATE TABLE responses (
    "status_code" INTEGER NOT NULL,
    "timestamp" DATETIME NOT NULL,
    "headers" BLOB NOT NULL,        -- gzip-compressed JSON
    "payload" BLOB NOT NULL,        -- gzip-compressed response body
    -- Foreign key reference that tells us which message this is a response to. This is
    -- NOT the request id that Anthropic returns in the header.
    "request_row_id" INTEGER NOT NULL REFERENCES requests(id),

    -- The usage information is also present in the payload, but this gives us the
    -- ability to summarize without loading the entire payload.
    "input_tokens" INTEGER,
    "output_tokens" INTEGER,
    "cache_creation_input_tokens" INTEGER,
    "cache_read_input_tokens" INTEGER,
    "unified_5h_utilization" REAL,
    "unified_7d_utilization" REAL,
    "unified_5h_reset" DATETIME,
    "unified_7d_reset" DATETIME,
    "account_id" TEXT,
    "model" TEXT
);
CREATE INDEX responses_request_id ON responses(request_row_id);
CREATE INDEX responses_timestamp ON responses(timestamp);

-- Session metadata sourced from the first entry of the JSONL transcript at
-- ~/.claude/projects/<encoded-cwd>/<session_id>.jsonl.
CREATE TABLE sessions (
    "session_id" TEXT PRIMARY KEY,
    "cwd" TEXT,
    "started_at" DATETIME,
    "git_branch" TEXT,
    "is_sidechain" INTEGER,
    "title" TEXT
);

-- Full-text search over extracted plain text from blobs (message content,
-- tool inputs/results, thinking). Indexed text is capped per blob to keep
-- the index compact; see fts.py:TEXT_CAP.
-- content='' makes this a contentless FTS5 index — only the inverted index is
-- stored, not the text itself. Search is faster and the table is ~14 MB
-- smaller on a typical DB. Snippets are rebuilt in Python at query time by
-- re-extracting text from the referenced blob.
CREATE VIRTUAL TABLE blob_fts USING fts5(
    text,
    content='',
    tokenize = "unicode61 tokenchars '_'"
);
-- Maps blob_fts rowids back to blob hashes. Contentless FTS can't return
-- UNINDEXED column values, so the hash lives in this side table and is
-- joined on rowid at query time.
CREATE TABLE fts_hash (
    "rowid" INTEGER PRIMARY KEY,
    "hash" BLOB NOT NULL UNIQUE
);

-- Maps each FTS-indexed blob to the sessions it appears in. Deduplicated to
-- one row per (session_id, hash) pair to avoid the quadratic blow-up from
-- per-turn conversation replay in the Anthropic API.
CREATE TABLE session_blobs (
    "session_id" TEXT NOT NULL,
    "hash" BLOB NOT NULL,
    PRIMARY KEY (session_id, hash)
);
CREATE INDEX session_blobs_hash ON session_blobs(hash);
