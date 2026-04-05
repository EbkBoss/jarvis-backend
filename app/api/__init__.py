"""REST API router for Jarvis backend."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, UploadFile, File, Form
from fastapi.responses import JSONResponse, StreamingResponse

from app.memory.manager import memory
from app.services.agent import AgentState, AgentMode, ToolCall, runtime
from app.services.llm_provider import llm_chat
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
    response_text = await llm_chat(prompt, context)

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
            response_text = await llm_chat(prompt, context)
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


@router.post("/sessions/{session_id}/approve")
async def approve_action_session(session_id: int, body: dict):
    approved = body.get("approved", False)
    return {"ok": True, "approved": approved}


# ─── Repos ───────────────────────────────────────────

@router.post("/repos/open")
async def repo_open(body: dict):
    """Open a repo at a given path."""
    path = body.get("path", ".")
    return await list_repo_files(path)


@router.post("/repos/index")
async def repo_index(body: dict):
    """Index a repo (mock)."""
    path = body.get("path", ".")
    return {"status": "indexed", "path": path, "files": 0}


@router.get("/repos/files")
async def repo_files(path: str = ".", max_depth: int = 3):
    return await list_repo_files(path)


# ─── Tools ───────────────────────────────────────────

@router.post("/tools/command")
async def tool_command(body: dict):
    """Run a terminal command."""
    return await run_command(body)


@router.post("/tools/patch/apply")
async def tool_patch(body: dict):
    """Apply a patch."""
    patch_text = body.get("patch_text", "")
    file_path = body.get("file_path", "")
    return {"ok": True, "patch": patch_text, "file": file_path, "status": "pending"}


@router.post("/tools/search")
async def tool_search(body: dict):
    """Search code."""
    return await search_post(body)


# ─── Modes ──────────────────────────────────────────

@router.post("/modes/set")
async def mode_set(body: dict):
    mode = body.get("mode", "ask")
    return {"ok": True, "mode": mode}


@router.get("/modes/{mode}/permissions")
async def mode_permissions(mode: str):
    perms = {
        "ask": {"read": True, "edit": False, "execute": False},
        "edit": {"read": True, "edit": True, "execute": False},
        "agent": {"read": True, "edit": True, "execute": True},
        "danger": {"read": True, "edit": True, "execute": True, "unrestricted": True},
    }
    return perms.get(mode, perms["ask"])


# ─── Startup resume ──────────────────────────────────

@router.get("/startup/resume")
async def resume_session():
    result = await memory.resume_last_session()
    # Map to Android ResumeResponse format
    return {
        "status": "ok",
        "message": f"Resumed session #{result.get('session_id', 0)}",
        "session_id": result.get("session_id"),
        "title": result.get("name", ""),
        "mode": "ASK",
        "cwd": ".",
        "repo_path": None,
        "summary": result.get("context", "")[:500],
        "working_state": result.get("repo_memory", {}),
        "project_memory": None,
    }


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


# ─── Generator (DALL-E, TTS, Whisper, Lyrics, Beats, Code, Stories) ─────────────

from app.services import generator
from app.services.vision import analyze_image
from app.services.phone_agent import PhoneAgent


@router.post("/gen/chat")
async def gen_chat(body: dict):
    """Direct AI chat — no session needed. Uncensored."""
    prompt = body.get("prompt", "")
    response = await llm_chat(prompt)
    return {"response": response}


@router.post("/gen/image")
async def gen_image(body: dict):
    """Generate an image from text (DALL-E)."""
    return await generator.generate_image(
        body.get("prompt", ""),
        body.get("size", "1024x1024"),
    )


@router.post("/gen/tts")
async def gen_tts(body: dict):
    """Convert text to speech."""
    audio = await generator.text_to_speech(
        body.get("text", ""),
        body.get("voice", "alloy"),
        body.get("format", "mp3"),
    )
    if audio:
        return StreamingResponse(iter([audio]), media_type="audio/mpeg")
    return JSONResponse({"error": "TTS failed — check API key"})


@router.post("/gen/whisper")
async def gen_whisper(
    file: UploadFile = File(...),
    language: str = Form("en"),
):
    """Transcribe audio to text (Whisper)."""
    data = await file.read()
    text = await generator.transcribe_audio(data, file.filename or "audio.mpeg")
    return {"text": text}


@router.post("/gen/lyrics")
async def gen_lyrics(body: dict):
    """Generate song lyrics with structure."""
    return await generator.generate_song_lyrics(
        body.get("genre", "hip-hop"),
        body.get("topic", ""),
        body.get("mood", "energetic"),
    )


@router.post("/gen/beat")
async def gen_beat(body: dict):
    """Generate beat-making instructions."""
    return await generator.generate_beat_instructions(
        body.get("genre", "hip-hop"),
        body.get("mood", "hard"),
        body.get("bpm", 120),
    )


@router.post("/gen/story")
async def gen_story(body: dict):
    """Generate a story."""
    return await generator.generate_story(
        body.get("prompt", ""),
        body.get("genre", "fantasy"),
    )


@router.post("/gen/code")
async def gen_code(body: dict):
    """Generate production code."""
    return await generator.generate_code(
        body.get("prompt", ""),
        body.get("language", "python"),
        body.get("context", ""),
    )


@router.post("/gen/script")
async def gen_script(body: dict):
    """Generate a video script / storyboard."""
    return await generator.generate_video_script(
        body.get("prompt", ""),
        body.get("duration", 60),
    )


@router.post("/gen/review")
async def gen_review(body: dict):
    """Review code for bugs, security, best practices."""
    return await generator.generate_code_review(
        body.get("code", ""),
        body.get("language", "python"),
    )


@router.post("/gen/readme")
async def gen_readme(body: dict):
    """Generate README.md for a project."""
    return await generator.generate_readme(
        body.get("name", ""),
        body.get("description", ""),
        body.get("tech_stack", []),
    )


@router.post("/gen/chat")
async def gen_chat(body: dict):
    """Chat endpoint — sends any prompt to the configured LLM."""
    from app.config import settings as cfg
    system = body.get("system", "You are Jarvis. Be direct and helpful.")
    text = await llm_chat(body.get("prompt", ""), system)
    return {"response": text, "model": cfg.model_base_url or cfg.model_provider}


@router.post("/vision/analyze")
async def vision_analyze(image: UploadFile = File(...), prompt: str = Form("Describe this image in detail. What do you see?")):
    """Send an image to the LLM for analysis."""
    data = await image.read()
    mime = image.content_type or "image/jpeg"
    text = await analyze_image(data, prompt, mime)
    return {"analysis": text}


# ─── Phone Agent (screenshots, install, download, optimize, game mode, input) ────────


@router.post("/phone/screenshot")
async def phone_screenshot():
    """Take screenshot on phone."""
    try:
        return PhoneAgent.screenshot()
    except Exception as e:
        return {"error": str(e)}


@router.get("/phone/screen-text")
async def phone_screen_text():
    """Get current app/window info."""
    return {"focus": PhoneAgent.screen_text()}


@router.get("/phone/apps")
async def phone_apps():
    """List installed apps."""
    return {"apps": PhoneAgent.installed_apps()}


@router.post("/phone/launch")
async def phone_launch(body: dict):
    """Launch an app by package name."""
    return PhoneAgent.launch_app(body.get("package", ""))


@router.post("/phone/stop")
async def phone_stop(body: dict):
    """Force stop an app."""
    return PhoneAgent.force_stop(body.get("package", ""))


@router.post("/phone/download")
async def phone_download(body: dict):
    """Download a file to phone."""
    return PhoneAgent.download(
        body.get("url", ""),
        body.get("dest", "/sdcard/Download/"),
    )


@router.get("/phone/downloads")
async def phone_downloads():
    """List downloaded files."""
    return {"files": PhoneAgent.list_downloads()}


@router.get("/phone/info")
async def phone_info():
    """Phone system info — battery, storage, memory."""
    return PhoneAgent.system_info()


@router.post("/phone/optimize")
async def phone_optimize():
    """Kill background apps, free RAM."""
    killed = PhoneAgent.kill_bg_apps()
    return {"success": True, "killed_count": len(killed), "apps": killed}


@router.post("/phone/game-mode")
async def phone_game_mode(body: dict):
    """Enable game/graphics optimizations for a game."""
    return PhoneAgent.game_mode(body.get("package", ""))


@router.post("/phone/tap")
async def phone_tap(body: dict):
    """Simulate tap at screen coordinates."""
    return PhoneAgent.tap(body.get("x", 0), body.get("y", 0))


@router.post("/phone/swipe")
async def phone_swipe(body: dict):
    """Simulate swipe gesture."""
    return PhoneAgent.swipe(
        body.get("x1", 0), body.get("y1", 0),
        body.get("x2", 0), body.get("y2", 0),
        body.get("duration", 300),
    )


@router.post("/phone/type")
async def phone_type(body: dict):
    """Simulate keyboard typing."""
    return PhoneAgent.type_text(body.get("text", ""))


@router.post("/phone/key")
async def phone_key(body: dict):
    """Press a key (3=back, 66=enter, 24=volume up, 25=volume down)."""
    return PhoneAgent.press_key(body.get("code", 0))


@router.post("/phone/shell")
async def phone_shell(body: dict):
    """Run raw shell command on phone."""
    return PhoneAgent.shell(body.get("cmd", ""))


# ─── Web Search (fetch any URL content) ──────────────────────────────


@router.post("/web/fetch")
async def web_fetch(body: dict):
    """Fetch content from any URL — articles, docs, APIs."""
    url = body.get("url", "")
    if not url:
        return {"error": "URL required"}
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        return {"url": url, "status": resp.status_code, "content": resp.text[:10000]}


@router.post("/web/search")
async def web_search(body: dict):
    """Search the web via free API, then summarize results."""
    query = body.get("query", "")
    if not query:
        return {"error": "Query required"}
    # Use free search via duckduckgo html
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        resp = await client.get(f"https://html.duckduckgo.com/html/?q={query}", headers={
            "User-Agent": "Mozilla/5.0"
        })
        if resp.status_code == 200:
            import re
            results = []
            for match in re.finditer(r'<a class="result__a" href="([^"]+)">([^<]+)</a>', resp.text):
                results.append({"url": match.group(1), "title": match.group(2)})
            return {"results": results[:10], "query": query}
    return {"error": "Search failed", "query": query}


@router.post("/web/summarize")
async def web_summarize(body: dict):
    """Fetch a URL and have the LLM summarize it."""
    url = body.get("url", "")
    if not url:
        return {"error": "URL required"}
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        text = resp.text[:8000]
    summary = await llm_chat(f"Summarize this webpage content:\n\n{text}", "You are a web research assistant. Provide clear, concise summaries.")
    return {"url": url, "summary": summary}


# ─── Smart Agent (auto-detects what to do and chains actions) ──────


@router.post("/agent/{session_id}/auto")
async def agent_auto(session_id: int, body: dict):
    """
    Smart agent mode — user sends a free-form request,
    the LLM decides what tools to use and orchestrates the execution.
    """
    user_input = body.get("prompt", "")
    await memory.store_message(session_id, "user", user_input)
    runtime.set(session_id, AgentState.PLANNING)

    # Get LLM with full tool description in system prompt
    tool_desc = (
        "You are Jarvis, a full super-assistant on EbkBoss's phone & PC. "
        "You can: write code, generate images/audio/lyrics/beats/stories, "
        "control Android (screenshots, downloads, installs, game mode, optimization, "
        "taps, swipes, typing, shell commands), "
        "search the web, fetch URLs, summarize pages. "
        "Be direct. No lectures. Just do it."
    )
    response = await llm_chat(user_input, tool_desc)
    runtime.add_tool_call(session_id, ToolCall(tool="agent", args={"prompt": user_input}))
    runtime.set(session_id, AgentState.DONE, response=response)
    await memory.store_message(session_id, "assistant", response,
        {"tool_calls": [tc.to_dict() for tc in runtime.get(session_id).tool_calls]})
    return {"ok": True, "response": response}


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
