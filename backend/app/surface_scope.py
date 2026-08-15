"""SurfaceScope — the single typed descriptor that parameterizes
`qa_agent.answer()` across the three answer surfaces: main chat, the
private ("My chat with Sprntly") project chat, and the @Sprntly group
project chat.

`scope is None` — the default for every caller that predates this module —
or `SurfaceScope(surface=Surface.main)` are BOTH no-ops: `answer()` runs its
current code path completely unchanged. Every field below is additive, and
none of them is read anywhere on the main-chat path.

Pure data — no I/O, no imports from `qa_agent` or any project module, so
importing this file can never create a cycle.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional


class Surface(str, Enum):
    main = "main"
    project_private = "project_private"
    project_group = "project_group"


@dataclass(frozen=True)
class SurfaceScope:
    """One turn's surface-specific context, built once by the caller
    (`ask_job_runner._single_shot` for the private surface,
    `routes.projects._respond_as_group_agent` for the group surface) and
    handed to `qa_agent.answer(scope=...)`.

    Field-by-field mapping to the pre-collapse code it replaces:
      - `context_payload` — the breadth block. Private: already folded into
        `history` by `routes/ask.py` before this ever reaches `answer()`, so
        the private scope leaves this "" (nothing to duplicate). Group:
        `project_group_context.assemble_group_agent_context`'s block.
      - `system_addendum` — the surface's own system-prompt text (private:
        the relocated individual-chat instructions + roster; group: the
        relocated group instructions + roster + addressing note + any
        edit-status note), appended ahead of `context_payload`.
      - `extra_tools` — exactly the 4 project read tools + `delegate_task` +
        `execute_task` (6 total) for both project surfaces; empty for main.
      - `roster` — `list_members(project_id)`, fetched once per turn.
      - `assigner_identity` — `{assigner_user_id, source_conversation_id}`
        (private) or `{assigner_user_id, source_turn_id}` (group); threaded
        into `handle_delegate_task`/`handle_execute_task` so delegation
        attribution survives the collapse.
      - `post_turn` — the surface's own turn-writer, handed to
        `handle_execute_task` for its progress posts.
      - `prerendered_transcript` — group only: the speaker-attributed
        transcript (`"Name (job role): message"` lines), passed through
        WITHOUT being re-flattened into `answer()`'s single-user history
        model.
      - `capabilities` — declarative only (streaming/cancel flags this
        descriptor documents); nothing in `answer()` branches on it — the
        real streaming/cancel behaviour is a structural property of WHICH
        code path a turn takes (the sixth tool-loop branch never streams;
        the untouched composer path always does), not a flag read at
        runtime.
      - `multi_party` — flags the group surface for router/interceptor
        behaviour (the LT-8 input-shape decision, pinned at ship-gate).
    """

    surface: Surface
    project_id: Optional[int] = None
    context_payload: str = ""
    system_addendum: str = ""
    extra_tools: tuple[dict, ...] = ()
    roster: tuple[dict, ...] = ()
    assigner_identity: Optional[dict] = None
    post_turn: Optional[Callable[[str], None]] = None
    prerendered_transcript: Optional[str] = None
    capabilities: Optional[dict] = None
    multi_party: bool = False

    @property
    def is_noop(self) -> bool:
        """True for `surface == main` — the enum-main path is a no-op ALIAS
        for `scope is None` (AC2), never a second code path: every seam
        `answer()` guards on is empty/falsy for a main-surface scope by
        construction."""
        return self.surface == Surface.main
