"""Async SQLite database connection (WAL mode)."""

from __future__ import annotations

import os
import aiosqlite
from pathlib import Path

# Use /tmp on Railway (read-only filesystem), local otherwise
if os.path.exists("/tmp/jarvis.db"):
    DB_PATH = Path("/tmp") / "jarvis.db"
else:
    DB_PATH = Path(__file__).parent.parent.parent / "data" / "jarvis.db"

_conn: aiosqlite.Connection | None = None


async def init_db():
    global _conn
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _conn = await aiosqlite.connect(str(DB_PATH))
    await _conn.execute("PRAGMA journal_mode=WAL;")
    await _conn.execute("PRAGMA foreign_keys=ON;")
    await _conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT DEFAULT 'untitled',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_archived INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            meta TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS agent_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL UNIQUE REFERENCES sessions(id) ON DELETE CASCADE,
            state TEXT DEFAULT 'IDLE',
            mode TEXT DEFAULT 'ask',
            context TEXT DEFAULT '{}',
            plan TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS repo_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL UNIQUE REFERENCES sessions(id) ON DELETE CASCADE,
            architecture TEXT DEFAULT '',
            conventions TEXT DEFAULT '',
            notes TEXT DEFAULT '{}'
        );
    """)
    await _conn.commit()


async def close_db():
    global _conn
    if _conn:
        await _conn.close()
        _conn = None


def get_db() -> aiosqlite.Connection:
    if _conn is None:
        raise RuntimeError("Database not initialized")
    return _conn
