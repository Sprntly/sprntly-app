"""In-memory per-session state.

Holds:
  - the locally-stored CSV path (display + download-only — Claude never reads it directly)
  - the Anthropic Files-API file_id that mirrors it (Claude reads via that)
  - the code-execution container_id so successive turns share state
  - the chat transcript (Anthropic message list, fed back on each turn)
  - rendered code-execution bundles (for the UI to show inline)

Lifetime is the process — fine for a pilot. If the service restarts,
sessions vanish.
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

    # Local copy of the user's CSV (for ingest helpers and reset cleanup).
    csv_path: Path | None = None
    dataset_label: str | None = None

    # The same CSV uploaded to Anthropic's Files API, so Claude's sandbox
    # can read it via a `container_upload` content block on the next turn.
    anthropic_file_id: str | None = None
    # Set True after we've attached the file to one message; subsequent
    # turns use the same container and don't need to re-attach.
    anthropic_file_attached: bool = False

    # The code-execution container Claude is using. Anthropic returns it
    # in `response.container.id`; passing it back on the next request
    # reuses the same filesystem + installed packages + variables.
    container_id: str | None = None

    # Anthropic messages list — each entry is {"role": "user"|"assistant", "content": ...}
    messages: list[dict[str, Any]] = field(default_factory=list)


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
