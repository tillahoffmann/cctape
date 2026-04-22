# cctape

A local proxy for Claude Code that archives every request and response to a SQLite database, with a web UI for browsing sessions, searching transcripts, and inspecting raw API traffic.

## Run

Build the frontend once (output is served by the backend from `backend/src/cctape/static/`):

```bash
cd frontend && npm install && npm run build
```

Start the backend:

```bash
cd backend && uv run uvicorn --reload --factory --port=5555 cctape:create_app
```

Point Claude Code at the proxy:

```bash
ANTHROPIC_BASE_URL=http://127.0.0.1:5555/proxy claude
```

Open the UI at http://127.0.0.1:5555.

## Persistent setup

Add to `~/.zshrc` (or `~/.bashrc`):

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:5555/proxy
```

For the Claude Code VS Code extension, add to user settings:

```json
"claudeCode.environmentVariables": [
    {"name": "ANTHROPIC_BASE_URL", "value": "http://127.0.0.1:5555/proxy"}
]
```
