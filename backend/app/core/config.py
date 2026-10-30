"""Application configuration loaded from environment variables.

Every service URL, credential and tunable lives here so that nothing is
hardcoded across the codebase. See `.env.example` at the repository root.
"""

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    # --- General -----------------------------------------------------------
    app_name: str = "SmartShelf AI"
    environment: str = "development"  # development | production | test
    log_level: str = "INFO"
    frontend_base_url: str = "http://localhost:5173"
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["http://localhost:5173"])

    # --- Database ----------------------------------------------------------
    database_url: str = "postgresql+psycopg://smartshelf:smartshelf@localhost:5432/smartshelf"

    # --- Auth --------------------------------------------------------------
    session_ttl_hours: int = 24 * 7
    session_cookie_name: str = "smartshelf_session"
    session_cookie_secure: bool = False
    # Rate limit for login/registration: attempts per window per client.
    auth_rate_limit_attempts: int = 10
    auth_rate_limit_window_seconds: int = 300
    upload_rate_limit_per_minute: int = 20

    # --- File uploads ------------------------------------------------------
    upload_dir: Path = Path("./data/uploads")
    max_upload_mb: int = 15
    max_pdf_pages: int = 20

    # --- OCR ---------------------------------------------------------------
    # auto = PaddleOCR when installed, otherwise Tesseract.
    ocr_engine: str = "auto"
    tesseract_cmd: str | None = None
    tesseract_lang: str = "eng"

    # --- Local LLM (Ollama) ------------------------------------------------
    llm_enabled: bool = True
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:1.5b"
    # Optional vision-language model for packaging images; empty = disabled.
    ollama_vision_model: str = ""
    llm_timeout_seconds: float = 120.0
    llm_max_attempts: int = 2

    # --- Background jobs ---------------------------------------------------
    run_scheduler: bool = True
    expiry_check_interval_minutes: int = 15
    notification_dispatch_interval_seconds: int = 60
    processing_sweep_interval_seconds: int = 60
    processing_max_attempts: int = 3
    processing_stale_minutes: int = 15

    # --- Email -------------------------------------------------------------
    smtp_host: str = ""
    smtp_port: int = 1025
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = False  # STARTTLS
    smtp_use_ssl: bool = False  # implicit TLS (port 465)
    smtp_timeout_seconds: float = 20.0
    email_from: str = "SmartShelf AI <alerts@smartshelf.local>"
    email_max_attempts: int = 5

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            if value.startswith("["):
                import json

                return json.loads(value)
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def smtp_configured(self) -> bool:
        return bool(self.smtp_host)


@lru_cache
def get_settings() -> Settings:
    return Settings()
