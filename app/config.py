"""Environment-driven settings. Secrets never live in code."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    google_api_key: str = ""
    # NOTE: gemini-2.5-flash has a published shutdown date of 2026-10-16; keep this env-driven.
    llm_model: str = "gemini-3.5-flash"
    search_model: str = ""  # model for the search sub-agent; empty = same as llm_model (set a "lite" model to cut cost)
    request_timeout_s: float = 60.0
    max_retries: int = 3
    max_message_chars: int = 2000
    rate_limit_per_min: int = 20
    max_sessions: int = 1000
    log_level: str = "INFO"
    log_format: str = "text"  # "text" | "json" (use json behind a log aggregator)

    # --- sessions / memory ---
    session_db_url: str = ""  # e.g. sqlite+aiosqlite:///./sessions.db or postgresql+asyncpg://...; empty = in-memory
    max_history_turns: int = 12  # user turns sent to the model per request; 0 = unlimited

    # --- reliability / cost ---
    max_concurrent_requests: int = 8  # simultaneous in-flight LLM runs per instance
    queue_timeout_s: float = 5.0  # how long a request may wait for a free slot before a 503
    cache_ttl_s: int = 600  # answer cache for first-turn questions; 0 disables
    cache_max_entries: int = 500
    ready_check_ttl_s: int = 60

    @property
    def effective_search_model(self) -> str:
        return self.search_model or self.llm_model


@lru_cache
def get_settings() -> Settings:
    return Settings()
