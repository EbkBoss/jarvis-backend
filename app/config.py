"""Jarvis backend configuration."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False
    model_provider: str = "mock"
    api_key: str = ""
    model_base_url: str = ""
    planner_model: str = ""
    coder_model: str = ""
    summarizer_model: str = ""
    workspace_root: str = "/"
    max_agent_steps: int = 50
    command_timeout: int = 30
    default_mode: str = "ask"
    model_config = {"env_prefix": "JARVIS_", "env_file": ".env"}


settings = Settings()
