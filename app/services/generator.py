"""Multimodal generators — image, audio, text, code, beats."""

from __future__ import annotations
import httpx
import base64
import os
import json
from app.config import settings


def _get_api_key() -> str:
    return settings.api_key or os.environ.get("OPENAI_API_KEY", "") or ""


def _get_gemini_key() -> str:
    return settings.api_key or os.environ.get("GOOGLE_API_KEY", "") or ""


# ─── Image Generation ──────────────────────────────────

async def generate_image(prompt: str, size: str = "1024x1024") -> dict:
    """Generate an image from a text prompt using DALL-E or compatible."""
    key = _get_api_key()
    if not key:
        return {"error": "Set JARVIS_API_KEY for image generation"}

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            "https://api.openai.com/v1/images/generations",
            json={"prompt": prompt, "model": "dall-e-3", "size": size, "quality": "hd", "n": 1},
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        if resp.status_code != 200:
            return {"error": f"{resp.status_code}: {resp.text[:200]}"}
        data = resp.json()
        return data["data"][0]  # {"url": "...", "revised_prompt": "..."}


async def edit_image(image_data: bytes, mask: bytes | None, prompt: str) -> dict:
    """Edit/modify an existing image with DALL-E."""
    key = _get_api_key()
    if not key:
        return {"error": "Set JARVIS_API_KEY"}
    # DALL-E edit requires multipart — simplified here
    return {"error": "Image edit requires multipart upload — use generate_image for now"}


# ─── Speech (TTS) ──────────────────────────────────────

async def text_to_speech(text: str, voice: str = "alloy", format: str = "mp3") -> bytes | None:
    """Convert text to spoken audio."""
    key = _get_api_key()
    if not key:
        return None

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            "https://api.openai.com/v1/audio/speech",
            json={"input": text, "model": "tts-1-hd", "voice": voice, "response_format": format},
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        if resp.status_code == 200:
            return resp.content
    return None


async def transcribe_audio(audio_data: bytes, filename: str = "audio.wav") -> str:
    """Transcribe audio to text (Whisper)."""
    key = _get_api_key()
    if not key:
        return "Set JARVIS_API_KEY for transcription"

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            "https://api.openai.com/v1/audio/transcriptions",
            data={"model": "whisper-1", "language": "en"},
            files={"file": (filename, audio_data, "audio/mpeg")},
            headers={"Authorization": f"Bearer {key}"},
        )
        if resp.status_code == 200:
            return resp.json().get("text", "")
    return "Transcription failed"


# ─── Song / Beat Generation (text → lyrics + structure) ──

ASYNC_LLM = None

async def generate_song_lyrics(genre: str, topic: str, mood: str = "energetic") -> dict:
    """Generate song lyrics with verse/chorus structure."""
    from app.services.llm_provider import llm_chat

    system = (
        "You are a professional songwriter and producer. Generate complete song lyrics "
        "with verse, chorus, bridge, hook structure. Include beat patterns and production notes."
    )
    prompt = (
        f"Genre: {genre}\nTopic: {topic}\nMood: {mood}\n\n"
        f"Generate a full song with:\n"
        f"- Song title\n- Verses (at least 2)\n- Chorus (catchy, repeatable)\n"
        f"- Bridge (at least 1)\n- Hook/Outro\n- Beat/production notes (BPM, key, instruments)"
    )
    text = await llm_chat(prompt, system)
    return {"lyrics": text, "genre": genre, "topic": topic}


async def generate_beat_instructions(genre: str, mood: str, bpm: int = 120) -> dict:
    """Generate beat-making instructions for FL Studio / Ableton."""
    from app.services.llm_provider import llm_chat

    system = "You are a professional music producer. Give detailed beat-making instructions."
    prompt = (
        f"Create a {mood} {genre} beat at {bpm} BPM.\n\n"
        f"Include:\n"
        f"- Drum pattern (kick, snare, hi-hats)\n- Bass pattern\n"
        f"- Melody/synthesis layers\n- FX and transitions\n"
        f"- Arrangement structure\n- VST/plugin suggestions"
    )
    text = await llm_chat(prompt, system)
    return {"instructions": text, "genre": genre, "bpm": bpm}


async def generate_story(prompt: str, genre: str = "fantasy") -> dict:
    """Generate a full story with chapters."""
    from app.services.llm_provider import llm_chat

    text = await llm_chat(
        f"Write a {genre} story based on: {prompt}\n\nInclude characters, setting, plot twists, and an ending.",
        f"You are a creative {genre} author. Write engaging stories with vivid descriptions.",
    )
    return {"story": text, "genre": genre}


async def generate_code(prompt: str, language: str = "python", context: str = "") -> dict:
    """Generate production-ready code."""
    from app.services.llm_provider import llm_chat

    system = (
        f"You are an expert {language} developer. Write clean, well-commented, production-ready code. "
        f"Include error handling, type hints, and docstrings."
    )
    text = await llm_chat(f"Language: {language}\n{context}\n\nTask: {prompt}", system)
    return {"code": text, "language": language}


async def generate_video_script(topic: str, duration_sec: int = 60) -> dict:
    """Generate a video script / storyboard."""
    from app.services.llm_provider import llm_chat

    system = "You are a YouTube video producer. Write engaging video scripts with timestamps."
    prompt = (
        f"Create a {duration_sec}-second video script about: {topic}\n\n"
        f"Include: intro hook, main segments with timestamps, visual cues, outro CTA"
    )
    text = await llm_chat(prompt, system)
    return {"script": text, "duration": duration_sec}


async def generate_code_review(code: str, language: str = "python") -> dict:
    """Review code and suggest improvements."""
    from app.services.llm_provider import llm_chat

    text = await llm_chat(
        f"Language: {language}\n\nCode to review:\n```\n{code}\n```\n\nReview for: bugs, security, performance, readability, best practices.",
        "You are a senior software engineer doing code review. Be thorough but constructive.",
    )
    return {"review": text, "language": language}


async def generate_readme(project_name: str, description: str, tech_stack: list[str]) -> dict:
    """Generate a README.md for a project."""
    from app.services.llm_provider import llm_chat

    text = await llm_chat(
        f"Project: {project_name}\nDescription: {description}\nTech: {', '.join(tech_stack)}\n\nGenerate a complete README.md with installation, usage, and contributing sections.",
        "You are a professional technical writer. Generate clear, well-formatted README files.",
    )
    return {"readme": text}
