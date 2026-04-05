"""REST API router for Jarvis backend."""

from __future__ import annotations

import asyncio
import json
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


@router.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


@router.post("/sessions", response_model=SessionResponse)
async def create_session(body: SessionCreate):
    sid = await memory.create_session(body.name)
    sessions = await memory.list_sessions()
    return next(s for s in sessions if s["id"] == sid)


@router.get("/sessions")
async def list_sessions():
    return await memory.list_sessions()


@router.get("/sessions/{session_id}")
async def get_session(session_id: int):
    sessions = await memory.list_sessions()
    for s in sessions:
        if s["id"] == session_id:
            return s
    return JSONResponse(status_code=404, content={"error": "Session not found"})


@router.get("/startup/resume", response_model=ResumeResponse)
async def resume_session():
    result = await memory.resume_last_session()
    return result


@router.get("/memory/project")
async def get_project_memory(session_id: int = 0):
    if not session_id:
        return {}
    return await memory.repo.load(session_id)


@router.post("/memory/project")
async def save_project_memory(session_id: int, data: dict):
    await memory.save_project_memory(session_id, data)
    return {"ok": True}


@router.post("/sessions/{session_id}/save")
async def save_session(session_id: int, data: dict = None):
    name = data.get("name") if data else None
    await memory.save_session(session_id, name)
    return {"ok": True}


@router.post("/sessions/{session_id}/message")
async def send_message(session_id: int, body: MessageRequest):
    """Create a new message and trigger agent response via WebSocket."""
    # Store user message
    await memory.store_message(session_id, "user", body.prompt, {"timestamp": datetime.utcnow().isoformat()})

    # Set agent planning
    runtime.set(session_id, AgentState.PLANNING)

    # Create session memory context
    context = await memory.build_context(session_id=session_id)

    # Mock agent response (replace with real LLM call)
    tool_calls = []
    response_text = _generate_mock_response(body.prompt)

    runtime.add_tool_call(session_id, ToolCall(tool="think", args={"reasoning": response_text[:200]}))
    runtime.set(session_id, AgentState.DONE, response=response_text)

    # Store assistant message
    await memory.store_message(session_id, "assistant", response_text, {"tool_calls": [tc.to_dict() for tc in runtime.get(session_id).tool_calls]})

    return {"ok": True, "response": response_text, "state": runtime.get(session_id).state.value}


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

            if not prompt:
                continue

            # Store and process
            await memory.store_message(session_id, "user", prompt)
            runtime.set(session_id, AgentState.PLANNING)
            await _ws_send(websocket, {"type": "status", "state": "PLANNING", "message": "Thinking..."})

            await asyncio.sleep(0.3)

            context = await memory.build_context(session_id=session_id)
            response_text = _generate_mock_response(prompt)
            response_text = f"Session #{session_id}: {response_text}"

            runtime.add_tool_call(session_id, ToolCall(tool="think", args={"context": response_text[:150]}))
            runtime.set(session_id, AgentState.DONE, response=response_text)

            await memory.store_message(
                session_id, "assistant", response_text,
                {"tool_calls": [tc.to_dict() for tc in runtime.get(session_id).tool_calls]},
            )

            await _ws_send(websocket, {
                "type": "tool_call",
                "tool": "think",
                "args": {"context": response_text[:150]},
            })
            await _ws_send(websocket, {
                "type": "assistant",
                "content": response_text,
                "state": "DONE",
            })

    except WebSocketDisconnect:
        pass


@router.get("/agent/{session_id}/status")
async def agent_status(session_id: int):
    return runtime.status(session_id)


# Helpers

async def _ws_send(ws: WebSocket, data: dict):
    await ws.send_text(json.dumps(data))


def _generate_mock_response(prompt: str) -> str:
    """Mock agent response. Replace with real LLM integration."""
    words = prompt.lower().split()
    if any(w in words for w in ["fix", "bug", "error", "broken"]):
        return (
            "I'll investigate the bug. Let me:\n"
            "1. Read the relevant source files\n"
            "2. Identify the root cause\n"
            "3. Propose a specific fix\n"
            f"\nPrompt was: \"{prompt[:100]}\"... [Mock response - connect a real LLM]"
        )
    if any(w in words for w in ["create", "add", "new", "build", "make"]):
        return (
            "I'll build that for you. Plan:\n"
            "1. Analyze existing code patterns\n"
            "2. Create the new files with proper structure\n"
            "3. Run tests\n"
            f"\nRequested: \"{prompt[:100]}\"... [Mock response - connect a real LLM]"
        )
    return (
        f"I understand. You said: \"{prompt[:200]}\"\n\n"
        "This is a mock response from the MVP backend. "
        "To enable real AI responses, set JARVIS_MODEL_PROVIDER and configure your LLM API key."
    )
