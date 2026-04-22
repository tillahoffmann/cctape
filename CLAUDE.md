# Working in this repo

## Project

cctape is a local proxy for Claude Code. It sits between `claude` and the Anthropic API (`ANTHROPIC_BASE_URL=http://127.0.0.1:5555/proxy`), forwards every request/response, and archives them into a SQLite database at `~/.cctape/cctape.db`. A small FastAPI backend + React (Vite/Shadcn) frontend expose a UI at `/` for browsing sessions, searching transcripts, and viewing token usage and cost. Layout: `backend/src/cctape/` (Python, `src/` layout, hatchling-built wheel bundles `static/`), `frontend/` (Vite → builds into `backend/src/cctape/static/`). Ships on PyPI and runs via `uvx cctape`.

## ⚠️ Proxy deadlock — read first

When working on cctape, **you are almost certainly running through the proxy yourself** (`claude` has `ANTHROPIC_BASE_URL` pointed at it). If you break the proxy, your next tool call can't reach Anthropic and the session is dead — the user has to manually recover.

During dev the user typically runs cctape under `uvicorn --reload`, so edits reload live. That means:

1. **The code must be executable at every point during a change.** Don't leave imports broken, the app un-instantiable, or the proxy path half-refactored between edits. Uvicorn will reload into whatever state you left the tree in, and if that fails to start, you're stuck.
2. **Don't try to "restart" or "verify the server starts" yourself.** By the time a restart would help, you're already dead. There's no recovery path from inside the session — tell the user what happened instead of thrashing.
3. **Be especially careful around `proxy.py`, `storage.py`, `main.py` lifespan, and FastAPI app assembly.** These are the critical path.

## Database migrations must be proactive

The live database is in active use by the very `claude` session you're running in. Every proxied request writes to it. So:

1. **Migrate the live DB first, *before* touching any query that depends on the new shape.** Add the column (idempotently, `CREATE ... IF NOT EXISTS` / `ALTER TABLE ... ADD COLUMN` guarded by a `PRAGMA table_info` check) in `main.py`'s lifespan startup path, *and* run the ALTER directly against `~/.cctape/cctape.db` via `sqlite3` so it's applied now — not on next restart.
2. **Only then update queries, writers, or frontend code that reads the new column.** If you flip the order, every new request blows up in the running process until the next restart, which you can't trigger (see deadlock above).
3. **Same for dropping/renaming columns** — widen to "both old and new present" first, migrate code, then narrow.

See the `cache_creation_input_tokens` → `cache_creation_{5m,1h}_input_tokens` migration in `main.py` for the pattern: idempotent ALTERs guarded by `PRAGMA table_info`, run inside lifespan on every startup.
