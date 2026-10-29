from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Read from environment variables or .env (see .env.example)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    service_key: str = ""
    # Origins allowed to call the service from the browser, comma-separated (e.g. http://localhost:44300).
    # Only needed when the portal page calls the service on another address instead of through its own /ai/ path.
    cors_origins: str = ""

    # Which model provider reviews the documents
    ai_provider: Literal["claude", "openai", "gemini", "local"] = "claude"
    ai_timeout_seconds: float = 600

    anthropic_api_key: str = ""
    claude_model: str = "claude-opus-5-5"
    claude_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    claude_max_tokens: int = 64000
    claude_refusal_fallback: bool = True

    openai_api_key: str = ""
    openai_model: str = "gpt-5"
    openai_base_url: str = ""
    openai_reasoning_effort: str = ""

    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-pro"

    local_base_url: str = "http://localhost:11434"
    local_model: str = "gemma3:4b"
    local_send_images: bool = False
    local_context_tokens: int = 32768

    soffice_path: str = "soffice"
    conversion_timeout_seconds: int = 180
    max_upload_mb: int = 50  # the portal page's CAPAAiMaxSizeMB

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
