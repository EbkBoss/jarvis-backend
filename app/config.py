"""Jarvis backend configuration."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False

    # ── LLM ──
    model_provider: str = "mock"          # groq | openrouter | openai | anthropic | gemini | mock
    api_key: str = ""                     # JARVIS_API_KEY — your LLM API key
    model_base_url: str = ""              # Override model name / custom endpoint

    # ── Agent ──
    workspace_root: str = "/"
    max_agent_steps: int = 50
    command_timeout: int = 30
    default_mode: str = "ask"

    model_config = {"env_prefix": "JARVIS_", "env_file": ".env"}


settings = Settings()
