import bz2
import json
import sqlite3
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from httpx_sse._decoders import SSEDecoder

from .sessions import ensure_known as ensure_session_known
from .storage import decompose_payload

ANTHROPIC_BASE_URL = "https://api.anthropic.com/"
# Headers that describe a single connection hop, not the end-to-end request
# (RFC 7230 §6.1), plus `host` and `content-length` which httpx/Starlette must
# recompute for the outgoing hop. Forwarding them corrupts framing or routing.
HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}
# httpx decompresses the upstream body via aiter_bytes, so the forwarded
# payload is already decoded; leaving content-encoding would cause the
# client to attempt a second decompression. Only strip on the response
# side — the request body is passed through unchanged.
RESPONSE_HOP_BY_HOP = HOP_BY_HOP | {"content-encoding"}

router = APIRouter(prefix="/proxy")


@router.post("/v1/messages")
async def _post_messages(request: Request):
    # Save the complete request entry. The body is decomposed into dedup columns
    # (system, tools, individual messages are interned in the `blobs` table) so
    # identical content shared across requests is stored once. Only if the body
    # fails to parse do we fall back to the legacy single-blob `payload` column.
    conn: sqlite3.Connection = request.app.state.conn
    request_body = await request.body()
    values = {
        "headers": bz2.compress(json.dumps(request.headers.items()).encode()),
        "timestamp": datetime.now(UTC).isoformat(),
        "session_id": request.headers.get("x-claude-code-session-id"),
        "system_hash": None,
        "tools_hash": None,
        "message_hashes": None,
        "extras": None,
        "payload": None,
    }
    try:
        values.update(decompose_payload(conn, request_body))
    except (ValueError, TypeError):
        values["payload"] = bz2.compress(request_body)
    cursor = conn.execute(
        """
        INSERT INTO requests (
            timestamp, headers, session_id,
            system_hash, tools_hash, message_hashes, extras, payload
        ) VALUES (
            :timestamp, :headers, :session_id,
            :system_hash, :tools_hash, :message_hashes, :extras, :payload
        )
        """,
        values,
    )
    if not cursor.rowcount:  # pragma: no cover
        print("ERROR: Failed to insert request.")
    request_row_id = cursor.lastrowid
    conn.commit()

    ensure_session_known(conn, values["session_id"])

    # Send the request, excluding headers that should not be proxied.
    client: httpx.AsyncClient = request.app.state.http_client
    url = httpx.URL(ANTHROPIC_BASE_URL).join("v1/messages")
    request_headers = [
        (k, v) for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP
    ]
    upstream_req = client.build_request(
        "POST", url, headers=request_headers, content=request.stream()
    )
    upstream = await client.send(upstream_req, stream=True)

    async def body():
        chunks = []
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
                chunks.append(chunk)
        finally:
            await upstream.aclose()

            # Parse the response and usage.
            payload = b"".join(chunks)
            text = payload.decode()
            usage = {}
            decoder = SSEDecoder()
            for line in text.splitlines():
                event = decoder.decode(line)
                if event and event.event == "message_delta":
                    data = json.loads(event.data)
                    usage = data["usage"]

            # Non-streaming responses, error responses, and any parse failures
            # leave usage empty; fill missing fields so the INSERT always binds.
            usage = {
                "input_tokens": None,
                "output_tokens": None,
                "cache_creation_input_tokens": None,
                "cache_read_input_tokens": None,
            } | usage

            # Insert the response.
            if "date" in upstream.headers:
                timestamp = parsedate_to_datetime(upstream.headers["date"])
            else:  # pragma: no cover
                timestamp = datetime.now(UTC)
            values = {
                "status_code": upstream.status_code,
                "timestamp": timestamp,
                "headers": bz2.compress(
                    json.dumps(list(upstream.headers.items())).encode()
                ),
                "payload": bz2.compress(payload),
                "request_row_id": request_row_id,
                "unified_5h_utilization": float(
                    upstream.headers["anthropic-ratelimit-unified-5h-utilization"]
                )
                if "anthropic-ratelimit-unified-5h-utilization" in upstream.headers
                else None,
                "unified_7d_utilization": float(
                    upstream.headers["anthropic-ratelimit-unified-7d-utilization"]
                )
                if "anthropic-ratelimit-unified-7d-utilization" in upstream.headers
                else None,
                **usage,
            }
            cursor = conn.execute(
                """
                INSERT INTO responses (
                    status_code,
                    timestamp,
                    headers,
                    payload,
                    request_row_id,
                    input_tokens,
                    output_tokens,
                    cache_creation_input_tokens,
                    cache_read_input_tokens,
                    unified_5h_utilization,
                    unified_7d_utilization
                ) VALUES (
                    :status_code,
                    :timestamp,
                    :headers,
                    :payload,
                    :request_row_id,
                    :input_tokens,
                    :output_tokens,
                    :cache_creation_input_tokens,
                    :cache_read_input_tokens,
                    :unified_5h_utilization,
                    :unified_7d_utilization
                )
                """,
                values,
            )
            if not cursor.rowcount:  # pragma: no cover
                print(f"ERROR: Failed to insert response for request {request_row_id}.")
            conn.commit()

    # Response headers to forward, excluding headers that should not be proxied.
    response_headers = [
        (k, v)
        for k, v in upstream.headers.items()
        if k.lower() not in RESPONSE_HOP_BY_HOP
    ]
    return StreamingResponse(
        body(),
        status_code=upstream.status_code,
        headers=dict(response_headers),
    )
