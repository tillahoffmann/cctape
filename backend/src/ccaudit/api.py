import sqlite3
from datetime import UTC, datetime, timedelta
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .fts import search_sessions
from .pricing import PRICING, cost
from .storage import decompress, first_message, reconstruct_payload
from .util import iter_records

router = APIRouter(prefix="/api")


class UsageRecord(BaseModel):
    timestamp: datetime
    input_tokens: int | None
    output_tokens: int | None
    cache_creation_input_tokens: int | None
    cache_read_input_tokens: int | None
    unified_5h_utilization: float | None
    unified_7d_utilization: float | None
    unified_5h_reset: datetime | None
    unified_7d_reset: datetime | None


class AccountSummary(BaseModel):
    account_id: str
    message_count: int
    first_timestamp: datetime
    last_timestamp: datetime
    input_tokens: int | None
    output_tokens: int | None
    cache_creation_input_tokens: int | None
    cache_read_input_tokens: int | None
    cost_usd: float | None


class SessionSummary(BaseModel):
    session_id: str
    first_timestamp: datetime
    last_timestamp: datetime
    turn_count: int
    input_tokens: int | None
    output_tokens: int | None
    cache_creation_input_tokens: int | None
    cache_read_input_tokens: int | None
    cost_usd: float | None
    first_message_preview: str | None
    peak_context_tokens: int | None
    cwd: str | None
    git_branch: str | None
    is_sidechain: bool | None
    started_at: datetime | None
    title: str | None


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
    unified_5h_utilization: float | None
    unified_7d_utilization: float | None
    model: str | None
    cost_usd: float | None


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
    title: str | None


class SessionUpdate(BaseModel):
    title: str | None = None


class SearchHit(BaseModel):
    session_id: str
    snippet: str
    rank: float
    title: str | None
    cwd: str | None
    git_branch: str | None


class Config(BaseModel):
    version: str | None
    db_path: str
    anthropic_base_url: str


@router.get("/pricing")
async def _get_pricing() -> dict[str, dict[str, float]]:
    return PRICING


@router.get("/config")
async def _get_config(request: Request) -> Config:
    try:
        pkg_version: str | None = version("ccaudit")
    except PackageNotFoundError:
        pkg_version = None
    base_url = str(request.base_url).rstrip("/") + "/proxy"
    return Config(
        version=pkg_version,
        db_path=request.app.state.db_path,
        anthropic_base_url=base_url,
    )


@router.get("/search")
async def _search(request: Request, q: str, limit: int = 50) -> list[SearchHit]:
    conn: sqlite3.Connection = request.app.state.conn
    try:
        results = search_sessions(conn, q, limit=limit)
    except sqlite3.OperationalError as exc:
        # FTS5 raises OperationalError on malformed MATCH expressions (e.g.
        # unbalanced quotes). Surface as 400 rather than 500.
        raise HTTPException(status_code=400, detail=f"invalid search: {exc}") from exc
    return [SearchHit(**row) for row in results]


@router.get("/usage")
async def _get_usage(
    request: Request, days: int = 7, account_id: str | None = None
) -> list[UsageRecord]:
    conn: sqlite3.Connection = request.app.state.conn
    params: dict[str, Any] = {"oldest_record": datetime.now(UTC) - timedelta(days=days)}
    account_clause = ""
    if account_id is not None:
        account_clause = " AND account_id = :account_id"
        params["account_id"] = account_id
    return list(
        iter_records(
            conn.execute(
                f"""
                SELECT
                    timestamp,
                    input_tokens,
                    output_tokens,
                    COALESCE(cache_creation_5m_input_tokens, 0)
                        + COALESCE(cache_creation_1h_input_tokens, 0)
                        AS cache_creation_input_tokens,
                    cache_read_input_tokens,
                    unified_5h_utilization,
                    unified_7d_utilization,
                    unified_5h_reset,
                    unified_7d_reset
                FROM responses
                WHERE timestamp > :oldest_record{account_clause}
                ORDER BY timestamp
                """,
                params,
            ),
            UsageRecord,
        )
    )


