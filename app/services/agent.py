"""Agent runtime — state machine with 4 modes"""

from __future__ import annotations
import enum
import json
from dataclasses import dataclass, field


class AgentState(str, enum.Enum):
    IDLE = "IDLE"
    PLANNING = "PLANNING"
    EXECUTING = "EXECUTING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    DONE = "DONE"
    ERROR = "ERROR"


class AgentMode(str, enum.Enum):
    ASK = "ask"
    EDIT = "edit"
    AGENT = "agent"
    DANGER = "danger"


@dataclass
class ToolCall:
    tool: str
    args: dict
    result: str = ""

    def to_dict(self):
        return {"tool": self.tool, "args": self.args, "result": self.result}


@dataclass
class AgentStep:
    state: AgentState
    tool_calls: list[ToolCall] = field(default_factory=list)
    response: str = ""
    plan: str = ""


class AgentRuntime:
    """In-memory agent state per session."""

    def __init__(self):
        self._states: dict[int, AgentStep] = {}

    def get(self, session_id: int) -> AgentStep:
        if session_id not in self._states:
            self._states[session_id] = AgentStep(state=AgentState.IDLE)
        return self._states[session_id]

    def set(self, session_id: int, state: AgentState, **kwargs):
        step = self.get(session_id)
        step.state = state
        for k, v in kwargs.items():
            setattr(step, k, v)

    def add_tool_call(self, session_id: int, tc: ToolCall):
        self.get(session_id).tool_calls.append(tc)

    def clear_tool_calls(self, session_id: int):
        self.get(session_id).tool_calls = []

    def status(self, session_id: int) -> dict:
        step = self.get(session_id)
        return {
            "state": step.state.value,
            "tool_calls": [t.to_dict() for t in step.tool_calls],
            "plan": step.plan,
        }


# Singleton
runtime = AgentRuntime()
