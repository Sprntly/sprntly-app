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

    # Internal service-to-service API (DS Agent → Backend)
    internal_api_key: str = ""

    # Google Drive connector (OAuth)
    google_client_id: str = ""
    google_client_secret: str = ""
    google_oauth_redirect_uri: str = ""
    token_encryption_key: str = ""
    frontend_url: str = "http://localhost:3000"

    # Supabase — set in EC2 + GH secrets. Backend uses the service-role
    # key (bypasses RLS) since it's a trusted server, not a browser.
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    # JWT secret from Supabase project settings (Settings → API → JWT Secret).
    # Used to verify access tokens sent as Authorization: Bearer …
    supabase_jwt_secret: str = ""
    # Shadow-write inserts to Supabase alongside SQLite. Reads stay on
    # SQLite. Defaults off so the flag has to be flipped per environment.
    supabase_dual_write: bool = False

    # Figma connector (OAuth 2.0)
    figma_client_id: str = ""
    figma_client_secret: str = ""
    figma_oauth_redirect_uri: str = ""

    # GitHub connector (GitHub App with user-to-server OAuth)
    github_app_id: str = ""
    github_app_client_id: str = ""
    github_app_client_secret: str = ""
    # Private key as a PEM string (-----BEGIN ... -----END ...). When stored in
    # .env, newlines should be literal \n; we normalize at load time.
    github_app_private_key: str = ""
    github_oauth_redirect_uri: str = ""
    github_webhook_secret: str = ""

    @property
    def github_app_private_key_pem(self) -> str:
        """Normalize the PEM: turn literal `\\n` sequences into real newlines."""
        return (self.github_app_private_key or "").replace("\\n", "\n")

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
