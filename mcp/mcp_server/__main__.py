"""Entrypoint for `uvicorn mcp_server.__main__:app` (used by systemd)."""

from dotenv import load_dotenv

load_dotenv()  # load .env before anything reads os.environ

from .app import create_app  # noqa: E402

app = create_app()