@router.get("/accounts")
async def _get_accounts(request: Request) -> list[AccountSummary]:
    conn: sqlite3.Connection = request.app.state.conn
    # Group by (account_id, model) so cost can be computed per model before
    # summing back up to the account. Totals (tokens, counts, timestamps) are
    # collapsed across models in Python.
    rows = conn.execute(
        """
        SELECT
            account_id,
            model,
            COUNT(*) AS message_count,
            MIN(timestamp) AS first_timestamp,
            MAX(timestamp) AS last_timestamp,
            SUM(input_tokens) AS input_tokens,
            SUM(output_tokens) AS output_tokens,
            SUM(cache_creation_5m_input_tokens) AS cache_creation_5m_input_tokens,
            SUM(cache_creation_1h_input_tokens) AS cache_creation_1h_input_tokens,
            SUM(cache_read_input_tokens) AS cache_read_input_tokens
        FROM responses
        WHERE account_id IS NOT NULL
        GROUP BY account_id, model
        """
    ).fetchall()

    def _dt(value: Any) -> datetime:
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(value)

    agg: dict[str, dict[str, Any]] = {}
    for (
        account_id,
        model,
        message_count,
        first_ts,
        last_ts,
        input_tokens,
        output_tokens,
        cache_5m,
        cache_1h,
        cache_read,
    ) in rows:
        a = agg.setdefault(
            account_id,
            {
                "account_id": account_id,
                "message_count": 0,
                "first_timestamp": None,
                "last_timestamp": None,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "cost_usd": 0.0,
                "cost_known": False,
            },
        )
        a["message_count"] += message_count
        first_dt, last_dt = _dt(first_ts), _dt(last_ts)
        a["first_timestamp"] = (
            first_dt
            if a["first_timestamp"] is None
            else min(a["first_timestamp"], first_dt)
        )
        a["last_timestamp"] = (
            last_dt
            if a["last_timestamp"] is None
            else max(a["last_timestamp"], last_dt)
        )
        a["input_tokens"] += input_tokens or 0
        a["output_tokens"] += output_tokens or 0
        a["cache_creation_input_tokens"] += (cache_5m or 0) + (cache_1h or 0)
        a["cache_read_input_tokens"] += cache_read or 0
        c = cost(model, input_tokens, output_tokens, cache_5m, cache_1h, cache_read)
        if c is not None:
            a["cost_usd"] += c
            a["cost_known"] = True

    summaries = [
        AccountSummary(
            account_id=a["account_id"],
            message_count=a["message_count"],
            first_timestamp=a["first_timestamp"],
            last_timestamp=a["last_timestamp"],
            input_tokens=a["input_tokens"],
            output_tokens=a["output_tokens"],
            cache_creation_input_tokens=a["cache_creation_input_tokens"],
            cache_read_input_tokens=a["cache_read_input_tokens"],
            cost_usd=a["cost_usd"] if a["cost_known"] else None,
        )
        for a in agg.values()
    ]
    summaries.sort(key=lambda s: s.message_count, reverse=True)
    return summaries


