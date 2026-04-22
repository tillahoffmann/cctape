# cctape

A local proxy for Claude Code that archives every request and response to a SQLite database, with a web UI for browsing sessions, searching transcripts, inspecting raw API traffic, and tracking token usage and cost.

## Quick start

Run cctape with [uv](https://docs.astral.sh/uv/):

```bash
uvx cctape
```

On first run, your browser opens to a setup page at <http://127.0.0.1:5555/setup> with the one env var you need to copy. After that, run `claude` as usual — sessions appear in the UI as they happen.

Pass `--no-browser` to skip the auto-open.

## Configuration

### Shell

Add to `~/.zshrc` (or `~/.bashrc`):

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:5555/proxy
```

### Claude Code VS Code extension

Add to user settings:

```json
"claudeCode.environmentVariables": [
    {"name": "ANTHROPIC_BASE_URL", "value": "http://127.0.0.1:5555/proxy"}
]
```

### Flags and environment variables

- `--host` / `--port` — bind address (defaults: `127.0.0.1`, `5555`)
- `--no-browser` — don't auto-open the browser on first run
- `CCTAPE_DB` — database path (default: `~/.cctape/cctape.db`)

## Running it as a service

cctape is a regular HTTP server; run it under whatever supervisor you like (launchd, systemd, tmux, etc.). Once it's up on `127.0.0.1:5555` and your `ANTHROPIC_BASE_URL` points at `/proxy`, it just stays out of the way.

## Development

Clone the repo and run the frontend + backend separately during development.

Build the frontend once (output is served by the backend from `backend/src/cctape/static/`):

```bash
cd frontend && npm install && npm run build
```

Start the backend with reload:

```bash
cd backend && uv run uvicorn --reload --factory --port=5555 cctape:create_app
```

Open <http://127.0.0.1:5555>.

The frontend can also run on its own dev server with HMR:

```bash
cd frontend && npm run dev
```

Pre-commit hooks run ruff, pyright, pytest, eslint, and tsc.
