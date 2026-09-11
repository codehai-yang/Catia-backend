from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_name: str = "Catia Web API"
    version: str = "0.1.0"
    debug: bool = False

    # Server
    host: str = "127.0.0.1"
    port: int = 8000

    # API
    api_prefix: str = "/api"

    # CATIA
    catia_enabled: bool = True

@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()