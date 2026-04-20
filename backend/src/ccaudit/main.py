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
        Path(os.environ.get("CCAUDIT_DB", "~/.ccaudit/ccaudit.db"))
        .expanduser()
        .resolve()
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
