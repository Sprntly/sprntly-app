"""Pluggable context-assembler seam for the ask path.

This module defines the seam by which ANY surface can bring its own context
assembler to `qa_agent.answer()`. An ask with NO `context_source` on the wire
short-circuits to `None` in `resolve_context_scope()` BEFORE the registry is
consulted, so every main-chat ask runs the exact current (unscoped) main path,
byte-identical. Only an ask that carries a `context_source` whose `kind` has a
registered assembler gets a scope.

`ASSEMBLER_REGISTRY` is populated at import time by
`_register_builtin_assemblers()` (bottom of this module). The one built-in is
`"project" → ProjectContextAssembler` (the private + @Sprntly-group project
chats), which returns a `SurfaceScope` — the type `answer(scope=...)` already
consumes — populated with the project's breadth block.

`ContextScope` (below) is the seam's own descriptor, kept from the scaffolding
pass; the first assembler returns a `SurfaceScope` directly to avoid churning
`answer()`'s internals. FOLLOW-UP: unify the two into one descriptor.

The seam itself does no I/O and imports no surface/`qa_agent` module at module
top; `_register_builtin_assemblers()` uses a DEFERRED import, so the dependency
stays one-directional and no import cycle can form.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Optional, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover - typing only, no runtime import (cycle-safe)
    from app.surface_scope import SurfaceScope


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
    #: The caller's workspace, threaded so an assembler's membership/tenant gate
    #: can scope to the full `(company_id, workspace_id)` pair (the project
    #: membership gate 404s a foreign-workspace project id).
    workspace_id: Optional[str] = None
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


#: Keyed by `context_source["kind"]`. The `"project"` assembler is registered
#: at import time by `_register_builtin_assemblers()` at the bottom of this
#: module. An ask with NO `context_source` never consults this registry
#: (`resolve_context_scope` returns None first), so the main-chat path stays
#: byte-identical.
ASSEMBLER_REGISTRY: dict[str, ContextAssembler] = {}


def resolve_context_scope(
    context_source: Optional[dict], req: AssembleRequest
) -> "Optional[SurfaceScope]":
    """Route ``context_source["kind"]`` to its registered assembler and return
    the scope it builds.

    Returns ``None`` when there is no source, no ``kind``, or no registered
    assembler for that kind — which is EVERY main-chat ask (no ``context_
    source`` on the wire ⇒ ``None`` ⇒ the byte-identical unscoped path). When a
    scope IS returned, its assembler has already run its own auth gate (raising
    on failure); the seam only routes.

    Return type widened from ``Optional[ContextScope]`` to
    ``Optional[SurfaceScope]``: ``answer(scope=...)`` consumes ``SurfaceScope``,
    and the first registered assembler (``ProjectContextAssembler``) returns one
    directly to avoid churning ``answer()``'s internals this phase. FOLLOW-UP:
    unify ``ContextScope`` and ``SurfaceScope`` into one descriptor.
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


def _register_builtin_assemblers() -> None:
    """Populate `ASSEMBLER_REGISTRY` with the in-repo assemblers, once, at
    import time. The deferred import lives HERE (not at module top) so the
    dependency is one-directional — `context_assembler_project` imports names
    from THIS module, and this module never needs it at definition time — which
    keeps the seam free of the import cycle its scaffolding docstring guards
    against. Failure is swallowed: a broken optional assembler must never take
    down the whole ask path (the affected `kind` simply routes to no assembler
    and `resolve_context_scope` returns None, i.e. the main path)."""
    try:
        from app.context_assembler_project import ProjectContextAssembler
    except Exception:  # noqa: BLE001 — never break import of the ask path
        return
    ASSEMBLER_REGISTRY.setdefault("project", ProjectContextAssembler())


_register_builtin_assemblers()
