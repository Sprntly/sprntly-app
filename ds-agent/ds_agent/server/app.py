"""FastAPI app served at api.sprntly.ai/agent/ (proxied behind nginx).

Routes (all live under the nginx `/agent/` prefix at deploy time):

    GET  /                          — login page or chat UI (HTML)
    GET  /health                    — unauth health probe
    POST /api/login                 — exchange password for bearer token
    POST /api/logout                — clear session server-side
    GET  /api/session               — current session info (or 401)
    GET  /api/samples               — list bundled sample datasets
    POST /api/load-sample           — load a sample by id into the session
    POST /api/upload                — upload one or many files (csv/json/parquet/
                                      xlsx/txt/pdf/...) or a zip
    GET  /api/state                 — files + transcript snapshot for the UI
    POST /api/chat                  — send one user message, get assistant reply
    POST /api/reset                 — wipe chat history + loaded files
    GET  /api/files/{file_id}       — proxy-download a file written by the
                                      sandbox (charts, derived CSVs)
"""

from __future__ import annotations

import os
import secrets
import shutil
import tempfile
import uuid
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
from .state import FileEntry, SessionState, SessionStore


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
            _wipe_session_files(sessions.get_or_create(sid))
            sessions.reset(sid)
        return {"ok": True}

    @app.get("/api/session")
    def session_info(sid: str = Depends(require)) -> dict[str, Any]:
        s = sessions.get_or_create(sid)
        return {
            "authenticated": True,
            "has_dataset": s.has_files,
            "dataset_label": s.dataset_label,
            "file_count": len(s.files),
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
        _reset_dataset(s)
        _attach_files(s, [(Path(sample["path"]), sample["label"])])
        return {"ok": True, "label": s.dataset_label, "file_count": len(s.files)}

    @app.post("/api/upload")
    async def upload_files(
        sid: str = Depends(require),
        files: list[UploadFile] = File(...),
    ) -> dict[str, Any]:
        if not files:
            raise HTTPException(400, "no_files")

        s = sessions.get_or_create(sid)
        # Per-upload workdir (zip extractions go here; raw files copied here too).
        workdir = _UPLOAD_DIR / sid / uuid.uuid4().hex
        workdir.mkdir(parents=True, exist_ok=True)

        # Spool each upload to disk so the staging code can inspect them.
        sources: list[tuple[Path, str]] = []
        for up in files:
            if not up.filename:
                raise HTTPException(400, "missing_filename")
            target = workdir / Path(up.filename).name  # strip any client-side path
            with target.open("wb") as out:
                shutil.copyfileobj(up.file, out)
            sources.append((target, up.filename))

        try:
            staged = _files.stage_uploads(sources, workdir, existing_count=len(s.files))
        except _files.IngestError as exc:
            shutil.rmtree(workdir, ignore_errors=True)
            raise HTTPException(400, str(exc)) from exc

        try:
            uploaded = _files.upload_staged(staged)
        except Exception as exc:  # noqa: BLE001 — surface Files API errors as 502
            shutil.rmtree(workdir, ignore_errors=True)
            raise HTTPException(502, f"files_api_error:{type(exc).__name__}") from exc

        # Replace any prior files (we treat each /upload as a "new analysis context").
        _wipe_session_files(s)
        s.files = [
            FileEntry(
                local_path=u.local_path,
                label=u.label,
                anthropic_file_id=u.anthropic_file_id,
                size_bytes=u.size_bytes,
            )
            for u in uploaded
        ]
        s.dataset_label = _summarize_label(s.files)
        s.container_id = None
        s.messages = []

        return {
            "ok": True,
            "label": s.dataset_label,
            "file_count": len(s.files),
            "files": [{"label": f.label, "size_bytes": f.size_bytes} for f in s.files],
        }

    @app.get("/api/state")
    def state(sid: str = Depends(require)) -> dict[str, Any]:
        s = sessions.get_or_create(sid)
        return {
            "has_dataset": s.has_files,
            "dataset_label": s.dataset_label,
            "files": [{"label": f.label, "size_bytes": f.size_bytes} for f in s.files],
            "messages": _visible_transcript(s.messages),
        }

    @app.post("/api/reset")
    def reset(sid: str = Depends(require)) -> dict[str, Any]:
        s = sessions.get_or_create(sid)
        _wipe_session_files(s)
        s.files = []
        s.dataset_label = None
        s.container_id = None
        s.messages = []
        return {"ok": True}

    # ───── chat ─────

    @app.post("/api/chat")
    def chat(body: ChatBody, sid: str = Depends(require)) -> dict[str, Any]:
        s = sessions.get_or_create(sid)
        if not s.has_files:
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
            "code_executions": [
                asdict(ce) if is_dataclass(ce) else ce for ce in result.code_executions
            ],
        }

    # ───── sandbox-artifact proxy ─────

    @app.get("/api/files/{file_id}")
    def download_file(file_id: str, sid: str = Depends(require)) -> StreamingResponse:
        try:
            meta = _anthropic().beta.files.retrieve_metadata(file_id)
            resp = _anthropic().beta.files.download(file_id)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(404, f"file_unavailable:{exc}") from exc

        mime = getattr(meta, "mime_type", None) or "application/octet-stream"

        def _iter():
            for chunk in resp.iter_bytes(chunk_size=64 * 1024):
                if chunk:
                    yield chunk

        return StreamingResponse(_iter(), media_type=mime)

    return app


# ─────────────────────── helpers ───────────────────────


def _reset_dataset(s: SessionState) -> None:
    """Clear file state without touching anything else; used before loading a new sample."""
    _wipe_session_files(s)
    s.files = []
    s.dataset_label = None
    s.container_id = None
    s.messages = []


def _attach_files(s: SessionState, sources: list[tuple[Path, str]]) -> None:
    """For samples — skip the multi-file staging pipeline (single trusted file)."""
    uploaded = _files.upload_staged(
        [_files.StagedFile(local_path=p, label=label, size_bytes=p.stat().st_size) for p, label in sources]
    )
    s.files = [
        FileEntry(
            local_path=u.local_path,
            label=u.label,
            anthropic_file_id=u.anthropic_file_id,
            size_bytes=u.size_bytes,
        )
        for u in uploaded
    ]
    s.dataset_label = _summarize_label(s.files)


def _summarize_label(files: list[FileEntry]) -> str:
    if not files:
        return ""
    if len(files) == 1:
        return files[0].label
    if len(files) <= 3:
        return f"{len(files)} files: " + ", ".join(f.label for f in files)
    head = ", ".join(f.label for f in files[:2])
    return f"{len(files)} files: {head}, +{len(files) - 2} more"


def _wipe_session_files(s: SessionState) -> None:
    """Delete every Anthropic file + local upload tied to this session."""
    for f in s.files:
        _files.delete_file(f.anthropic_file_id)
        if str(f.local_path).startswith(str(_UPLOAD_DIR)):
            try:
                f.local_path.unlink(missing_ok=True)
            except OSError:
                pass
    # Tidy up per-session upload workdir if empty.
    session_dir = _UPLOAD_DIR / s.sid
    if session_dir.exists():
        for sub in session_dir.iterdir():
            if sub.is_dir():
                try:
                    shutil.rmtree(sub, ignore_errors=True)
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
    return {"path": path, "label": meta.get("label", sample_id)}


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
    """Strip Anthropic-specific blocks for UI consumption."""
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
