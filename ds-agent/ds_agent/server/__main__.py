"""Entrypoint for `python -m ds_agent.server` (used by systemd via uvicorn)."""

from .app import create_app


app = create_app()
