"""3-layer memory system: working, session, repo."""

from __future__ import annotations
import json
from app.db.session import get_db


class WorkingMemory:
    """Current task's immediate context — lives in memory only."""

    def update(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

    def clear(self):
        for attr in dir(self):
            if not attr.startswith("_"):
                delattr(self, attr)

    def to_dict(self):
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}


class SessionMemory:
    """Persisted session summaries — stored in messages table."""

    async def save_summary(self, session_id: int, summary: str):
        db = get_db()
        await db.execute(
            "INSERT INTO messages (session_id, role, content) VALUES (?, 'assistant', ?)",
            (session_id, summary),
        )
        await db.commit()

    async def load_history(self, session_id: int, limit: int = 50) -> list[dict]:
        db = get_db()
        cursor = await db.execute(
            "SELECT role, content, meta FROM messages WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
            (session_id, limit),
        )
        rows = await cursor.fetchall()
        return [{"role": r[0], "content": r[1], "meta": json.loads(r[2] or "{}")} for r in rows]


class RepoMemory:
    """Long-term project knowledge — architecture, conventions, indexed code."""

    async def save(self, session_id: int, architecture: str = "", conventions: str = "", notes: dict | None = None):
        db = get_db()
        await db.execute(
            """INSERT INTO repo_memory (session_id, architecture, conventions, notes)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(session_id) DO UPDATE SET
               architecture = excluded.architecture,
               conventions = excluded.conventions,
               notes = excluded.notes""",
            (session_id, architecture, conventions, json.dumps(notes or {})),
        )
        await db.commit()

    async def load(self, session_id: int) -> dict:
        db = get_db()
        cursor = await db.execute("SELECT architecture, conventions, notes FROM repo_memory WHERE session_id = ?", (session_id,))
        row = await cursor.fetchone()
        if row:
            return {"architecture": row[0], "conventions": row[1], "notes": json.loads(row[2] or "{}")}
        return {}


class MemoryManager:
    def __init__(self):
        self.working = WorkingMemory()
        self.session = SessionMemory()
        self.repo = RepoMemory()

    async def build_context(self, session_id: int, limit: int = 30) -> str:
        """Build full context from all memory layers for the agent."""
        messages = await self.session.load_history(session_id, limit=limit)
        context_parts = []
        # Working memory
        if self.working.to_dict():
            context_parts.append(f"### Working Memory\n{json.dumps(self.working.to_dict(), indent=2)}")
        # Repo memory
        repo = await self.repo.load(session_id)
        if repo.get("architecture"):
            context_parts.append(f"### Architecture\n{repo['architecture']}")
        if repo.get("conventions"):
            context_parts.append(f"### Conventions\n{repo['conventions']}")
        # Conversation history (reversed for chronological order)
        history_lines = []
        for msg in reversed(messages):
            history_lines.append(f"**{msg['role']}**: {msg['content'][:500]}")
        if history_lines:
            context_parts.append("### Recent Conversation\n" + "\n\n".join(history_lines[-6:]))
        return "\n\n".join(context_parts)

    async def resume_last_session(self, session_id: int | None = None):
        """Resume the most recent active session."""
        db = get_db()
        cursor = await db.execute(
            "SELECT id, name FROM sessions WHERE is_archived = 0 ORDER BY updated_at DESC LIMIT 1"
        )
        row = await cursor.fetchone()
        if row:
            sid, name = row
            context = await self.build_context(session_id=sid)
            repo = await self.repo.load(sid)
            return {"session_id": sid, "name": name, "context": context, "repo_memory": repo}
        # Create a new session
        cursor = await db.execute("INSERT INTO sessions (name) VALUES ('New Session')")
        await db.commit()
        new_id = cursor.lastrowid
        await db.execute("INSERT INTO agent_state (session_id) VALUES (?)", (new_id,))
        await db.commit()
        return {"session_id": new_id, "name": "New Session", "context": "", "repo_memory": {}}

    async def save_project_memory(self, session_id: int, data: dict):
        """Save architecture notes and conventions."""
        await self.repo.save(
            session_id,
            architecture=data.get("architecture", ""),
            conventions=data.get("conventions", ""),
            notes=data.get("notes"),
        )

    async def save_session(self, session_id: int, name: str | None = None):
        """Update session metadata."""
        db = get_db()
        if name:
            await db.execute("UPDATE sessions SET name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (name, session_id))
        else:
            await db.execute("UPDATE sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (session_id,))
        await db.commit()

    async def list_sessions(self):
        db = get_db()
        cursor = await db.execute("SELECT id, name, created_at, updated_at FROM sessions ORDER BY updated_at DESC")
        rows = await cursor.fetchall()
        return [{"id": r[0], "name": r[1], "created_at": r[2], "updated_at": r[3]} for r in rows]

    async def create_session(self, name: str = "New Session"):
        db = get_db()
        cursor = await db.execute("INSERT INTO sessions (name) VALUES (?)", (name,))
        await db.commit()
        sid = cursor.lastrowid
        await db.execute("INSERT INTO agent_state (session_id) VALUES (?)", (sid,))
        await db.commit()
        return sid

    async def store_message(self, session_id: int, role: str, content: str, meta: dict | None = None):
        db = get_db()
        await db.execute(
            "INSERT INTO messages (session_id, role, content, meta) VALUES (?, ?, ?, ?)",
            (session_id, role, content, json.dumps(meta or {})),
        )
        await db.execute("UPDATE sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (session_id,))
        await db.commit()


# Singleton
memory = MemoryManager()
