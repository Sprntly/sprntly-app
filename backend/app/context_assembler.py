"""Pluggable context-assembler seam for the ask path.

Phase 1 — SCAFFOLDING ONLY. This module defines the seam by which ANY surface
can bring its own context assembler to `qa_agent.answer()`, but it registers
NONE. With `ASSEMBLER_REGISTRY` empty, `resolve_context_scope()` returns `None`
for every ask, so every ask runs the exact current (unscoped) main path
unchanged. No behaviour changes this phase.

`ContextScope` generalizes the older `app.surface_scope.SurfaceScope`: instead
of an enum of three fixed surfaces, any caller supplies an assembler keyed by a
`context_source["kind"]` string, and each assembler produces a `ContextScope`.
A `ContextScope` whose `is_noop` is True — like a `None` scope — is the no-op:
`answer()` runs its current code path completely unchanged.

Pure seam: no I/O, and no import of `qa_agent` or any surface module, so
importing this file can never create an import cycle.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class ContextScope:
    """One turn's assembled, surface-agnostic context, produced by a
    `ContextAssembler` and handed to `qa_agent.answer(scope=...)`.

    Field-by-field generalization of the descriptor it replaces
    (`app.surface_scope.SurfaceScope`):
      - ``system_addendum`` — the surface's own system-prompt text.
      - ``context_block`` — the breadth block (was ``context_payload``),
        prepended as one synthetic context row on the fall-through/decline
        seam.
      - ``tools`` — the surface's extra tools (was ``extra_tools``).
      - ``post_answer_hook`` — the surface's own turn-writer / post-answer
        callback (was ``post_turn``).
      - ``is_noop`` — True marks this scope as an ALIAS for ``scope is None``:
        every seam ``answer()`` guards on is empty/falsy by construction, so
        the current main path runs unchanged.
    """

    system_addendum: str = ""
    context_block: str = ""
    tools: list = field(default_factory=list)
    post_answer_hook: Optional[Callable] = None
    is_noop: bool = False


@dataclass(frozen=True)
class AssembleRequest:
    """What an assembler is handed to build a `ContextScope`: the ask's identity
    and question, plus a free-form ``params`` bag for kind-specific inputs (for
    example a ``project_id``) taken from ``context_source["params"]``."""

    user_id: Optional[str]
    company_id: Optional[str]
    dataset: Optional[str]
    conversation_id: Optional[int]
    question: str
    params: dict = field(default_factory=dict)


@runtime_checkable
class ContextAssembler(Protocol):
    """A surface's context builder.

    Contract: ``assemble`` MUST enforce its OWN authorization gate and RAISE on
    an unauthorized request. The seam does not auth-check on an assembler's
    behalf — it only routes ``context_source["kind"]`` to the registered
    assembler and returns whatever it produces.
    """

    def assemble(self, req: AssembleRequest) -> ContextScope:
        ...


#: Empty in Phase 1 — no assembler is registered, so `resolve_context_scope`
#: returns None for every ask and behaviour is byte-identical to today's main
#: path. Phase 2 registers a surface's assembler under its `kind` key.
ASSEMBLER_REGISTRY: dict[str, ContextAssembler] = {}


def resolve_context_scope(
    context_source: Optional[dict], req: AssembleRequest
) -> Optional[ContextScope]:
    """Route ``context_source["kind"]`` to its registered assembler and return
    the `ContextScope` it builds.

    Returns ``None`` when there is no source, no ``kind``, or no registered
    assembler for that kind — which is EVERY ask in Phase 1, because
    `ASSEMBLER_REGISTRY` is empty. When a scope IS returned, its assembler has
    already run its own auth gate (raising on failure); the seam only routes.
    """
    if not context_source:
        return None
    kind = context_source.get("kind")
    if not kind:
        return None
    assembler = ASSEMBLER_REGISTRY.get(kind)
    if assembler is None:
        return None
    return assembler.assemble(req)
