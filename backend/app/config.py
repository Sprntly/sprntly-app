from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = ""
    allowed_origins: str = "http://localhost:3000"
    env: str = "development"

    demo_password: str = ""
    jwt_secret: str = "dev-only-change-me"
    session_ttl_hours: int = 720
    cookie_domain: str = ""

    # Where corpus markdown + uploaded originals live. In prod this is set to
    # /var/lib/sprntly/data so EC2 git pulls don't wipe uploads. Templates
    # (sprntly_prd_template.md, sprntly_evidence_template.md) live here too
    # but ship via the repo; on first boot we seed them if the dir is empty.
    data_dir: str = str(REPO_ROOT / "data")
    # Templates ship in-repo and never get uploaded by users. Keeping them
    # under the repo even when DATA_DIR points elsewhere means template
    # edits flow through normal PRs.
    template_dir: str = str(REPO_ROOT / "data")

    db_path: str = str(REPO_ROOT / "data" / "sprintly.db")

    @property
    def origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def cookie_secure(self) -> bool:
        return self.env == "production"

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir)

    @property
    def template_path(self) -> Path:
        return Path(self.template_dir)


settings = Settings()

# Back-compat: existing code (corpus.py, etc.) imports DATA_DIR directly.
# Keep it as a module-level alias of settings.data_path.
DATA_DIR = settings.data_path
TEMPLATE_DIR = settings.template_path
