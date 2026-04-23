import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from httpx_sse._decoders import SSEDecoder

from . import mcp_caller
from .fts import index_request_blobs
from .sessions import ensure_known as ensure_session_known
from .storage import compress, decompose_payload

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
# Sensitive request headers redacted before persisting to the DB. The header is
# still forwarded upstream unchanged.
REDACTED_REQUEST_HEADERS = {"authorization"}
# Headers containing secrets that must not be persisted to the database. The
# upstream request still carries them — only the stored copy is redacted.
REDACTED_HEADERS = {"authorization"}


def _redact_header_value(name: str, value: str) -> str:
    """Redact sensitive header values before persisting. For `authorization`,
    replace the secret with a short sha256 fingerprint of the token (scheme
    stripped) so distinct keys can be told apart in stored request logs
    without the raw token hitting disk."""
    if name.lower() not in REDACTED_REQUEST_HEADERS:
        return value
    token = value.split(" ", 1)[1] if " " in value else value
    digest = hashlib.sha256(token.encode()).hexdigest()[:12]
    return f"[REDACTED sha256:{digest}]"


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
        "headers": compress(
            json.dumps(
                [(k, _redact_header_value(k, v)) for k, v in request.headers.items()]
            ).encode()
        ),
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
        values["payload"] = compress(request_body)

    # Claude Code issues a structured-output request with a {title: string}
    # schema to generate the session title. Detect it here so we can capture
    # the generated title from the SSE stream below.
    is_title_request = False
    try:
        parsed_body = json.loads(request_body)
        schema = (
            parsed_body.get("output_config", {}).get("format", {}).get("schema", {})
        )
        props = schema.get("properties", {})
        is_title_request = list(props.keys()) == ["title"]
    except (ValueError, TypeError, AttributeError):
        pass
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

    session_id = values["session_id"]
    ensure_session_known(conn, session_id)

    # Index extracted text from this request's blobs for full-text search.
    # index_request_blobs swallows its own errors so a bug here cannot break
    # the proxy; at worst new rows remain unsearchable until next startup.
    index_request_blobs(
        conn,
        session_id,
        values["system_hash"],
        values["tools_hash"],
        values["message_hashes"],
    )
    conn.commit()

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
        # Inline SSE decoder for detecting mcp__cctape__ tool_use blocks as
        # they stream past. This must happen BEFORE the chunk is yielded to
        # the client: the client fires the MCP tool call the moment it sees
        # content_block_stop, and if we defer recording to the `finally`
        # block (which runs only after the stream fully closes), the MCP
        # call's lookup happens against an empty cache.
        inline_decoder = SSEDecoder()
        cctape_tool_blocks: dict[int, dict[str, str]] = {}

        def _feed_line(line: str) -> None:
            # Feeds a single SSE line (already line-terminator-stripped) through
            # the decoder. Empty strings signal end-of-event and are meaningful;
            # the caller MUST only pass them when they came from a real blank
            # line in the stream, not as artifacts of split("\n") trailing.
            try:
                event = inline_decoder.decode(line.rstrip("\r"))
            except Exception:
                return
            if not event:
                return
            try:
                if event.event == "content_block_start":
                    data = json.loads(event.data)
                    block = data.get("content_block") or {}
                    if block.get("type") == "tool_use":
                        name = block.get("name") or ""
                        if name.startswith("mcp__cctape__"):
                            cctape_tool_blocks[int(data["index"])] = {
                                "name": name,
                                "input": "",
                            }
                elif event.event == "content_block_delta":
                    data = json.loads(event.data)
                    delta = data.get("delta") or {}
                    if delta.get("type") == "input_json_delta":
                        idx = data.get("index")
                        if isinstance(idx, int) and idx in cctape_tool_blocks:
                            cctape_tool_blocks[idx]["input"] += delta.get(
                                "partial_json", ""
                            )
                elif event.event == "content_block_stop":
                    idx = int(json.loads(event.data)["index"])
                    pending = cctape_tool_blocks.pop(idx, None)
                    if pending is not None and session_id:
                        short = pending["name"].split("__", 2)[-1]
                        try:
                            arguments = json.loads(pending["input"] or "{}")
                        except ValueError:
                            return
                        mcp_caller.record(short, arguments, session_id)
            except (ValueError, KeyError, TypeError):
                return

        # Buffer straddling chunks and extract complete lines between
        # newlines. SSEDecoder expects one line at a time, with line
        # terminators already stripped — empty strings ARE meaningful
        # (they signal end-of-event), so we must only feed empty strings
        # that were real blank lines in the stream, not trailing artifacts
        # of splitting on "\n".
        line_buf = ""
        try:
            async for chunk in upstream.aiter_bytes():
                # Record tool_use blocks BEFORE yielding so the MCP call
                # (triggered the moment the client sees content_block_stop)
                # can look up its caller.
                try:
                    line_buf += chunk.decode("utf-8", errors="replace")
                    # split -> all elements except the last are complete
                    # lines (the last is a possibly-partial tail).
                    parts = line_buf.split("\n")
                    line_buf = parts[-1]
                    for line in parts[:-1]:
                        _feed_line(line)
                except Exception:
                    pass
                yield chunk
                chunks.append(chunk)
            # Flush any residual line (no trailing newline was seen).
            if line_buf:
                try:
                    _feed_line(line_buf)
                except Exception:
                    pass
        finally:
            await upstream.aclose()

            # Parse the response and usage. (Tool-use tracking already ran
            # inline above; this block handles usage/model/title extraction.)
            payload = b"".join(chunks)
            text = payload.decode()
            usage = {}
            model: str | None = None
            title_text = ""
            decoder = SSEDecoder()
            # SSEDecoder dispatches a completed event only when fed the blank
            # line separator between events. str.splitlines() drops those
            # blanks, so events never fire — use split("\n") to preserve them.
            for line in text.split("\n"):
                event = decoder.decode(line.rstrip("\r"))
                if not event:
                    continue
                if event.event == "message_start":
                    # Best-effort model extraction; a parse failure here must
                    # never break the proxy — leave model as None.
                    try:
                        model = json.loads(event.data)["message"]["model"]
                    except (ValueError, KeyError, TypeError):
                        pass
                elif event.event == "message_delta":
                    data = json.loads(event.data)
                    usage = data["usage"]
                elif event.event == "content_block_start":
                    try:
                        data = json.loads(event.data)
                        block = data.get("content_block") or {}
                        if block.get("type") == "tool_use":
                            name = block.get("name") or ""
                            if name.startswith("mcp__cctape__"):
                                cctape_tool_blocks[int(data["index"])] = {
                                    "name": name,
                                    "input": "",
                                }
                    except (ValueError, KeyError, TypeError):
                        pass
                elif event.event == "content_block_delta":
                    delta_data = None
                    try:
                        delta_data = json.loads(event.data)
                    except ValueError:
                        pass
                    delta = (delta_data or {}).get("delta") or {}
                    if is_title_request and delta.get("type") == "text_delta":
                        title_text += delta.get("text", "")
                    elif delta.get("type") == "input_json_delta":
                        idx = (delta_data or {}).get("index")
                        if isinstance(idx, int) and idx in cctape_tool_blocks:
                            cctape_tool_blocks[idx]["input"] += delta.get(
                                "partial_json", ""
                            )
                elif event.event == "content_block_stop":
                    try:
                        idx = int(json.loads(event.data)["index"])
                    except (ValueError, KeyError, TypeError):
                        idx = -1
                    pending = cctape_tool_blocks.pop(idx, None)
                    if pending is not None and session_id:
                        # Strip the "mcp__<server>__" prefix so the name the
                        # MCP handler sees ("search_transcripts") matches.
                        short = pending["name"].split("__", 2)[-1]
                        try:
                            arguments = json.loads(pending["input"] or "{}")
                        except ValueError:
                            arguments = None
                        if arguments is not None:
                            mcp_caller.record(short, arguments, session_id)

            # Persist the generated title against the session row. The session
            # row may not exist yet — ensure_session_known only inserts once
            # Claude Code has written the session's JSONL file to disk, which
            # can lag the first title request — so upsert rather than update.
            if is_title_request and title_text and session_id:
                try:
                    title = json.loads(title_text).get("title")
                except ValueError:
                    title = None
                if isinstance(title, str) and title:
                    with conn:
                        conn.execute(
                            """
                            INSERT INTO sessions (session_id, title)
                            VALUES (?, ?)
                            ON CONFLICT(session_id) DO UPDATE SET title = excluded.title
                            """,
                            (session_id, title),
                        )

            # Split the cache-creation total into 5m/1h buckets. Anthropic
            # exposes the split as `usage.cache_creation` in `message_start`,
            # but `message_delta.usage` places the nested object under
            # `iterations[*].cache_creation` while only `cache_creation_input_tokens`
            # appears at the top level. Summing across iterations is safe: a
            # single response always reports one iteration unless server-side
            # batching changes that. When the nested object is absent fall back
            # to attributing the full total to the 5m bucket — that matches the
            # pre-split default TTL.
            iterations = usage.pop("iterations", None) or []
            cache_split = usage.pop("cache_creation", None)
            if cache_split is None and iterations:
                cache_split = {
                    "ephemeral_5m_input_tokens": sum(
                        (it.get("cache_creation") or {}).get(
                            "ephemeral_5m_input_tokens"
                        )
                        or 0
                        for it in iterations
                    ),
                    "ephemeral_1h_input_tokens": sum(
                        (it.get("cache_creation") or {}).get(
                            "ephemeral_1h_input_tokens"
                        )
                        or 0
                        for it in iterations
                    ),
                }
                if not any((it.get("cache_creation") is not None) for it in iterations):
                    cache_split = None
            cache_total = usage.pop("cache_creation_input_tokens", None)
            if cache_split is not None:
                cache_5m = cache_split.get("ephemeral_5m_input_tokens")
                cache_1h = cache_split.get("ephemeral_1h_input_tokens")
            else:
                cache_5m = cache_total
                cache_1h = None
            usage["cache_creation_5m_input_tokens"] = cache_5m
            usage["cache_creation_1h_input_tokens"] = cache_1h
            # Non-streaming responses, error responses, and any parse failures
            # leave usage empty; fill missing fields so the INSERT always binds.
            usage = {
                "input_tokens": None,
                "output_tokens": None,
                "cache_creation_5m_input_tokens": None,
                "cache_creation_1h_input_tokens": None,
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
                "headers": compress(
                    json.dumps(list(upstream.headers.items())).encode()
                ),
                "payload": compress(payload),
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
                "unified_5h_reset": datetime.fromtimestamp(
                    int(upstream.headers["anthropic-ratelimit-unified-5h-reset"]), UTC
                )
                if "anthropic-ratelimit-unified-5h-reset" in upstream.headers
                else None,
                "unified_7d_reset": datetime.fromtimestamp(
                    int(upstream.headers["anthropic-ratelimit-unified-7d-reset"]), UTC
                )
                if "anthropic-ratelimit-unified-7d-reset" in upstream.headers
                else None,
                "model": model,
                "account_id": upstream.headers.get("anthropic-organization-id"),
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
                    cache_creation_5m_input_tokens,
                    cache_creation_1h_input_tokens,
                    cache_read_input_tokens,
                    unified_5h_utilization,
                    unified_7d_utilization,
                    unified_5h_reset,
                    unified_7d_reset,
                    model,
                    account_id
                ) VALUES (
                    :status_code,
                    :timestamp,
                    :headers,
                    :payload,
                    :request_row_id,
                    :input_tokens,
                    :output_tokens,
                    :cache_creation_5m_input_tokens,
                    :cache_creation_1h_input_tokens,
                    :cache_read_input_tokens,
                    :unified_5h_utilization,
                    :unified_7d_utilization,
                    :unified_5h_reset,
                    :unified_7d_reset,
                    :model,
                    :account_id
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
