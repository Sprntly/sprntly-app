"""FastAPI app served at api.sprntly.ai/agent/ (proxied behind nginx).

Routes (all live under the nginx `/agent/` prefix at deploy time, so
e.g. `GET /` here is `GET /agent/` from the public internet):

    GET  /                   — login page or chat UI (HTML)
    GET  /health             — unauth health probe
    POST /api/login          — exchange password for session cookie
    POST /api/logout         — clear session cookie
    GET  /api/session        — current session info (or 401)
    GET  /api/samples        — list bundled sample datasets
    POST /api/load-sample    — load a sample by id into the session
    POST /api/upload         — upload a CSV (multipart)
    GET  /api/state          — dataset/goal/last-run snapshot for the UI
    POST /api/chat           — send one user message, get assistant reply
    POST /api/reset          — wipe chat history + loaded dataset (keep auth)
"""

from __future__ import annotations

import secrets
import shutil
import tempfile
from pathlib import Path
from typing import Any

from fastapi import (
    Depends,
    FastAPI,
    File,
    HTTPException,
    UploadFile,
)
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth as _auth
from .chat import ChatRunner
from .state import SessionStore


_HERE = Path(__file__).parent
_STATIC_DIR = _HERE / "static"
_SAMPLES_DIR = _HERE / "samples"
_UPLOAD_DIR = Path(
    # In production, systemd unit overrides this via env. /tmp is the fallback.
    __import__("os").environ.get("AGENT_UPLOAD_DIR", tempfile.gettempdir() + "/sprntly-agent-uploads")
)
_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


class LoginBody(BaseModel):
    password: str


class ChatBody(BaseModel):
    message: str


class LoadSampleBody(BaseModel):
    sample_id: str


def create_app() -> FastAPI:
    cfg = _auth.load_config()
    serializer = _auth.make_serializer(cfg)
    sessions = SessionStore()
    # Chat runner is lazily created on first /api/chat call so the rest of the
    # service can still start in environments without ANTHROPIC_API_KEY (tests).
    _runner_holder: dict[str, ChatRunner] = {}

    def _runner() -> ChatRunner:
        if "r" not in _runner_holder:
            _runner_holder["r"] = ChatRunner()
        return _runner_holder["r"]

    app = FastAPI(title="Sprntly Data Science Agent", docs_url=None, redoc_url=None)

    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    require = _auth.require_session(serializer)
    optional = _auth.optional_session(serializer)

    # ───── health & root ─────

    @app.get("/health", response_class=PlainTextResponse)
    def health() -> str:
        return "ok"

    @app.get("/", response_class=FileResponse)
    def root() -> FileResponse:
        index = _STATIC_DIR / "index.html"
        if not index.exists():
            raise HTTPException(500, "ui_not_bundled")
        return FileResponse(str(index))

    # ───── auth ─────

    @app.post("/api/login")
    def login(body: LoginBody) -> dict[str, Any]:
        # Constant-time compare so we don't leak password length / prefix.
        if not secrets.compare_digest(body.password, cfg.password):
            raise HTTPException(401, "invalid_password")
        sid, token = _auth.issue_token(serializer)
        sessions.get_or_create(sid)
        # Client stores `token` in localStorage and sends it back as
        # `Authorization: Bearer <token>` on every subsequent request.
        return {"ok": True, "token": token}

    @app.post("/api/logout")
    def logout(sid: str | None = Depends(optional)) -> dict[str, Any]:
        if sid:
            sessions.reset(sid)
        return {"ok": True}

    @app.get("/api/session")
    def session_info(sid: str = Depends(require)) -> dict[str, Any]:
        s = sessions.get_or_create(sid)
        return {
            "authenticated": True,
            "has_dataset": s.csv_path is not None,
            "dataset_label": s.dataset_label,
            "goal_metric": s.goal_metric,
            "message_count": _user_visible_count(s.messages),
        }

    # ───── dataset ─────

    @app.get("/api/samples")
    def list_samples() -> dict[str, Any]:
        return {"samples": _list_samples()}

    @app.post("/api/load-sample")
    def load_sample(body: LoadSampleBody, sid: str = Depends(require)) -> dict[str, Any]:
        sample = _find_sample(body.sample_id)
        if not sample:
            raise HTTPException(404, "unknown_sample")
        s = sessions.get_or_create(sid)
        s.csv_path = sample["path"]
        s.dataset_label = sample["label"]
        s.goal_metric = sample.get("default_goal_metric")
        s.last_run = None
        return {
            "ok": True,
            "label": s.dataset_label,
            "goal_metric": s.goal_metric,
        }

    @app.post("/api/upload")
    async def upload_csv(
        sid: str = Depends(require),
        file: UploadFile = File(...),
    ) -> dict[str, Any]:
        if not file.filename or not file.filename.lower().endswith(".csv"):
            raise HTTPException(400, "only_csv_supported")
        target = _UPLOAD_DIR / f"{sid}.csv"
        with target.open("wb") as out:
            shutil.copyfileobj(file.file, out)
        s = sessions.get_or_create(sid)
        s.csv_path = target
        s.dataset_label = file.filename
        s.goal_metric = None
        s.last_run = None
        return {"ok": True, "label": s.dataset_label}

    @app.get("/api/state")
    def state(sid: str = Depends(require)) -> dict[str, Any]:
        s = sessions.get_or_create(sid)
        return {
            "has_dataset": s.csv_path is not None,
            "dataset_label": s.dataset_label,
            "goal_metric": s.goal_metric,
            "messages": _visible_transcript(s.messages),
        }

    @app.post("/api/reset")
    def reset(sid: str = Depends(require)) -> dict[str, Any]:
        s = sessions.get_or_create(sid)
        if s.csv_path and str(s.csv_path).startswith(str(_UPLOAD_DIR)):
            try:
                Path(s.csv_path).unlink(missing_ok=True)
            except OSError:
                pass
        s.csv_path = None
        s.dataset_label = None
        s.goal_metric = None
        s.last_run = None
        s.messages = []
        return {"ok": True}

    # ───── chat ─────

    @app.post("/api/chat")
    def chat(body: ChatBody, sid: str = Depends(require)) -> dict[str, Any]:
        s = sessions.get_or_create(sid)
        if not s.csv_path:
            raise HTTPException(400, "no_dataset_loaded")
        msg = body.message.strip()
        if not msg:
            raise HTTPException(400, "empty_message")
        try:
            result = _runner().turn(s, msg)
        except RuntimeError as exc:
            raise HTTPException(500, f"chat_error:{exc}") from exc
        return {
            "assistant": result.assistant_text,
            "tool_calls": result.tool_calls,
        }

    return app


