"""In-memory per-session state.

Holds the uploaded/selected CSV path, goal metric, and the chat
transcript that gets fed back to Claude on each turn. Lifetime is the
process — fine for a pilot. If the service restarts, sessions vanish.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SessionState:
    sid: str
    created_at: float = field(default_factory=time.time)
    csv_path: Path | None = None
    dataset_label: str | None = None
    goal_metric: str | None = None
    # Anthropic messages list — each entry is {"role": "user"|"assistant", "content": ...}
    messages: list[dict[str, Any]] = field(default_factory=list)
    # Cached findings so follow-ups don't re-run the full pipeline.
    last_run: dict[str, Any] | None = None


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
        """Drop sessions older than max_age_s. Returns number purged."""
        cutoff = time.time() - max_age_s
        with self._lock:
            stale = [s for s, st in self._sessions.items() if st.created_at < cutoff]
            for s in stale:
                del self._sessions[s]
            return len(stale)
