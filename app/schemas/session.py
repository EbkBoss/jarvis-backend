"""Pydantic schemas for API requests/responses."""

from __future__ import annotations
from pydantic import BaseModel


class MessageRequest(BaseModel):
    prompt: str


class SessionCreate(BaseModel):
    name: str = "New Session"


class SessionResponse(BaseModel):
    id: int
    name: str
    created_at: str
    updated_at: str


class AgentStatus(BaseModel):
    state: str
    tool_calls: list[dict]
    plan: str


class ResumeResponse(BaseModel):
    session_id: int
    name: str
    context: str
    repo_memory: dict
