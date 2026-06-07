"""Entrypoint for `python -m ds_agent.server` (used by systemd via uvicorn)."""

from dotenv import load_dotenv

load_dotenv()  # load .env before anything reads os.environ

from .app import create_app  # noqa: E402


app = create_app()
