"""Secret management — loads credentials from .env only."""

from __future__ import annotations

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Secrets(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    openai_api_key: SecretStr = SecretStr("")
    openai_base_url: str = "https://api.openai.com/v1"
    deepseek_api_key: SecretStr = SecretStr("")
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    llm_discussion_api_key: SecretStr = SecretStr("")
    llm_discussion_base_url: str = ""
    llm_code_api_key: SecretStr = SecretStr("")
    llm_code_base_url: str = ""
    email_smtp_host: str = ""
    email_smtp_port: int = 587
    email_smtp_user: str = ""
    email_smtp_password: SecretStr = SecretStr("")
    email_imap_host: str = ""
    email_imap_user: str = ""
    email_imap_password: SecretStr = SecretStr("")
    moltbook_api_key: SecretStr = SecretStr("")
    moltbook_base_url: str = ""
