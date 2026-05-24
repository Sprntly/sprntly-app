"""FastAPI app served at api.sprntly.ai/agent/ (proxied behind nginx).

Routes (all live under the nginx `/agent/` prefix at deploy time, so
e.g. `GET /` here is `GET /agent/` from the public internet):

    GET  /                          — login page or chat UI (HTML)
    GET  /health                    — unauth health probe
    POST /api/login                 — exchange password for bearer token
    POST /api/logout                — clear session server-side
    GET  /api/session               — current session info (or 401)
    GET  /api/samples               — list bundled sample datasets
    POST /api/load-sample           — load a sample by id into the session
    POST /api/upload                — upload a CSV (multipart)
    GET  /api/state                 — dataset/messages snapshot for the UI
    POST /api/chat                  — send one user message, get assistant reply
    POST /api/reset                 — wipe chat history + loaded dataset
    GET  /api/files/{file_id}       — proxy-download a file written by the
                                      sandbox (charts, derived CSVs)
"""

from __future__ import annotations

import os
import secrets
import shutil
import tempfile
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from anthropic import Anthropic
from fastapi import (
    Depends,
    FastAPI,
    File,
    HTTPException,
    UploadFile,
)
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth as _auth
from . import tools as _files
from .chat import ChatRunner
from .state import SessionState, SessionStore


