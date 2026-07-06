from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    SLACK_CLIENT_ID: str = ""
    SLACK_CLIENT_SECRET: str = ""
    SLACK_SIGNING_SECRET: str = ""
    SLACK_APP_TOKEN: str = ""

    UTEM_BACKEND_URL: str = "http://utem-platform-backend:8000"
    UTEM_INTERNAL_TOKEN: str = ""

    # Shared cluster Redis — durable store for OAuth CSRF state and the
    # welcome-dedupe set (G80). Must be set in every environment that runs
    # more than one Uvicorn worker or replica; an empty value degrades to a
    # per-process in-memory fallback that is NOT durable.
    REDIS_URL: str = (
        "redis://utem-redis-master.utem-system.svc.cluster.local:6379/0"
    )

    SLACK_OAUTH_REDIRECT_URI: str = (
        "https://utem.innavoto.com/api/proxy/slack-app/oauth/callback"
    )

    APP_PORT: int = 8098
    LOG_LEVEL: str = "INFO"
    FERNET_KEY: str = ""

    model_config = {"env_prefix": "", "case_sensitive": True}


settings = Settings()
