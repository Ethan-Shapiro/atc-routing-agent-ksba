"""Env-driven settings for the evaluation harness. Mirrors the other services' config.py."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgis_db: str
    postgis_user: str
    postgis_password: str
    postgis_host: str = "postgis"
    postgis_port: int = 5432

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql://{self.postgis_user}:{self.postgis_password}"
            f"@{self.postgis_host}:{self.postgis_port}/{self.postgis_db}"
        )


settings = Settings()