_HERE = Path(__file__).parent
_STATIC_DIR = _HERE / "static"
_SAMPLES_DIR = _HERE / "samples"
_UPLOAD_DIR = Path(
    os.environ.get("AGENT_UPLOAD_DIR", tempfile.gettempdir() + "/sprntly-agent-uploads")
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
    _runner_holder: dict[str, ChatRunner] = {}

    def _runner() -> ChatRunner:
        if "r" not in _runner_holder:
            _runner_holder["r"] = ChatRunner()
        return _runner_holder["r"]

    # Lazily-built Anthropic client for the file-download proxy. The chat
    # runner already has its own; we keep a separate handle so /api/files
    # works even if no chat has been issued yet.
    _anthropic_holder: dict[str, Anthropic] = {}

    def _anthropic() -> Anthropic:
        if "c" not in _anthropic_holder:
            key = os.environ.get("ANTHROPIC_API_KEY")
            if not key:
                raise RuntimeError("ANTHROPIC_API_KEY is not set.")
            _anthropic_holder["c"] = Anthropic(api_key=key)
        return _anthropic_holder["c"]

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
        if not secrets.compare_digest(body.password, cfg.password):
            raise HTTPException(401, "invalid_password")
        sid, token = _auth.issue_token(serializer)
        sessions.get_or_create(sid)
        return {"ok": True, "token": token}

    @app.post("/api/logout")
    def logout(sid: str | None = Depends(optional)) -> dict[str, Any]:
        if sid:
            _wipe_session_resources(sessions.get_or_create(sid))
            sessions.reset(sid)
        return {"ok": True}

    @app.get("/api/session")
    def session_info(sid: str = Depends(require)) -> dict[str, Any]:
        s = sessions.get_or_create(sid)
        return {
            "authenticated": True,
            "has_dataset": s.csv_path is not None,
            "dataset_label": s.dataset_label,
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
        _swap_dataset(s, sample["path"], sample["label"])
        return {"ok": True, "label": s.dataset_label}

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
        _swap_dataset(s, target, file.filename)
        return {"ok": True, "label": s.dataset_label}

    @app.get("/api/state")
    def state(sid: str = Depends(require)) -> dict[str, Any]:
        s = sessions.get_or_create(sid)
        return {
            "has_dataset": s.csv_path is not None,
            "dataset_label": s.dataset_label,
            "messages": _visible_transcript(s.messages),
        }

    @app.post("/api/reset")
    def reset(sid: str = Depends(require)) -> dict[str, Any]:
        s = sessions.get_or_create(sid)
        _wipe_session_resources(s)
        s.csv_path = None
        s.dataset_label = None
        s.anthropic_file_id = None
        s.anthropic_file_attached = False
        s.container_id = None
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

        # After a successful turn, we've attached the file (if any).
        if s.anthropic_file_id:
            s.anthropic_file_attached = True

        return {
            "assistant": result.assistant_text,
            "code_executions": [
                asdict(ce) if is_dataclass(ce) else ce for ce in result.code_executions
            ],
        }

    # ───── file proxy (charts, derived CSVs the sandbox produced) ─────

    @app.get("/api/files/{file_id}")
    def download_file(file_id: str, sid: str = Depends(require)) -> StreamingResponse:
        # No per-session ACL — file_ids are session-opaque random strings
        # so guessing is infeasible. Anthropic's Files API also enforces
        # workspace boundaries on its side.
        try:
            meta = _anthropic().beta.files.retrieve_metadata(file_id)
            resp = _anthropic().beta.files.download(file_id)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(404, f"file_unavailable:{exc}") from exc

        mime = getattr(meta, "mime_type", None) or "application/octet-stream"

        def _iter():
            # SDK returns a streaming body — iter in modest chunks.
            for chunk in resp.iter_bytes(chunk_size=64 * 1024):
                if chunk:
                    yield chunk

        return StreamingResponse(_iter(), media_type=mime)

    return app


# ─────────────────────── helpers ───────────────────────


def _swap_dataset(s: SessionState, path: Path, label: str) -> None:
    """Replace the session's dataset and reset everything tied to it.

    Uploads the CSV to Anthropic Files API and stores the file_id; the
    next chat turn will attach it via a container_upload block.
    """
    # Cleanup what's there.
    if s.anthropic_file_id:
        _files.delete_file(s.anthropic_file_id)
    if s.csv_path and str(s.csv_path).startswith(str(_UPLOAD_DIR)):
        try:
            Path(s.csv_path).unlink(missing_ok=True)
        except OSError:
            pass

    s.csv_path = path
    s.dataset_label = label
    s.anthropic_file_id = _files.upload_csv(path, filename=label)
    s.anthropic_file_attached = False
    s.container_id = None  # new dataset → fresh sandbox
    s.messages = []  # new dataset → fresh conversation


def _wipe_session_resources(s: SessionState) -> None:
    """Delete the Anthropic-side file + local upload, leaving session state intact for the caller to clear."""
    if s.anthropic_file_id:
        _files.delete_file(s.anthropic_file_id)
    if s.csv_path and str(s.csv_path).startswith(str(_UPLOAD_DIR)):
        try:
            Path(s.csv_path).unlink(missing_ok=True)
        except OSError:
            pass


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
        "row_count": 4000,
    },
}


def _visible_transcript(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip Anthropic-specific blocks (server_tool_use, tool_results) for UI consumption.

    Keeps only text from user and assistant turns, and the rendered
    code-execution bundles attached to the last assistant turn that
    produced any. The UI uses this to rehydrate a returning session.
    """
    out: list[dict[str, Any]] = []
    for m in messages:
        if m["role"] == "user":
            text_parts: list[str] = []
            for block in m["content"] if isinstance(m["content"], list) else [m["content"]]:
                if isinstance(block, str):
                    text_parts.append(block)
                elif isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
            if text_parts:
                out.append({"role": "user", "text": "\n".join(text_parts).strip()})

        elif m["role"] == "assistant":
            text_parts = []
            for block in m["content"] if isinstance(m["content"], list) else []:
                btype = getattr(block, "type", None) or (
                    block.get("type") if isinstance(block, dict) else None
                )
                if btype == "text":
                    text = getattr(block, "text", None) or (
                        block.get("text", "") if isinstance(block, dict) else ""
                    )
                    text_parts.append(text)
            if text_parts:
                out.append({"role": "assistant", "text": "\n".join(text_parts).strip()})
    return out
