from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Azure credentials
    azure_client_id: str = Field(description="Azure app client ID")
    azure_client_secret: str = Field(description="Azure app client secret")

    # Token storage
    token_cache_path: Path = Field(default=Path(".token_cache.json"))
    token_encryption_key: str = Field(description="Fernet encryption key for token cache")

    # MCP server auth
    mcp_api_key: str = Field(description="Bearer token for MCP server auth")

    # Scope toggles
    scope_mail_read: bool = Field(default=True)
    scope_mail_write: bool = Field(default=True)
    scope_mail_send: bool = Field(default=True)
    scope_calendar_read: bool = Field(default=True)
    scope_calendar_write: bool = Field(default=True)
    scope_contacts_read: bool = Field(default=True)
    scope_contacts_write: bool = Field(default=False)
    scope_tasks_read: bool = Field(default=True)
    scope_tasks_write: bool = Field(default=False)

    # Server config
    host: str = Field(default="0.0.0.0")  # noqa: S104  # nosec B104 - intentional bind-all for server
    port: int = Field(default=8000)
    log_level: str = Field(default="INFO")

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in valid:
            raise ValueError(f"log_level must be one of {valid}")
        return upper
