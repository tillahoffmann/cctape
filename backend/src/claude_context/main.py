import os
import sqlite3
from contextlib import asynccontextmanager, closing
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .proxy import router as proxy_router

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
        Path(os.environ.get("CLAUDE_CONTEXT_DB", "~/.claude-context/claude-context.db"))
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

            conn.execute("PRAGMA foreign_keys = ON")
            if not db_exists:
                schema_path = Path(__file__).parent / "schema.sql"
                conn.executescript(schema_path.read_text())
            yield


def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan)
    app.include_router(proxy_router)
    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app
