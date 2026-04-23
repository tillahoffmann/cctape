import argparse
import logging
import os
import sqlite3
import threading
import webbrowser
from contextlib import AsyncExitStack, asynccontextmanager, closing
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

from .api import router as api_router
from .fts import backfill as fts_backfill
from .fts import ensure_schema as fts_ensure_schema
from .proxy import router as proxy_router
from .sessions import sync_all as sync_sessions

# Defensive import: the MCP server is a new feature and must not be able to
# prevent the proxy from coming up. If the `mcp` package is broken at import
# time we log and continue without the MCP mount.
try:
    from .mcp_server import build_mcp as _build_mcp
except Exception:  # noqa: BLE001
    logging.getLogger(__name__).exception("MCP import failed; continuing without MCP")
    _build_mcp = None  # type: ignore[assignment]

STATIC_DIR = Path(__file__).parent / "static"

# Read endpoints whose output depends only on the DB's current contents.
# The ETag middleware attaches a DB-mtime ETag to these responses and
# returns 304 when the client already has a fresh copy. The proxy path and
# anything else fall straight through.
_CACHEABLE_PATHS = frozenset(
    {
        "/api/sessions",
        "/api/usage",
        "/api/accounts",
        "/api/search",
    }
)


def _db_etag(db_path: str) -> str:
    # Every write commit bumps the DB file mtime, so hashing it invalidates
    # cached client responses without running a query. Quoted per RFC 7232.
    try:
        mtime_ns = os.stat(db_path).st_mtime_ns
    except OSError:
        mtime_ns = 0
    return f'"{mtime_ns:x}"'


# Datetime conversion based on the somewhat dubious recommendations at
# https://docs.python.org/3/library/sqlite3.html#sqlite3-adapter-converter-recipes
def adapt_datetime_iso(value: datetime):
    return value.isoformat()


def convert_datetime(value: bytes):
    return datetime.fromisoformat(value.decode())


sqlite3.register_adapter(datetime, adapt_datetime_iso)
sqlite3.register_converter("datetime", convert_datetime)


@asynccontextmanager
async def lifespan(app: FastAPI):
    database = (
        Path(os.environ.get("CCTAPE_DB", "~/.cctape/cctape.db")).expanduser().resolve()
    )
    database.parent.mkdir(exist_ok=True, parents=True)
    db_exists = database.is_file()

    # Nested withs because one is async and one is sync.
    async with httpx.AsyncClient(timeout=None) as client:
        with closing(sqlite3.connect(database)) as conn:
            app.state.http_client = client
            app.state.conn = conn
            app.state.db_path = str(database)

            conn.execute("PRAGMA foreign_keys = ON")
            if not db_exists:
                schema_path = Path(__file__).parent / "schema.sql"
                conn.executescript(schema_path.read_text())
            # Idempotent index creation for databases that predate this index.
            conn.execute(
                "CREATE INDEX IF NOT EXISTS responses_timestamp ON responses(timestamp)"
            )
            # Migrate responses.cache_creation_input_tokens (single total) to the
            # split cache_creation_{5m,1h}_input_tokens columns. Pre-split rows
            # are attributed entirely to 5m (the old default TTL). Runs before
            # any query touches the new columns so the app never sees the old
            # shape.
            resp_cols = {
                row[1]
                for row in conn.execute("PRAGMA table_info(responses)").fetchall()
            }
            if "cache_creation_5m_input_tokens" not in resp_cols:
                with conn:
                    conn.execute(
                        "ALTER TABLE responses "
                        "ADD COLUMN cache_creation_5m_input_tokens INTEGER"
                    )
                    conn.execute(
                        "ALTER TABLE responses "
                        "ADD COLUMN cache_creation_1h_input_tokens INTEGER"
                    )
                    if "cache_creation_input_tokens" in resp_cols:
                        conn.execute(
                            "UPDATE responses SET cache_creation_5m_input_tokens = "
                            "cache_creation_input_tokens"
                        )
                        conn.execute(
                            "ALTER TABLE responses "
                            "DROP COLUMN cache_creation_input_tokens"
                        )
            # Idempotent: creates FTS tables on pre-existing databases and
            # indexes any blobs not yet covered. Wrapped so a failure here
            # cannot prevent the proxy from coming up.
            try:
                fts_ensure_schema(conn)
                fts_backfill(conn)
            except Exception:
                logging.getLogger(__name__).exception("FTS setup failed")
            sync_sessions(conn)

            # Enter the MCP sub-app's lifespan so its streamable-HTTP session
            # manager is initialized. Swallow any failure — per the proxy
            # deadlock rule, MCP must not prevent the proxy from coming up.
            async with AsyncExitStack() as stack:
                mcp_app = getattr(app.state, "mcp_app", None)
                if mcp_app is not None:
                    try:
                        await stack.enter_async_context(
                            mcp_app.router.lifespan_context(mcp_app)
                        )
                    except Exception:
                        logging.getLogger(__name__).exception("MCP lifespan failed")
                yield


def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan)

    @app.middleware("http")
    async def etag_middleware(request: Request, call_next):
        # Narrow scope: only exact cacheable GETs. Everything else (proxy,
        # /api/sessions/{id}, PATCH, static) falls through untouched.
        if request.method != "GET" or request.url.path not in _CACHEABLE_PATHS:
            return await call_next(request)
        etag = _db_etag(request.app.state.db_path)
        if request.headers.get("if-none-match") == etag:
            return Response(
                status_code=304,
                headers={"ETag": etag, "Cache-Control": "no-cache"},
            )
        response = await call_next(request)
        response.headers["ETag"] = etag
        response.headers["Cache-Control"] = "no-cache"
        return response

    app.include_router(api_router)
    app.include_router(proxy_router)

    # Mount the MCP server at /mcp BEFORE the static catch-all at "/" so its
    # routes win. Built defensively — if construction fails, we log and skip
    # the mount (per the proxy deadlock rule in CLAUDE.md).
    #
    # Starlette's mount matches `/mcp/<rest>` but NOT the bare `/mcp` (without
    # trailing slash). MCP clients commonly use `/mcp` with no slash, so we
    # add a small redirect route. The mount itself serves `/mcp/` (the real
    # streamable-HTTP endpoint).
    if _build_mcp is not None:
        try:
            _, mcp_app = _build_mcp(lambda: app.state.conn)
            app.state.mcp_app = mcp_app
            app.mount("/mcp", mcp_app)

            from starlette.responses import RedirectResponse

            @app.api_route(
                "/mcp",
                methods=["GET", "POST", "DELETE"],
                include_in_schema=False,
            )
            async def _mcp_trailing_redirect() -> RedirectResponse:
                # 308 preserves method + body, which matters for POST initialize.
                return RedirectResponse(url="/mcp/", status_code=308)
        except Exception:
            logging.getLogger(__name__).exception("MCP mount failed")

    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


def _db_exists() -> bool:
    database = Path(os.environ.get("CCTAPE_DB", "~/.cctape/cctape.db")).expanduser()
    return database.is_file()


def run() -> None:
    """CLI entrypoint for `cctape` / `uvx cctape`."""
    import uvicorn

    parser = argparse.ArgumentParser(prog="cctape", description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5555)
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open the browser automatically on first run.",
    )
    args = parser.parse_args()

    if not args.no_browser and not _db_exists():
        # First run: open /setup once the server is up. Delay so uvicorn
        # is actually listening before we hit the URL.
        url = f"http://{args.host}:{args.port}/setup"
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    uvicorn.run(
        "cctape:create_app",
        host=args.host,
        port=args.port,
        factory=True,
    )
