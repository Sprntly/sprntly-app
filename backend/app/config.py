from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = ""
    allowed_origins: str = "http://localhost:3000"
    env: str = "development"

    demo_password: str = ""
    jwt_secret: str = "dev-only-change-me"
    session_ttl_hours: int = 720
    cookie_domain: str = ""

    db_path: str = str(REPO_ROOT / "data" / "sprintly.db")

    @property
    def origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def cookie_secure(self) -> bool:
        return self.env == "production"


settings = Settings()
