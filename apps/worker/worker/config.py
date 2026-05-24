from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"
    envs_dir: str = "./envs"
    # Where the API lives, for the Beat-driven scheduler tick.
    api_base_url: str = "http://localhost:8000"
    # Shared secret for /internal/* endpoints; must match settings.internal_api_token
    # on the API. Blank disables the header check on the API side.
    internal_api_token: str = ""
    # Beat fires the scheduler tick this often.
    scheduler_tick_seconds: int = 60


settings = Settings()