# ─────────────────────── helpers ───────────────────────


def _list_samples() -> list[dict[str, str]]:
    if not _SAMPLES_DIR.exists():
        return []
    samples = []
    for path in sorted(_SAMPLES_DIR.glob("*.csv")):
        meta = _SAMPLES_META.get(path.stem, {})
        samples.append(
            {
                "id": path.stem,
                "label": meta.get("label", path.stem),
                "description": meta.get("description", ""),
                "default_goal_metric": meta.get("default_goal_metric", ""),
                "row_count": meta.get("row_count", 0),
            }
        )
    return samples


def _find_sample(sample_id: str) -> dict[str, Any] | None:
    path = _SAMPLES_DIR / f"{sample_id}.csv"
    if not path.exists():
        return None
    meta = _SAMPLES_META.get(sample_id, {})
    return {
        "path": path,
        "label": meta.get("label", sample_id),
        "default_goal_metric": meta.get("default_goal_metric"),
    }


# Static metadata for bundled samples. Update when adding new sample CSVs.
_SAMPLES_META: dict[str, dict[str, Any]] = {
    "saas_retention": {
        "label": "SaaS retention (synthetic)",
        "description": (
            "~4,000 users with first-week behaviors and 30-day retention as the goal "
            "metric. Engineered with known causal patterns so the agent can demonstrate "
            "what it does."
        ),
        "default_goal_metric": "retention_30d",
        "row_count": 4000,
    },
}


def _user_visible_count(messages: list[dict[str, Any]]) -> int:
    """Count only user + assistant *text* turns — tool exchanges don't count."""
    n = 0
    for m in messages:
        if m["role"] == "user" and isinstance(m["content"], str):
            n += 1
        elif m["role"] == "assistant" and any(
            isinstance(b, dict) and b.get("type") == "text"
            for b in (m["content"] if isinstance(m["content"], list) else [])
        ):
            n += 1
    return n


def _visible_transcript(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Strip out tool messages and tool_use blocks for UI consumption."""
    out: list[dict[str, str]] = []
    for m in messages:
        if m["role"] == "user":
            if isinstance(m["content"], str):
                out.append({"role": "user", "text": m["content"]})
            # tool_result-only user messages are skipped
        elif m["role"] == "assistant":
            text_parts = []
            for block in m["content"] if isinstance(m["content"], list) else []:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
            if text_parts:
                out.append({"role": "assistant", "text": "\n".join(text_parts).strip()})
    return out