def _extract_preview(
    conn: sqlite3.Connection,
    message_hashes: bytes | None,
    payload: bytes | None,
    limit: int = 200,
) -> str | None:
    first = first_message(conn, message_hashes, payload)
    if not isinstance(first, dict):
        return None
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
            SUM(
                COALESCE(resp.cache_creation_5m_input_tokens, 0)
                + COALESCE(resp.cache_creation_1h_input_tokens, 0)
            ) AS cache_creation_input_tokens,
            SUM(resp.cache_read_input_tokens) AS cache_read_input_tokens,
            MAX(
                COALESCE(resp.input_tokens, 0)
                + COALESCE(resp.cache_creation_5m_input_tokens, 0)
                + COALESCE(resp.cache_creation_1h_input_tokens, 0)
                + COALESCE(resp.cache_read_input_tokens, 0)
            ) AS peak_context_tokens,
            s.cwd AS cwd,
            s.git_branch AS git_branch,
            s.is_sidechain AS is_sidechain,
            s.started_at AS started_at,
            s.title AS title
        FROM requests r
        LEFT JOIN responses resp ON resp.request_row_id = r.id
        LEFT JOIN sessions s ON s.session_id = r.session_id
        WHERE r.session_id IS NOT NULL
        GROUP BY r.session_id
        ORDER BY last_timestamp DESC
        """
    ).fetchall()

    # Fetch the earliest request per session for preview text. Prefer the dedup
    # columns; fall back to `payload` for legacy rows whose body failed to parse.
    # The subquery picks the row with the minimum timestamp per session and is
    # ~10x faster than an equivalent ROW_NUMBER() window.
    preview_rows = conn.execute(
        """
        SELECT session_id, message_hashes, payload
        FROM requests
        WHERE id IN (
            SELECT id FROM requests
            WHERE session_id IS NOT NULL
            GROUP BY session_id
            HAVING timestamp = MIN(timestamp)
        )
        """
    ).fetchall()
    previews: dict[str, str | None] = {
        session_id: _extract_preview(conn, message_hashes, payload)
        for session_id, message_hashes, payload in preview_rows
    }

    # Per-session cost: group responses by (session_id, model), compute cost
    # per group, sum per session. Sessions with only unknown-model rows end up
    # with cost_usd = None.
    cost_rows = conn.execute(
        """
        SELECT
            r.session_id,
            resp.model,
            SUM(resp.input_tokens),
            SUM(resp.output_tokens),
            SUM(resp.cache_creation_5m_input_tokens),
            SUM(resp.cache_creation_1h_input_tokens),
            SUM(resp.cache_read_input_tokens)
        FROM requests r
        JOIN responses resp ON resp.request_row_id = r.id
        WHERE r.session_id IS NOT NULL
        GROUP BY r.session_id, resp.model
        """
    ).fetchall()
    session_costs: dict[str, float | None] = {}
    for session_id, model, inp, out, cc_5m, cc_1h, cr in cost_rows:
        c = cost(model, inp, out, cc_5m, cc_1h, cr)
        if c is None:
            session_costs.setdefault(session_id, None)
            continue
        prev = session_costs.get(session_id)
        session_costs[session_id] = c if prev is None else prev + c

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
            cost_usd=session_costs.get(session_id),
            first_message_preview=previews.get(session_id),
            peak_context_tokens=peak_context_tokens or None,
            cwd=cwd,
            git_branch=git_branch,
            is_sidechain=bool(is_sidechain) if is_sidechain is not None else None,
            started_at=_dt(started_at) if started_at else None,
            title=title,
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
            title,
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
            r.system_hash AS system_hash,
            r.tools_hash AS tools_hash,
            r.message_hashes AS message_hashes,
            r.extras AS extras,
            r.payload AS request_payload,
            resp.status_code AS status_code,
            resp.timestamp AS response_timestamp,
            resp.payload AS response_payload,
            resp.input_tokens AS input_tokens,
            resp.output_tokens AS output_tokens,
            resp.cache_creation_5m_input_tokens AS cache_creation_5m_input_tokens,
            resp.cache_creation_1h_input_tokens AS cache_creation_1h_input_tokens,
            resp.cache_read_input_tokens AS cache_read_input_tokens,
            resp.unified_5h_utilization AS unified_5h_utilization,
            resp.unified_7d_utilization AS unified_7d_utilization,
            resp.model AS model
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
        "SELECT cwd, git_branch, is_sidechain, started_at, title FROM sessions WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    cwd, git_branch, is_sidechain, started_at, title = (
        meta if meta else (None, None, None, None, None)
    )

    turns: list[Turn] = []
    for row in rows:
        (
            request_id,
            request_timestamp,
            system_hash,
            tools_hash,
            message_hashes,
            extras,
            request_payload,
            status_code,
            response_timestamp,
            response_payload,
            input_tokens,
            output_tokens,
            cache_creation_5m_input_tokens,
            cache_creation_1h_input_tokens,
            cache_read_input_tokens,
            unified_5h_utilization,
            unified_7d_utilization,
            model,
        ) = row

        try:
            parsed_request = reconstruct_payload(
                conn,
                system_hash,
                tools_hash,
                message_hashes,
                extras,
                request_payload,
            )
        except (ValueError, KeyError):
            parsed_request = None

        response: ResponseRecord | None = None
        if status_code is not None:
            decoded_payload: str | None = None
            if response_payload:
                try:
                    decoded_payload = decompress(response_payload).decode(
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
                cache_creation_input_tokens=(cache_creation_5m_input_tokens or 0)
                + (cache_creation_1h_input_tokens or 0)
                if (
                    cache_creation_5m_input_tokens is not None
                    or cache_creation_1h_input_tokens is not None
                )
                else None,
                cache_read_input_tokens=cache_read_input_tokens,
                unified_5h_utilization=unified_5h_utilization,
                unified_7d_utilization=unified_7d_utilization,
                model=model,
                cost_usd=cost(
                    model,
                    input_tokens,
                    output_tokens,
                    cache_creation_5m_input_tokens,
                    cache_creation_1h_input_tokens,
                    cache_read_input_tokens,
                ),
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
        title=title,
    )


@router.patch("/sessions/{session_id}")
async def _update_session(
    request: Request, session_id: str, body: SessionUpdate
) -> dict[str, str | None]:
    conn: sqlite3.Connection = request.app.state.conn
    # Empty/whitespace string clears the title; NULL makes the frontend's
    # "Untitled" fallback kick in.
    title = body.title.strip() if body.title is not None else None
    if title == "":
        title = None
    cursor = conn.execute(
        "UPDATE sessions SET title = ? WHERE session_id = ?",
        (title, session_id),
    )
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="session not found")
    conn.commit()
    return {"session_id": session_id, "title": title}
