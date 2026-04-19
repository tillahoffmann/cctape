import json
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from zstandard import ZstdDecompressor

from .util import iter_records


def _maybe_decompress(payload: bytes) -> bytes:
    # Compression of fields has changed over time. We try to decompress based on the
    # header.
    _ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
    if payload.startswith(_ZSTD_MAGIC):
        return ZstdDecompressor().decompress(payload)
    return payload


router = APIRouter(prefix="/api")


class UsageRecord(BaseModel):
    timestamp: datetime
    input_tokens: int | None
    output_tokens: int | None
    cache_creation_input_tokens: int | None
    cache_read_input_tokens: int | None
    unified_5h_utilization: float | None
    unified_7d_utilization: float | None


class SessionSummary(BaseModel):
    session_id: str
    first_timestamp: datetime
    last_timestamp: datetime
    turn_count: int
    input_tokens: int | None
    output_tokens: int | None
    cache_creation_input_tokens: int | None
    cache_read_input_tokens: int | None
    first_message_preview: str | None
    peak_context_tokens: int | None
    cwd: str | None
    git_branch: str | None
    is_sidechain: bool | None
    started_at: datetime | None


class RequestRecord(BaseModel):
    id: int
    timestamp: datetime
    payload: dict[str, Any] | list[Any] | None


class ResponseRecord(BaseModel):
    status_code: int
    timestamp: datetime
    payload: str | None
    input_tokens: int | None
    output_tokens: int | None
    cache_creation_input_tokens: int | None
    cache_read_input_tokens: int | None


class Turn(BaseModel):
    request: RequestRecord
    response: ResponseRecord | None


class SessionDetail(BaseModel):
    session_id: str
    turns: list[Turn]
    cwd: str | None
    git_branch: str | None
    is_sidechain: bool | None
    started_at: datetime | None


@router.get("/usage")
async def _get_usage(request: Request, days: int = 7) -> list[UsageRecord]:
    conn: sqlite3.Connection = request.app.state.conn
    return list(
        iter_records(
            conn.execute(
                """
                SELECT
                    timestamp,
                    input_tokens,
                    output_tokens,
                    cache_creation_input_tokens,
                    cache_read_input_tokens,
                    unified_5h_utilization,
                    unified_7d_utilization
                FROM responses
                WHERE timestamp > :oldest_record
                ORDER BY timestamp
                """,
                {"oldest_record": datetime.now(UTC) - timedelta(days=days)},
            ),
            UsageRecord,
        )
    )


def _extract_preview(payload: bytes, limit: int = 200) -> str | None:
    try:
        data = json.loads(_maybe_decompress(payload))
    except (ValueError, TypeError):
        return None
    messages = data.get("messages") if isinstance(data, dict) else None
    if not messages:
        return None
    first = messages[0]
    content = first.get("content")
    text: str | None = None
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                break
    if text is None:
        return None
    text = text.strip()
    return text[:limit] + ("…" if len(text) > limit else "")


