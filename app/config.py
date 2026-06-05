from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    SLACK_CLIENT_ID: str = ""
    SLACK_CLIENT_SECRET: str = ""
    SLACK_SIGNING_SECRET: str = ""
    SLACK_APP_TOKEN: str = ""

    UTEM_BACKEND_URL: str = "http://utem-platform-backend:8000"
    UTEM_INTERNAL_TOKEN: str = ""

    SLACK_OAUTH_REDIRECT_URI: str = (
        "https://utem.innavoto.com/api/proxy/slack-app/oauth/callback"
    )

    APP_PORT: int = 8098
    LOG_LEVEL: str = "INFO"
    FERNET_KEY: str = ""

    model_config = {"env_prefix": "", "case_sensitive": True}


settings = Settings()
