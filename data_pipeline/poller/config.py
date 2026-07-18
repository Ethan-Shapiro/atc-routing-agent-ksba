"""Env-driven settings for the OpenSky poller service."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # OpenSky OAuth2 client_credentials
    opensky_client_id: str
    opensky_client_secret: str
    opensky_daily_credit_budget: int = 4000

    # PostGIS
    postgis_db: str
    postgis_user: str
    postgis_password: str
    postgis_host: str = "postgis"
    postgis_port: int = 5432

    # Domain constants
    lax_lat: float = 33.9425
    lax_lon: float = -118.4081
    coverage_radius_miles: float = 50.0
    poll_interval_seconds: float = 12.0

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql://{self.postgis_user}:{self.postgis_password}"
            f"@{self.postgis_host}:{self.postgis_port}/{self.postgis_db}"
        )


settings = Settings()
