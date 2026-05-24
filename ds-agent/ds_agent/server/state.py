"""In-memory per-session state.

A session can have **multiple files attached** (e.g. a CSV plus a
README, or several tables from a zip). Each `FileEntry` carries:
  - `local_path` — the file on the agent host (kept for cleanup)
  - `label` — the display name (also what the file is named inside
    the sandbox, after zip-path flattening if applicable)
  - `anthropic_file_id` — the Files API id Claude reads from
  - `attached` — True after we've sent a container_upload block for
    it; subsequent turns don't re-attach
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class FileEntry:
    local_path: Path
    label: str
    anthropic_file_id: str
    size_bytes: int
    attached: bool = False


@dataclass
class SessionState:
    sid: str
    created_at: float = field(default_factory=time.time)

    files: list[FileEntry] = field(default_factory=list)
    # Short display label: "data.csv" or "3 files: x.csv, y.csv, z.pdf"
    dataset_label: str | None = None

    # Code-execution container — persists across turns within a session.
    container_id: str | None = None

    # Anthropic messages list — full content blocks, fed back each turn.
    messages: list[dict[str, Any]] = field(default_factory=list)

    @property
    def has_files(self) -> bool:
        return bool(self.files)

    @property
    def unattached_files(self) -> list[FileEntry]:
        return [f for f in self.files if not f.attached]


class SessionStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, SessionState] = {}

    def get_or_create(self, sid: str) -> SessionState:
        with self._lock:
            existing = self._sessions.get(sid)
            if existing is None:
                existing = SessionState(sid=sid)
                self._sessions[sid] = existing
            return existing

    def reset(self, sid: str) -> None:
        with self._lock:
            self._sessions.pop(sid, None)

    def gc(self, max_age_s: int = 60 * 60 * 24) -> int:
        cutoff = time.time() - max_age_s
        with self._lock:
            stale = [s for s, st in self._sessions.items() if st.created_at < cutoff]
            for s in stale:
                del self._sessions[s]
            return len(stale)