@router.get("/sessions")
async def _get_sessions(request: Request) -> list[SessionSummary]:
    conn: sqlite3.Connection = request.app.state.conn
    # Aggregate per-session metrics. LEFT JOIN because a request may not yet have
    # a response row (in-flight or failed).
    rows = conn.execute(
        """
        SELECT
            r.session_id AS session_id,
            MIN(r.timestamp) AS first_timestamp,
            MAX(r.timestamp) AS last_timestamp,
            COUNT(r.id) AS turn_count,
            SUM(resp.input_tokens) AS input_tokens,
            SUM(resp.output_tokens) AS output_tokens,
            SUM(resp.cache_creation_input_tokens) AS cache_creation_input_tokens,
            SUM(resp.cache_read_input_tokens) AS cache_read_input_tokens,
            MAX(
                COALESCE(resp.input_tokens, 0)
                + COALESCE(resp.cache_creation_input_tokens, 0)
                + COALESCE(resp.cache_read_input_tokens, 0)
            ) AS peak_context_tokens,
            s.cwd AS cwd,
            s.git_branch AS git_branch,
            s.is_sidechain AS is_sidechain,
            s.started_at AS started_at
        FROM requests r
        LEFT JOIN responses resp ON resp.request_row_id = r.id
        LEFT JOIN sessions s ON s.session_id = r.session_id
        WHERE r.session_id IS NOT NULL
        GROUP BY r.session_id
        ORDER BY last_timestamp DESC
        """
    ).fetchall()

    # Fetch the earliest request payload per session for preview text.
    preview_rows = conn.execute(
        """
        SELECT session_id, payload
        FROM (
            SELECT
                session_id,
                payload,
                ROW_NUMBER() OVER (
                    PARTITION BY session_id ORDER BY timestamp ASC
                ) AS rn
            FROM requests
            WHERE session_id IS NOT NULL
        )
        WHERE rn = 1
        """
    ).fetchall()
    previews: dict[str, str | None] = {
        session_id: _extract_preview(payload) for session_id, payload in preview_rows
    }

    # SQLite's MIN/MAX on a TEXT-stored datetime returns a string; convert.
    def _dt(value: Any) -> datetime:
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(value)

    return [
        SessionSummary(
            session_id=session_id,
            first_timestamp=_dt(first_ts),
            last_timestamp=_dt(last_ts),
            turn_count=turn_count,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_input_tokens=cache_creation_input_tokens,
            cache_read_input_tokens=cache_read_input_tokens,
            first_message_preview=previews.get(session_id),
            peak_context_tokens=peak_context_tokens or None,
            cwd=cwd,
            git_branch=git_branch,
            is_sidechain=bool(is_sidechain) if is_sidechain is not None else None,
            started_at=_dt(started_at) if started_at else None,
        )
        for (
            session_id,
            first_ts,
            last_ts,
            turn_count,
            input_tokens,
            output_tokens,
            cache_creation_input_tokens,
            cache_read_input_tokens,
            peak_context_tokens,
            cwd,
            git_branch,
            is_sidechain,
            started_at,
        ) in rows
    ]


@router.get("/sessions/{session_id}")
async def _get_session(request: Request, session_id: str) -> SessionDetail:
    conn: sqlite3.Connection = request.app.state.conn
    rows = conn.execute(
        """
        SELECT
            r.id AS request_id,
            r.timestamp AS request_timestamp,
            r.payload AS request_payload,
            resp.status_code AS status_code,
            resp.timestamp AS response_timestamp,
            resp.payload AS response_payload,
            resp.input_tokens AS input_tokens,
            resp.output_tokens AS output_tokens,
            resp.cache_creation_input_tokens AS cache_creation_input_tokens,
            resp.cache_read_input_tokens AS cache_read_input_tokens
        FROM requests r
        LEFT JOIN responses resp ON resp.request_row_id = r.id
        WHERE r.session_id = :session_id
        ORDER BY r.timestamp ASC
        """,
        {"session_id": session_id},
    ).fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail="session not found")

    meta = conn.execute(
        "SELECT cwd, git_branch, is_sidechain, started_at FROM sessions WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    cwd, git_branch, is_sidechain, started_at = (
        meta if meta else (None, None, None, None)
    )

    decompressor = ZstdDecompressor()
    turns: list[Turn] = []
    for row in rows:
        (
            request_id,
            request_timestamp,
            request_payload,
            status_code,
            response_timestamp,
            response_payload,
            input_tokens,
            output_tokens,
            cache_creation_input_tokens,
            cache_read_input_tokens,
        ) = row

        try:
            parsed_request = (
                json.loads(_maybe_decompress(request_payload))
                if request_payload
                else None
            )
        except ValueError:
            parsed_request = None

        response: ResponseRecord | None = None
        if status_code is not None:
            decoded_payload: str | None = None
            if response_payload:
                try:
                    decoded_payload = decompressor.decompress(response_payload).decode(
                        "utf-8", errors="replace"
                    )
                except Exception:
                    decoded_payload = None
            response = ResponseRecord(
                status_code=status_code,
                timestamp=response_timestamp
                if isinstance(response_timestamp, datetime)
                else datetime.fromisoformat(response_timestamp),
                payload=decoded_payload,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_creation_input_tokens=cache_creation_input_tokens,
                cache_read_input_tokens=cache_read_input_tokens,
            )

        turns.append(
            Turn(
                request=RequestRecord(
                    id=request_id,
                    timestamp=request_timestamp
                    if isinstance(request_timestamp, datetime)
                    else datetime.fromisoformat(request_timestamp),
                    payload=parsed_request,
                ),
                response=response,
            )
        )

    return SessionDetail(
        session_id=session_id,
        turns=turns,
        cwd=cwd,
        git_branch=git_branch,
        is_sidechain=bool(is_sidechain) if is_sidechain is not None else None,
        started_at=(
            started_at
            if isinstance(started_at, datetime) or started_at is None
            else datetime.fromisoformat(started_at)
        ),
    )
