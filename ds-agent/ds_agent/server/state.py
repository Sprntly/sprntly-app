"""In-memory per-(session, agent) state.

A user can have multiple agents open in different tabs without their
conversations colliding. The store keys on `(sid, agent_id)`.
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
    agent_id: str
    created_at: float = field(default_factory=time.time)

    files: list[FileEntry] = field(default_factory=list)
    dataset_label: str | None = None

    anthropic_file_attached: bool = False
    container_id: str | None = None

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
        self._sessions: dict[tuple[str, str], SessionState] = {}

    def get_or_create(self, sid: str, agent_id: str) -> SessionState:
        with self._lock:
            key = (sid, agent_id)
            existing = self._sessions.get(key)
            if existing is None:
                existing = SessionState(sid=sid, agent_id=agent_id)
                self._sessions[key] = existing
            return existing

    def reset(self, sid: str, agent_id: str | None = None) -> None:
        """Drop session(s). If agent_id is None, drop ALL sessions for this sid (logout)."""
        with self._lock:
            if agent_id is None:
                keys = [k for k in self._sessions if k[0] == sid]
                for k in keys:
                    del self._sessions[k]
            else:
                self._sessions.pop((sid, agent_id), None)

    def all_for_sid(self, sid: str) -> list[SessionState]:
        with self._lock:
            return [v for k, v in self._sessions.items() if k[0] == sid]

    def gc(self, max_age_s: int = 60 * 60 * 24) -> int:
        cutoff = time.time() - max_age_s
        with self._lock:
            stale = [k for k, st in self._sessions.items() if st.created_at < cutoff]
            for k in stale:
                del self._sessions[k]
            return len(stale)
