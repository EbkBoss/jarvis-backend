"""REST API router for Jarvis backend."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from app.memory.manager import memory
from app.services.agent import AgentState, AgentMode, ToolCall, runtime
from app.schemas.session import (
    MessageRequest,
    SessionCreate,
    SessionResponse,
    AgentStatus,
    ResumeResponse,
)

router = APIRouter()


# ─── Health ──────────────────────────────────────────

@router.get("/health")
async def health():
    try:
        from app.config import settings
    except Exception:
        settings = None
    sessions = await memory.list_sessions()
    active = [s for s in sessions if not s.get("is_archived")]
    return JSONResponse({
        "status": "healthy",
        "version": "0.1.0",
        "active_sessions": len(active),
        "db_status": "ok",
    })


# ─── Sessions ───────────────────────────────────────

@router.post("/sessions")
async def create_session(body: dict):
    """Create session (accepts Android body)."""
    sid = await memory.create_session(body.get("title", "New Session"))
    return await _get_session_response(sid)


@router.get("/sessions")
async def list_sessions():
    sessions = await memory.list_sessions()
    # Enrich with agent state
    result = []
    for s in sessions:
        state = runtime.get(s["id"])
        result.append({
            "id": s["id"],
            "name": s["name"],
            "title": s["name"],
            "status": "active",
            "mode": "ASK",
            "cwd": ".",
            "repo_path": None,
            "created_at": s["created_at"],
            "updated_at": s["updated_at"],
        })
    return result


@router.get("/sessions/{session_id}")
async def get_session(session_id: int):
    sessions = await memory.list_sessions()
    for s in sessions:
        if s["id"] == session_id:
            return s
    return JSONResponse(status_code=404, content={"error": "Session not found"})


async def _get_session_response(sid: int):
    sessions = await memory.list_sessions()
    for s in sessions:
        if s["id"] == sid:
            return {
                "id": s["id"],
                "name": s["name"],
                "title": s["name"],
                "status": "active",
                "mode": "ASK",
                "cwd": ".",
                "repo_path": None,
                "created_at": s["created_at"],
                "updated_at": s["updated_at"],
            }
    return {}


# ─── Messages ───────────────────────────────────────

@router.get("/sessions/{session_id}/messages")
async def get_messages(session_id: int):
    messages = await memory.session.load_history(session_id, limit=100)
    # Convert to expected format
    result = []
    for i, m in enumerate(messages):
        result.append({
            "id": i + 1,
            "session_id": session_id,
            "role": m["role"].upper(),
            "content": m["content"],
            "metadata": m.get("meta", {}),
            "created_at": datetime.utcnow().isoformat(),
        })
    return list(reversed(result))  # chronological


@router.post("/sessions/{session_id}/message")
async def send_message(session_id: int, body: dict | MessageRequest):
    """Create a message and return immediate response."""
    prompt = body.prompt if isinstance(body, MessageRequest) else body.get("prompt", "")
    session_id = body.session_id if isinstance(body, MessageRequest) else body.get("session_id", session_id)

    # Store user message
    await memory.store_message(session_id, "user", prompt, {"timestamp": datetime.utcnow().isoformat()})

    # Set agent planning
    runtime.set(session_id, AgentState.PLANNING)
    context = await memory.build_context(session_id=session_id)

    # Generate response
    response_text = _generate_response(prompt)

    runtime.add_tool_call(session_id, ToolCall(tool="think", args={"reasoning": response_text[:200]}))
    runtime.set(session_id, AgentState.DONE, response=response_text)

    # Store assistant message
    await memory.store_message(
        session_id, "assistant", response_text,
        {"tool_calls": [tc.to_dict() for tc in runtime.get(session_id).tool_calls]},
    )

    return {"ok": True, "response": response_text, "state": runtime.get(session_id).state.value}


# ─── WebSocket streaming ─────────────────────────────

@router.websocket("/ws/{session_id}/stream")
@router.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: int):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            try:
                parsed = json.loads(data)
                prompt = parsed.get("prompt", "")
            except json.JSONDecodeError:
                prompt = data

            if not prompt.strip():
                continue

            # Store user message
            await memory.store_message(session_id, "user", prompt)
            runtime.set(session_id, AgentState.PLANNING)
            await _ws_send(websocket, {"type": "status", "state": "PLANNING", "message": "Thinking..."})

            await asyncio.sleep(0.2)

            context = await memory.build_context(session_id=session_id)
            response_text = _generate_response(prompt)
            response_text = response_text

            # Stream chunks
            steps = ["Analyzing...", "Planning approach...", "Executing...", "Done!"]
            for step in steps[:2]:
                await _ws_send(websocket, {"type": "tool_call", "content": "", "metadata": {"tool": "think", "detail": step}})
                await asyncio.sleep(0.15)

            await _ws_send(websocket, {"type": "done", "content": response_text, "metadata": {"state": "DONE"}})

            runtime.add_tool_call(session_id, ToolCall(tool="think", args={"context": response_text[:150]}))
            runtime.set(session_id, AgentState.DONE, response=response_text)

            await memory.store_message(
                session_id, "assistant", response_text,
                {"tool_calls": [tc.to_dict() for tc in runtime.get(session_id).tool_calls]},
            )

    except WebSocketDisconnect:
        pass


# ─── Agent ───────────────────────────────────────────

@router.get("/agent/{session_id}/status")
async def agent_status(session_id: int):
    return runtime.status(session_id)


@router.post("/agent/{session_id}/mode")
async def set_agent_mode(session_id: int, body: dict):
    mode = body.get("mode", "ask")
    runtime.set(session_id, AgentState.IDLE)
    return {"ok": True, "mode": mode}


# ─── Mode permissions ────────────────────────────────

@router.get("/mode-permissions")
async def get_mode_permissions(mode: str = "ask"):
    perms = {
        "ask": {"read": True, "edit": False, "execute": False},
        "edit": {"read": True, "edit": True, "execute": False},
        "agent": {"read": True, "edit": True, "execute": True},
        "danger": {"read": True, "edit": True, "execute": True, "unrestricted": True},
    }
    return perms.get(mode, perms["ask"])


@router.post("/mode-permissions")
async def update_mode_permissions(body: dict):
    return {"ok": True}


# ─── Commands (terminal) ────────────────────────────

@router.post("/commands")
async def run_command(body: dict):
    cmd = body.get("command", "")
    cwd = body.get("cwd", ".")
    timeout = body.get("timeout", 30)

    try:
        result = subprocess.run(
            cmd, shell=True, cwd=cwd,
            capture_output=True, text=True,
            timeout=timeout
        )
        return {
            "success": result.returncode == 0,
            "output": result.stdout or result.stderr,
            "metadata": {
                "exit_code": result.returncode,
                "duration_ms": 0,
                "classification": "ok" if result.returncode == 0 else "error",
            },
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "output": "Command timed out",
            "metadata": {"exit_code": -1, "duration_ms": timeout * 1000, "classification": "timeout"},
        }
    except Exception as e:
        return {
            "success": False,
            "output": str(e),
            "metadata": {"exit_code": -1, "duration_ms": 0, "classification": "error"},
        }


# ─── Search ──────────────────────────────────────────

@router.get("/search")
async def search(q: str = "", type: str = "grep", path: str = ".", limit: int = 20):
    if not q:
        return []
    results = []
    try:
        cmd = ["grep", "-rnI", "--include=*.py", "--include=*.kt", "--include=*.java", "--include=*.xml", "--include=*.json", q, path]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        for line in proc.stdout.strip().split("\n")[:limit]:
            if ":" in line:
                parts = line.split(":", 2)
                results.append({"file_path": parts[0], "line_number": int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None, "content": parts[-1], "score": None})
    except Exception:
        pass
    return results


@router.post("/search")
async def search_post(body: dict):
    return await search(
        body.get("q", ""),
        body.get("type", "grep"),
        body.get("path", "."),
        body.get("limit", 20),
    )


# ─── Repo files ──────────────────────────────────────

@router.get("/files/repo")
async def list_repo_files(path: str = "."):
    try:
        target = Path(path).resolve()
        items = []
        for p in target.rglob("*"):
            if p.is_file() and not p.name.startswith("_") and ".git/" not in str(p):
                ext = p.suffix.lower()
                items.append({
                    "path": str(p.relative_to(target)),
                    "language": _lang_from_ext(ext),
                    "size_bytes": p.stat().st_size if p.exists() else 0,
                    "is_binary": ext in (".png", ".jpg", ".jpeg", ".gif", ".pdf", ".exe", ".so", ".dll", ".db"),
                    "is_config": p.suffix in (".yml", ".yaml", ".json", ".toml", ".cfg", ".ini", ".xml"),
                    "is_readme": p.name.lower().startswith("readme"),
                    "is_test": "test" in p.stem.lower() or "spec" in p.stem.lower(),
                })
            if len(items) > 200:
                break
        return items[:100]
    except Exception:
        return []


@router.get("/files/repo/{path:path}")
async def open_repo(path: str):
    return await list_repo_files(path)


def _lang_from_ext(ext: str) -> str | None:
    mapping = {
        ".py": "python", ".kt": "kotlin", ".java": "java", ".js": "javascript",
        ".ts": "typescript", ".rs": "rust", ".go": "go",
        ".yml": "yaml", ".yaml": "yaml", ".json": "json", ".xml": "xml",
        ".md": "markdown", ".sh": "shell", ".bat": "batch", ".toml": "toml",
    }
    return mapping.get(ext)


@router.post("/files/repo")
async def get_file(body: dict):
    """Get file contents."""
    file_path = body.get("path", "")
    try:
        content = Path(file_path).read_text(encoding="utf-8")
        return {"path": file_path, "content": content}
    except Exception as e:
        return {"error": str(e)}


# ─── Patches ─────────────────────────────────────────

@router.post("/diffs")
async def apply_diff(body: dict):
    patch_text = body.get("patch_text", "")
    file_path = body.get("file_path", "")
    return {
        "ok": True,
        "patch": patch_text,
        "file": file_path,
        "status": "pending",
    }


@router.get("/diffs/{diff_id}")
async def get_diff(diff_id: int):
    return {"id": diff_id, "status": "pending", "patch": "", "file": ""}


# ─── Approvals ───────────────────────────────────────

@router.post("/approvals/{approval_id}")
async def approve_action(approval_id: str, body: dict):
    approved = body.get("approved", False)
    return {"ok": True, "approved": approved}


# ─── Startup resume ──────────────────────────────────

@router.get("/startup/resume")
async def resume_session():
    result = await memory.resume_last_session()
    return result


# ─── Project memory ──────────────────────────────────

@router.get("/memory/project")
async def get_project_memory(session_id: int = 0):
    if not session_id:
        return {}
    return await memory.repo.load(session_id)


@router.post("/memory/project")
async def save_project_memory(session_id: int, data: dict = {}):
    await memory.save_project_memory(session_id, data)
    return {"ok": True}


# ─── Save session ────────────────────────────────────

@router.post("/sessions/{session_id}/save")
async def save_session(session_id: int, body: dict = {}):
    name = body.get("name") if body else None
    await memory.save_session(session_id, name)
    return {"ok": True}


# ─── Helpers ────────────────────────────────────────

async def _ws_send(ws: WebSocket, data: dict):
    await ws.send_text(json.dumps(data))


def _generate_response(prompt: str) -> str:
    """Generate response. Replace with real LLM when API key is configured."""
    words = prompt.lower().split()
    if any(w in words for w in ["fix", "bug", "error", "broken"]):
        return (
            "I'll investigate the bug. Let me:\n"
            "1. Read the relevant source files\n"
            "2. Identify the root cause\n"
            "3. Propose a specific fix\n\n"
            f"Prompt: \"{prompt[:100]}\"... [Mock response - connect a real LLM]"
        )
    if any(w in words for w in ["create", "add", "new", "build", "make"]):
        return (
            "I'll build that for you. Plan:\n"
            "1. Analyze existing code patterns\n"
            "2. Create the new files with proper structure\n"
            "3. Run tests\n\n"
            f"Requested: \"{prompt[:100]}\"... [Mock response - connect a real LLM]"
        )
    return (
        f"I understand. You said: \"{prompt[:200]}\"\n\n"
        "This is a mock response from the Jarvis MVP. "
        "To enable real AI responses, configure your LLM provider via JARVIS_MODEL_PROVIDER and API key."
    )
