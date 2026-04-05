"""Vision/image analysis — send images to LLMs that support vision."""

from __future__ import annotations
import base64
import httpx
import os

from app.config import settings


_IMAGE_PROVIDERS = {"groq", "openai", "openrouter", "anthropic", "gemini"}


async def analyze_image(
    image_data: bytes,
    prompt: str = "Describe this image in detail. What do you see?",
    mime_type: str = "image/jpeg",
) -> str:
    """Send an image + prompt to the configured LLM for analysis."""
    provider = settings.model_provider
    api_key = settings.api_key or os.environ.get(f"{provider.upper()}_API_KEY", "")

    if provider not in _IMAGE_PROVIDERS or not api_key:
        return "Vision requires a supported provider (groq, openai, openrouter, anthropic, gemini) with an API key."

    if provider == "gemini":
        return await _gemini_vision(image_data, prompt, mime_type)
    return await _openai_vision(image_data, prompt, mime_type, provider, api_key)


async def _openai_vision(
    image_data: bytes,
    prompt: str,
    mime_type: str,
    provider: str,
    api_key: str,
) -> str:
    b64 = base64.b64encode(image_data).decode()
    payload = {
        "model": "gpt-4o" if provider == "openai" else ("llama-3.2-90b-vision-preview" if provider == "groq" else "openai/gpt-4o"),
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{b64}"},
                    },
                ],
            }
        ],
        "max_tokens": 4096,
    }
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"https://api.{provider}.com/v1/chat/completions" if provider != "openrouter" else "https://openrouter.ai/api/v1/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        data = resp.json()
        return data.get("choices", [{}])[0].get("message", {}).get("content", "No response")


async def _gemini_vision(
    image_data: bytes,
    prompt: str,
    mime_type: str,
) -> str:
    api_key = settings.api_key or os.environ.get("GOOGLE_API_KEY", "")
    model = "gemini-2.5-pro-preview-03-25"
    b64 = base64.b64encode(image_data).decode()
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {
                    "inline_data": {
                        "mime_type": mime_type,
                        "data": b64,
                    }
                },
            ]
        }]
    }
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}",
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        data = resp.json()
        parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        return parts[0].get("text", "No response") if parts else "No response"
