from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://noodle:noodle@localhost:5432/noodle"
    redis_url: str = "redis://localhost:6379/0"
    cors_origins: str = "http://localhost:5173"
    envs_dir: str = "./envs"
    enable_venv_builds: bool = True
    run_synchronously: bool = False
    use_subprocess_runner: bool = False
    auth_required: bool = False
    secret_key: str = "noodle-dev-secret-change-me-in-production"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
