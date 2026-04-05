"""
LLM abstraction layer — call any provider's model.
Configure provider via JARVIS_MODEL_PROVIDER env var:
  "openrouter" | "groq" | "openai" | "anthropic" | "gemini" | "mock"
"""
from __future__ import annotations
import os
import json
from typing import AsyncIterable
import httpx

from app.config import settings


PROVIDER_URLS = {
    "openai": "https://api.openai.com/v1/chat/completions",
    "groq": "https://api.groq.com/openai/v1/chat/completions",
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
    "anthropic": "https://api.anthropic.com/v1/messages",
}

DEFAULT_MODELS = {
    "openai": "gpt-4o",
    "groq": "llama-3.3-70b-versatile",
    "openrouter": "qwen/qwen-2.5-72b-instruct",
    "anthropic": "claude-3-7-sonnet-20250219",
    "gemini": "gemini-2.5-pro-preview-03-25",
}


def _get_api_key() -> str:
    if settings.api_key:
        return settings.api_key
    provider = settings.model_provider or "mock"
    env_map = {
        "openai": "OPENAI_API_KEY",
        "groq": "GROQ_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "gemini": "GOOGLE_API_KEY",
    }
    return os.environ.get(env_map.get(provider, ""), "")


def _model() -> str:
    if settings.model_base_url:
        return settings.model_base_url
    provider = settings.model_provider or "mock"
    return DEFAULT_MODELS.get(provider, "gpt-4o")


# ─── Chat / text generation ──────────────────────────────

async def llm_chat(prompt: str, system: str | None = None, stream: bool = True) -> str:
    """Send a prompt to the configured LLM provider."""
    if system is None:
        system = (
            "You are Jarvis, an AI coding assistant and creative collaborator. "
            "You write code, fix bugs, answer questions, generate content, "
            "and help with terminal commands. Be direct and helpful."
        )

    provider = settings.model_provider or "mock"
    if provider == "mock" or not _get_api_key():
        return _mock_response(prompt)

    if provider == "gemini":
        return await _gemini_chat(prompt, system)

    if provider == "anthropic":
        return await _anthropic_chat(prompt, system)

    # OpenAI-compatible (groq, openrouter, openai)
    return await _openai_compat_chat(prompt, system)


async def _openai_compat_chat(prompt: str, system: str) -> str:
    api_key = _get_api_key()
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            PROVIDER_URLS.get(settings.model_provider, PROVIDER_URLS["openai"]),
            json={
                "model": _model(),
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 4096,
            },
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        if resp.status_code != 200:
            return f"Error: {resp.status_code} — {resp.text[:300]}"
        data = resp.json()
        return data.get("choices", [{}])[0].get("message", {}).get("content", "No response")


async def _anthropic_chat(prompt: str, system: str) -> str:
    api_key = _get_api_key()
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            json={
                "model": _model(),
                "max_tokens": 4096,
                "system": system,
                "messages": [{"role": "user", "content": prompt}],
            },
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )
        if resp.status_code != 200:
            return f"Error: {resp.status_code} — {resp.text[:300]}"
        data = resp.json()
        return data.get("content", [{}])[0].get("text", "")


async def _gemini_chat(prompt: str, system: str) -> str:
    api_key = _get_api_key()
    model = _model().split(":")[-1] if ":" in _model() else _model()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            url,
            json={
                "system_instruction": {"parts": [{"text": system}]},
                "contents": [{"parts": [{"text": prompt}]}],
            },
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code != 200:
            return f"Error: {resp.status_code} — {resp.text[:300]}"
        data = resp.json()
        parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        return parts[0].get("text", "") if parts else "No response"


def _mock_response(prompt: str) -> str:
    words = prompt.lower().split()
    if any(w in words for w in ["fix", "bug", "error", "broken"]):
        return (
            "I'll investigate the bug. Let me:\n"
            "1. Read the relevant source files\n"
            "2. Identify the root cause\n"
            "3. Propose a specific fix\n\n"
            f"To enable real AI, set JARVIS_MODEL_PROVIDER and JARVIS_API_KEY."
        )
    if any(w in words for w in ["create", "add", "new", "build", "make"]):
        return (
            "I'll build that for you. Plan:\n"
            "1. Analyze existing code patterns\n"
            "2. Create the new files\n"
            "3. Run tests\n\n"
            f"To enable real AI, set JARVIS_MODEL_PROVIDER and JARVIS_API_KEY."
        )
    return (
        f"I understand: \"{prompt[:200]}\"\n\n"
        "This is a mock response. Configure JARVIS_MODEL_PROVIDER (openrouter, groq, openai, anthropic, "
        "gemini) and set JARVIS_API_KEY to get real AI."
    )
