"""Env-driven settings for the LangGraph orchestration service."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgis_db: str
    postgis_user: str
    postgis_password: str
    postgis_host: str = "postgis"
    postgis_port: int = 5432

    mcp_server_url: str = "http://mcp_server:8001/sse"

    anthropic_api_key: str = ""
    llm_model: str = "claude-sonnet-5"

    # KSBA reference point — used by observation_builder's phase-of-flight classifier to
    # compute each aircraft's distance from the airport. Matches data_pipeline/poller's
    # bbox center (kept as a separate setting here rather than shared config, consistent
    # with how postgis_* creds are duplicated per-service rather than centralized).
    airport_lat: float = 34.4262
    airport_lon: float = -119.8415

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql://{self.postgis_user}:{self.postgis_password}"
            f"@{self.postgis_host}:{self.postgis_port}/{self.postgis_db}"
        )


settings = Settings()
