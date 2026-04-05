# Jarvis Backend — Terminal-first AI Coding Agent

## Architecture

```
/app
  /agent        - Agent runtime (state machine, planner/coder/summarizer)
  /api          - FastAPI routes (REST + WebSocket)
  /db           - SQLite session/models
  /memory       - Working/session/repo memory management
  /models       - Model provider abstraction (OpenAI, Anthropic, Mock)
  /repo         - Repo scanning, chunking, grep/semantic search
  /schemas      - Pydantic request/response types
  /security     - Workspace confinement, command classification
  /services     - Business logic (session CRUD)
  /tools        - File tools, command executor, tool manager
```

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Set environment (optional, for real models)
export JARVIS_MODEL_PROVIDER=openai
export JARVIS_API_KEY=sk-your-key
export JARVIS_PLANNER_MODEL=gpt-4o
export JARVIS_CODER_MODEL=gpt-4o
export JARVIS_SUMMARIZER_MODEL=gpt-4o-mini

# Run
uvicorn main:app --reload
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health check |
| POST | `/api/sessions` | Create session |
| GET | `/api/sessions` | List sessions |
| GET | `/api/sessions/{id}` | Get session |
| POST | `/api/sessions/{id}/message` | Send message, start agent |
| WS | `/api/sessions/{id}/stream` | Stream agent responses |
| POST | `/api/sessions/{id}/approve` | Approve/reject action |
| GET | `/api/sessions/{id}/messages` | Get message history |
| GET | `/api/sessions/{id}/tools` | Get tool call history |
| POST | `/api/repos/open` | Open repo path |
| POST | `/api/repos/index` | Index repo for search |
| GET | `/api/repos/files` | List repo files |
| POST | `/api/tools/command` | Run shell command |
| POST | `/api/tools/patch/apply` | Apply unified diff |
| POST | `/api/tools/search` | Grep/semantic search |
| GET | `/api/diffs/{id}` | Get diff by ID |
| POST | `/api/modes/set` | Set agent mode |
| GET | `/api/modes/{mode}/permissions` | Get mode permissions |

## Agent Modes

- **ask**: Read-only. No file writes or shell commands.
- **edit**: Can propose patches. No shell execution.
- **agent**: Full tool access with approval gates for writes/system commands.
- **danger**: Unrestricted. For trusted projects only.

## Memory System

Three layers:
1. **Working memory** — current task state, plan, files touched, failures (per session)
2. **Session memory** — rolling summaries of completed sessions
3. **Repo memory** — indexed code chunks with symbol extraction

## Tests

```bash
pytest tests/ -v
```
