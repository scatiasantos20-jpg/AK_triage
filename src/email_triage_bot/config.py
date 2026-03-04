from __future__ import annotations

from pydantic import Field
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Logging ---
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # --- Multi-account profiles ---
    profiles_path: str = Field(default="profiles.json", alias="PROFILES_PATH")
    default_profile: str = Field(default="default", alias="DEFAULT_PROFILE")

    # --- Gmail ---
    gmail_credentials_path: str = Field(default="credentials.json", alias="GMAIL_CREDENTIALS_PATH")
    gmail_token_path: str = Field(default="token.json", alias="GMAIL_TOKEN_PATH")
    gmail_query: str = Field(default="is:unread newer_than:1d", alias="GMAIL_QUERY")
    batch_limit: int = Field(default=25, alias="BATCH_LIMIT")

    # --- Gemini (FREE tier) ---
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-2.5-flash", alias="GEMINI_MODEL")
    gemini_api_version: str = Field(default="v1beta", alias="GEMINI_API_VERSION")
    gemini_timeout_s: int = Field(default=60, alias="GEMINI_TIMEOUT_S")

    # --- Draft options ---
    draft_signature: str = Field(default="", alias="DRAFT_SIGNATURE")

    # --- Safety / filtering ---
    # If true, drafts are created only when the latest email contains one of the keywords below.
    require_name_mention: bool = Field(default=True, alias="REQUIRE_NAME_MENTION")
    name_keywords: str = Field(default="Andreia,Andrea", alias="NAME_KEYWORDS")
    # Always skip drafting for emails that contain one of these keywords
    # in sender, subject, or body.
    ignore_keywords: str = Field(default="social media,bank,newsletter,newsletters,noreply,no-reply", alias="IGNORE_KEYWORDS")

    @field_validator("gemini_timeout_s")
    @classmethod
    def _min_timeout(cls, v: int) -> int:
        # Gemini enforces minimum deadline/timeout of 10s; clamp to be safe.
        try:
            v = int(v)
        except Exception:
            v = 60
        return max(10, v)
