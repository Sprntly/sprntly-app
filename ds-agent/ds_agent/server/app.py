"""FastAPI app served at api.sprntly.ai/agent/ (proxied behind nginx).

Top-level URL layout (after nginx strips the /agent/ prefix):

    /                                      hub: cards landing page (HTML)
    /{agent_id}                            chat UI for one agent (HTML)
    /health                                unauth probe
    /static/...                            JS/CSS/samples

Per-agent JSON API (path-prefixed so sessions and state never collide
between agents):

    POST /api/login                        -> bearer token (global)
    POST /api/logout                       -> drops all sessions for this user
    GET  /api/session                      -> {authenticated: true}
    GET  /api/agents                       -> list of agent cards for the hub
    GET  /api/agents/{id}/state            -> per-agent files + transcript
    GET  /api/agents/{id}/samples
    POST /api/agents/{id}/load-sample
    POST /api/agents/{id}/upload           -> multipart files
    POST /api/agents/{id}/chat             -> NDJSON streaming
    POST /api/agents/{id}/reset
    GET  /api/files/{file_id}              -> proxy chart download (shared)
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import tempfile
import uuid
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

from . import agents as _agents
from . import auth as _auth
from . import tools as _files
from .agents import AgentConfig
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
    _runner_cache: dict[str, ChatRunner] = {}

    def _runner_for(agent: AgentConfig) -> ChatRunner:
        if agent.id not in _runner_cache:
            _runner_cache[agent.id] = ChatRunner(agent)
        return _runner_cache[agent.id]

    _anthropic_holder: dict[str, Anthropic] = {}

    def _anthropic() -> Anthropic:
        if "c" not in _anthropic_holder:
            key = os.environ.get("ANTHROPIC_API_KEY")
            if not key:
                raise RuntimeError("ANTHROPIC_API_KEY is not set.")
            _anthropic_holder["c"] = Anthropic(api_key=key)
        return _anthropic_holder["c"]

    app = FastAPI(title="Sprntly Agents", docs_url=None, redoc_url=None)

    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    require = _auth.require_session(serializer)
    optional = _auth.optional_session(serializer)

    def _resolve_agent(agent_id: str) -> AgentConfig:
        agent = _agents.get(agent_id)
        if not agent:
            raise HTTPException(404, "unknown_agent")
        return agent

    # ───── public ─────

    @app.get("/health", response_class=PlainTextResponse)
    def health() -> str:
        return "ok"

    @app.get("/", response_class=FileResponse)
    def root() -> FileResponse:
        # Single HTML for hub + chat; client-side JS routes on pathname.
        index = _STATIC_DIR / "index.html"
        if not index.exists():
            raise HTTPException(500, "ui_not_bundled")
        return FileResponse(str(index))

    # Per-agent landing — same HTML; the JS picks up agent_id from
    # window.location.pathname.
    @app.get("/{agent_id}", response_class=FileResponse)
    def agent_page(agent_id: str) -> FileResponse:
        if agent_id in _agents.RESERVED_AGENT_IDS:
            raise HTTPException(404, "not_found")
        # Don't 404 on unknown agent ids here — let the client render a
        # "unknown agent" state from /api/agents, so a typo doesn't bounce
        # an authenticated user out to a generic 404.
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
        return {"ok": True, "token": token}

    @app.post("/api/logout")
    def logout(sid: str | None = Depends(optional)) -> dict[str, Any]:
        if sid:
            for s in sessions.all_for_sid(sid):
                _wipe_session_files(s)
            sessions.reset(sid)
        return {"ok": True}

    @app.get("/api/session")
    def session_info(sid: str = Depends(require)) -> dict[str, Any]:
        return {"authenticated": True}

    # ───── hub: list of agents ─────

    @app.get("/api/agents")
    def list_agents() -> dict[str, Any]:
        return {
            "agents": [
                {
                    "id": a.id,
                    "name": a.name,
                    "tagline": a.tagline,
                    "icon": a.icon,
                    "description": a.description,
                    "status": a.status,
                    "accepts_files": a.accepts_files,
                }
                for a in _agents.AGENTS.values()
            ]
        }

    # ───── per-agent: dataset ─────

    @app.get("/api/agents/{agent_id}/samples")
    def list_samples(agent_id: str) -> dict[str, Any]:
        agent = _resolve_agent(agent_id)
        return {"samples": _list_samples(agent)}

    @app.post("/api/agents/{agent_id}/load-sample")
    def load_sample(
        agent_id: str, body: LoadSampleBody, sid: str = Depends(require)
    ) -> dict[str, Any]:
        agent = _resolve_agent(agent_id)
        sample = _find_sample(body.sample_id, agent)
        if not sample:
            raise HTTPException(404, "unknown_sample")
        s = sessions.get_or_create(sid, agent.id)
        _reset_dataset(s)
        _attach_files(s, [(Path(sample["path"]), sample["label"])])
        return {"ok": True, "label": s.dataset_label, "file_count": len(s.files)}

    @app.post("/api/agents/{agent_id}/upload")
    async def upload_files(
        agent_id: str,
        sid: str = Depends(require),
        files: list[UploadFile] = File(...),
    ) -> dict[str, Any]:
        agent = _resolve_agent(agent_id)
        if not agent.accepts_files:
            raise HTTPException(400, "agent_does_not_accept_files")
        if not files:
            raise HTTPException(400, "no_files")

        s = sessions.get_or_create(sid, agent.id)
        workdir = _UPLOAD_DIR / f"{sid}__{agent.id}" / uuid.uuid4().hex
        workdir.mkdir(parents=True, exist_ok=True)

        sources: list[tuple[Path, str]] = []
        for up in files:
            if not up.filename:
                raise HTTPException(400, "missing_filename")
            target = workdir / Path(up.filename).name
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
        except Exception as exc:  # noqa: BLE001
            shutil.rmtree(workdir, ignore_errors=True)
            raise HTTPException(502, f"files_api_error:{type(exc).__name__}") from exc

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

    @app.get("/api/agents/{agent_id}/state")
    def state(agent_id: str, sid: str = Depends(require)) -> dict[str, Any]:
        agent = _resolve_agent(agent_id)
        s = sessions.get_or_create(sid, agent.id)
        return {
            "agent": {
                "id": agent.id,
                "name": agent.name,
                "icon": agent.icon,
                "tagline": agent.tagline,
                "accepts_files": agent.accepts_files,
                "kickoff_message": agent.kickoff_message,
            },
            "has_dataset": s.has_files,
            "dataset_label": s.dataset_label,
            "files": [{"label": f.label, "size_bytes": f.size_bytes} for f in s.files],
            "messages": _visible_transcript(s.messages),
        }

    @app.post("/api/agents/{agent_id}/reset")
    def reset(agent_id: str, sid: str = Depends(require)) -> dict[str, Any]:
        agent = _resolve_agent(agent_id)
        s = sessions.get_or_create(sid, agent.id)
        _wipe_session_files(s)
        s.files = []
        s.dataset_label = None
        s.container_id = None
        s.messages = []
        return {"ok": True}

    # ───── per-agent: chat (streaming NDJSON) ─────

    @app.post("/api/agents/{agent_id}/chat")
    def chat(
        agent_id: str, body: ChatBody, sid: str = Depends(require)
    ) -> StreamingResponse:
        agent = _resolve_agent(agent_id)
        s = sessions.get_or_create(sid, agent.id)
        if agent.accepts_files and not s.has_files:
            raise HTTPException(400, "no_dataset_loaded")
        msg = body.message.strip()
        if not msg:
            raise HTTPException(400, "empty_message")
        try:
            runner = _runner_for(agent)
        except RuntimeError as exc:
            raise HTTPException(500, f"chat_error:{exc}") from exc

        def _ndjson():
            try:
                for ev in runner.stream_turn(s, msg):
                    yield (json.dumps(ev, default=str) + "\n").encode("utf-8")
            except Exception as exc:  # noqa: BLE001
                yield (
                    json.dumps({"type": "error", "error": f"{type(exc).__name__}: {exc}"})
                    + "\n"
                ).encode("utf-8")

        return StreamingResponse(
            _ndjson(),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    # ───── shared: sandbox-artifact proxy ─────

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
    _wipe_session_files(s)
    s.files = []
    s.dataset_label = None
    s.container_id = None
    s.messages = []


def _attach_files(s: SessionState, sources: list[tuple[Path, str]]) -> None:
    uploaded = _files.upload_staged(
        [
            _files.StagedFile(local_path=p, label=label, size_bytes=p.stat().st_size)
            for p, label in sources
        ]
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
    for f in s.files:
        _files.delete_file(f.anthropic_file_id)
        if str(f.local_path).startswith(str(_UPLOAD_DIR)):
            try:
                f.local_path.unlink(missing_ok=True)
            except OSError:
                pass
    session_dir = _UPLOAD_DIR / f"{s.sid}__{s.agent_id}"
    if session_dir.exists():
        for sub in session_dir.iterdir():
            if sub.is_dir():
                try:
                    shutil.rmtree(sub, ignore_errors=True)
                except OSError:
                    pass


def _list_samples(agent: AgentConfig) -> list[dict[str, Any]]:
    if not _SAMPLES_DIR.exists():
        return []
    samples = []
    for sample_id in agent.samples:
        path = _SAMPLES_DIR / f"{sample_id}.csv"
        if not path.exists():
            continue
        meta = _SAMPLES_META.get(sample_id, {})
        samples.append(
            {
                "id": sample_id,
                "label": meta.get("label", sample_id),
                "description": meta.get("description", ""),
                "row_count": meta.get("row_count", 0),
            }
        )
    return samples


def _find_sample(sample_id: str, agent: AgentConfig) -> dict[str, Any] | None:
    if sample_id not in agent.samples:
        return None
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
