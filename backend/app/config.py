"""Application Configuration module reading from source of truth .env."""

import json
from pathlib import Path
from typing import Any, Optional, Union
from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = BASE_DIR.parent
ROOT_ENV = ROOT_DIR / ".env"
LOCAL_ENV = BASE_DIR / ".env"


class Settings(BaseSettings):
    """Central application settings loaded from project environment and .env files."""

    model_config = SettingsConfigDict(
        env_file=(str(LOCAL_ENV), str(ROOT_ENV), ".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Supabase connectivity
    SUPABASE_URL: str = "https://placeholder.supabase.co"
    SUPABASE_ANON_KEY: str = Field(
        default="",
        validation_alias=AliasChoices("SUPABASE_ANON_KEY", "SUPABASE_PUBLISHABLE_KEY"),
    )
    SUPABASE_SERVICE_ROLE_KEY: str = Field(
        default="",
        validation_alias=AliasChoices("SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_SECRET_KEY"),
    )

    # Key aliases
    SUPABASE_PUBLISHABLE_KEY: Optional[str] = None
    SUPABASE_SECRET_KEY: Optional[str] = None

    # External services
    DATABASE_URL: Optional[str] = None
    CLOUDINARY_CLOUD_NAME: Optional[str] = None
    CLOUDINARY_API_KEY: Optional[str] = None
    CLOUDINARY_API_SECRET: Optional[str] = None
    CLOUDINARY_URL: Optional[str] = None

    # AI and orchestration keys
    OPENAI_API_KEY: str = ""
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:5173", "*"]
    N8N_WEBHOOK_SECRET: str = "hackathon-secret"
    PRICE_LOCK_SECRET: str = "price-lock-secret-key-12345"
    PORT: int = 8000

    def model_post_init(self, __context: Any) -> None:
        """Harmonize modern and legacy Supabase keys."""
        if not self.SUPABASE_ANON_KEY and self.SUPABASE_PUBLISHABLE_KEY:
            self.SUPABASE_ANON_KEY = self.SUPABASE_PUBLISHABLE_KEY
        elif not self.SUPABASE_PUBLISHABLE_KEY and self.SUPABASE_ANON_KEY:
            self.SUPABASE_PUBLISHABLE_KEY = self.SUPABASE_ANON_KEY

        if not self.SUPABASE_SERVICE_ROLE_KEY and self.SUPABASE_SECRET_KEY:
            self.SUPABASE_SERVICE_ROLE_KEY = self.SUPABASE_SECRET_KEY
        elif not self.SUPABASE_SECRET_KEY and self.SUPABASE_SERVICE_ROLE_KEY:
            self.SUPABASE_SECRET_KEY = self.SUPABASE_SERVICE_ROLE_KEY

    @property
    def supabase_url(self) -> str:
        """Property accessor for lowercase supabase_url."""
        return self.SUPABASE_URL

    @property
    def supabase_anon_key(self) -> str:
        """Get publishable / anon key with graceful fallback."""
        return (
            self.SUPABASE_ANON_KEY
            or self.SUPABASE_PUBLISHABLE_KEY
            or "placeholder-anon-key"
        )

    @property
    def supabase_service_role_key(self) -> str:
        """Get secret / service role key with graceful fallback."""
        return (
            self.SUPABASE_SERVICE_ROLE_KEY
            or self.SUPABASE_SECRET_KEY
            or "placeholder-service-key"
        )

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: Union[str, list[str]]) -> list[str]:
        """Parse CORS origins from string representation or keep list format."""
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                try:
                    parsed = json.loads(stripped)
                    if isinstance(parsed, list):
                        return parsed
                except json.JSONDecodeError:
                    return [item.strip() for item in stripped.split(",") if item.strip()]
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return value


settings = Settings()
