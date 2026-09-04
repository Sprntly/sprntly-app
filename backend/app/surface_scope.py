"""SurfaceScope — the single typed descriptor that parameterizes
`qa_agent.answer()` across the two answer surfaces: main chat and the
private ("My chat with Sprntly") project chat.

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


#: Accept-with-nudge (Babajide decision — the fix for `is_project_tool_
#: request`'s false-negative risk). `is_project_tool_request` is a cheap,
#: deliberately narrow gate — an IMPLIED delegation/execution request it
#: misses falls through to the composer, which has no `delegate_task`/
#: `execute_task` tool at all. Unlike the connector-family gates (a miss
#: there still degrades to a correct, just thinner, KG-grounded answer),
#: silently answering an unrouted delegation in prose is a FUNCTIONAL
#: failure — the task never gets created. Both project surfaces append this
#: sentence to their `SurfaceScope.system_addendum`, so it reaches the model
#: on the composer fall-through path (folded into `history`, `qa_agent.
#: answer`'s decline seam) as well as the sixth branch itself (harmless
#: there — the tools ARE available on that path).
PROJECT_TOOL_NUDGE = (
    "If this message is asking you to hand off, assign, or delegate a task "
    "to a teammate, or to draft/execute something on the team's behalf, but "
    "you do not have a delegate/execute tool available in THIS reply, do "
    "NOT silently answer as if nothing was asked and do NOT pretend to have "
    "done it. Tell the user plainly to phrase it explicitly — for example "
    "'delegate the onboarding doc to Ada' — so it gets routed correctly."
)


#: The single-sourced "answer from THIS block, don't deflect" header both
#: project surfaces frame their real facts with — moved here, byte-for-byte,
#: from its original private-only home (`routes/ask.py`'s inline
#: `project_preamble` literal) so the group composer fall-through
#: (`qa_agent._fold_project_context`) can prepend the SAME framing to its
#: own non-empty `context_payload` instead of folding the roster/ledger/
#: memory block as a passive, unframed "Context:" row the model is free to
#: deflect on. Private usage is unchanged byte-for-byte (`routes/ask.py`).
PROJECT_FACTS_AUTHORITATIVE_PREAMBLE = (
    "[Project workspace facts — AUTHORITATIVE for THIS project, and "
    "the source of truth for anything about the project itself. The "
    "lines below are the real members (and their roles), the real "
    "task/delegation ledger, and the real artifacts (PRDs, "
    "prototypes, evidence, reports, ticket sets) of the project this "
    "chat belongs to. When asked who is on this project, what tasks "
    "are open / who is doing what, or how many / which PRDs or "
    "artifacts exist, answer directly and specifically from these "
    "facts. Do NOT say you cannot see them and do NOT tell the user "
    "to connect a data source for them — this block IS that source.]"
)


@dataclass(frozen=True)
class SurfaceScope:
    """One turn's surface-specific context, built once by the caller
    (`ask_job_runner._single_shot` for the private surface) and handed to
    `qa_agent.answer(scope=...)`.

    Field-by-field mapping to the pre-collapse code it replaces:
      - `context_payload` — the breadth block. Private: already folded into
        `history` by `routes/ask.py` before this ever reaches `answer()`, so
        the private scope leaves this "" (nothing to duplicate).
      - `system_addendum` — the surface's own system-prompt text (private:
        the relocated individual-chat instructions + roster), appended ahead
        of `context_payload`. Consumed ONLY by the tool loop.
      - `composer_fold_addendum` — the SEPARATE, tool-guidance-free text
        folded into the gate-decline / composer fall-through path instead
        (falls back to `system_addendum` when empty, for pre-existing
        callers).
      - `extra_tools` — exactly the 4 project read tools + `delegate_task` +
        `execute_task` (6 total) for the project surface; empty for main.
      - `roster` — `list_members(project_id)`, fetched once per turn.
      - `assigner_identity` — `{assigner_user_id, source_conversation_id}`;
        threaded into `handle_delegate_task`/`handle_execute_task` so
        delegation attribution survives the collapse.
      - `post_turn` — the surface's own turn-writer, handed to
        `handle_execute_task` for its progress posts.
      - `capabilities` — declarative only (streaming/cancel flags this
        descriptor documents); nothing in `answer()` branches on it — the
        real streaming/cancel behaviour is a structural property of WHICH
        code path a turn takes (the sixth tool-loop branch never streams;
        the untouched composer path always does), not a flag read at
        runtime.
    """

    surface: Surface
    project_id: Optional[int] = None
    context_payload: str = ""
    system_addendum: str = ""
    #: The gate-decline / composer fall-through's OWN addendum — deliberately
    #: a SEPARATE field from `system_addendum`, not the same string reused.
    #: `system_addendum` is the tool-loop's system prompt and may describe
    #: tools (e.g. `delegate_task`, with its verbatim handoff-confirmation
    #: template) that are only real on that path; folding that same text into
    #: a turn with no tools available turns a tool-description into a
    #: fabricated claim of having used it. When empty (every caller that
    #: predates this field), `qa_agent._fold_project_context` falls back to
    #: `system_addendum` — so this is purely additive, not a required field.
    composer_fold_addendum: str = ""
    extra_tools: tuple[dict, ...] = ()
    roster: tuple[dict, ...] = ()
    assigner_identity: Optional[dict] = None
    post_turn: Optional[Callable[[str], None]] = None
    capabilities: Optional[dict] = None
    #: An in-band `edit_prd` tool's handler, given the tool's `{instruction}`
    #: input to apply an edit DIRECTLY through the shared editor against a
    #: target the handler closes over, returning `(narration, None)`. No
    #: surface currently populates this field — always `None` today — but it
    #: is kept (with its `Callable[..., tuple[str, Optional[dict]]]` type)
    #: because `qa_agent.py` unconditionally reads `scope.edit_prd_handler`
    #: guarded on `is not None`; removing the field would break that read.
    edit_prd_handler: Optional[Callable[[dict], "tuple[str, Optional[dict]]"]] = None
    #: True when THIS project surface has ≥1 uploaded document (a
    #: `custom_artifact`) attached. Read by `qa_agent`'s routing gates
    #: (`_skip_project_connectors` and the sixth-branch admission) to keep a
    #: document-phrased or bare factual question in the project tool loop — where
    #: `get_artifact_content` can read the project's own documents — instead of
    #: bailing to the workspace connector document-search. Default False for
    #: main/workspace scopes and for projects with no uploaded documents, so
    #: their routing is byte-identical to before this field existed.
    has_project_documents: bool = False

    @property
    def is_noop(self) -> bool:
        """True for `surface == main` — the enum-main path is a no-op ALIAS
        for `scope is None` (AC2), never a second code path: every seam
        `answer()` guards on is empty/falsy for a main-surface scope by
        construction."""
        return self.surface == Surface.main
