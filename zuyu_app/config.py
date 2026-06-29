from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator


class Settings(BaseModel):
    app_env: Literal["development", "staging", "production"] = "production"
    app_version: str = "2026.04.04"
    db_path: str = "/data/zm.db"
    static_dir: Path = Field(default_factory=lambda: Path(__file__).resolve().parents[1] / "static")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    client_log_enabled: bool = True
    openai_api_key: str | None = None
    openai_model: str = "gpt-5.4-mini"
    openai_timeout_ms: int = 12000
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-4-6"
    wiki_dir: Path = Field(default_factory=lambda: Path("/wiki"))

    @field_validator("db_path")
    @classmethod
    def validate_db_path(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("db_path must not be empty")
        return value

    @field_validator("static_dir")
    @classmethod
    def validate_static_dir(cls, value: Path) -> Path:
        if not value.exists():
            raise ValueError(f"static_dir does not exist: {value}")
        return value

    @field_validator("openai_api_key")
    @classmethod
    def validate_openai_api_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("openai_model")
    @classmethod
    def validate_openai_model(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("openai_model must not be empty")
        return value

    @field_validator("openai_timeout_ms")
    @classmethod
    def validate_openai_timeout_ms(cls, value: int) -> int:
        if value < 1000:
            raise ValueError("openai_timeout_ms must be at least 1000")
        return value

    @field_validator("anthropic_api_key")
    @classmethod
    def validate_anthropic_api_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


def _settings_from_env() -> Settings:
    raw = {
        "app_env": os.getenv("ZUYU_APP_ENV", "production"),
        "app_version": os.getenv("ZUYU_APP_VERSION", "2026.04.04"),
        "db_path": os.getenv("ZUYU_DB_PATH", "/data/zm.db"),
        "static_dir": os.getenv("ZUYU_STATIC_DIR") or str(Path(__file__).resolve().parents[1] / "static"),
        "log_level": os.getenv("ZUYU_LOG_LEVEL", "INFO").upper(),
        "client_log_enabled": os.getenv("ZUYU_CLIENT_LOG_ENABLED", "1") not in {"0", "false", "False"},
        "openai_api_key": os.getenv("OPENAI_API_KEY") or os.getenv("ZUYU_OPENAI_API_KEY"),
        "openai_model": os.getenv("ZUYU_OPENAI_MODEL", "gpt-5.4-mini"),
        "openai_timeout_ms": int(os.getenv("ZUYU_OPENAI_TIMEOUT_MS", "12000")),
        "anthropic_api_key": os.getenv("ANTHROPIC_API_KEY") or os.getenv("ZUYU_ANTHROPIC_API_KEY"),
        "anthropic_model": os.getenv("ZUYU_ANTHROPIC_MODEL", "claude-sonnet-4-6"),
        "wiki_dir": os.getenv("ZUYU_WIKI_DIR", "/wiki"),
    }
    try:
        return Settings.model_validate(raw)
    except ValidationError as exc:
        raise RuntimeError(f"Invalid ZUYU configuration: {exc}") from exc


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return _settings_from_env()
