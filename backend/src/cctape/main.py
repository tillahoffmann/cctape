import os
import sqlite3
from contextlib import asynccontextmanager, closing
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api import router as api_router
from .fts import backfill as fts_backfill
from .fts import ensure_schema as fts_ensure_schema
from .proxy import router as proxy_router
from .sessions import sync_all as sync_sessions

STATIC_DIR = Path(__file__).parent / "static"


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
                import logging

                logging.getLogger(__name__).exception("FTS setup failed")
            sync_sessions(conn)
            yield


def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan)
    app.include_router(api_router)
    app.include_router(proxy_router)
    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app
