CREATE TABLE requests (
    "id" INTEGER PRIMARY KEY,
    "timestamp" DATETIME NOT NULL,
    "headers" BLOB NOT NULL,
    "payload" BLOB NOT NULL,

    -- The session id is present in the header, but this facilitates easy grouping.
    "session_id" TEXT
);
CREATE INDEX requests_session_id ON requests(session_id);

CREATE TABLE responses (
    "status_code" INTEGER NOT NULL,
    "timestamp" DATETIME NOT NULL,
    "headers" BLOB NOT NULL,
    "payload" BLOB NOT NULL,
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
    "unified_7d_utilization" REAL
);
CREATE INDEX responses_request_id ON responses(request_row_id);
