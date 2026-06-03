"""P5-05 — Background-task strong-ref discipline: verify + lock.

asyncio holds only a *weak* reference to the result of ``asyncio.create_task``.
A long-running Design Agent generation/iteration task that is held by nothing
but a bare local can therefore be garbage-collected mid-run, silently killing
the generation (the failure mode BUILD-PHASES §Phase 5 names). The discipline
that prevents this is, at every ``create_task`` site:

    _inflight_tasks.add(task)
    task.add_done_callback(_inflight_tasks.discard)

i.e. a module-level ``set`` holds a strong ref for the task's whole lifetime and
the done-callback discards it on completion.

This module is the GUARDRAIL. It does two load-bearing things:

1. INVENTORY (AC1/AC3/AC4): an ``ast``-based scan of the design-agent backend
   package + the design-agent routes module enumerates every function that
   *calls* ``asyncio.create_task`` (docstring/comment mentions are NOT calls and
   are excluded by parsing the AST rather than grepping text) and asserts each
   such function also registers the task in ``_inflight_tasks`` and discards it
   via ``add_done_callback(_inflight_tasks.discard)`` in the same function body.
   A FUTURE ticket that adds a bare ``create_task`` without the discipline trips
   this test at CI time.

2. GC-PRESSURE REGRESSION (AC2): a background task held only by a strong-ref set
   following the discipline survives a forced ``gc.collect()`` mid-flight and
   completes cleanly.

At HEAD all 5 real sites are compliant (verified 2026-06-02): ``generate``,
``post_iterate``, ``post_confirm_plan``, ``post_manual_edit`` in
``app/routes/design_agent.py`` and ``drain_iteration_queue`` in
``app/design_agent/runner.py`` (the queue-chain site, which reuses the routes'
``_inflight_tasks`` set via a deferred import). No source change ships from this
ticket — it is test-only (per the 2026-06-02 resize from "build inflight.py").
"""

import ast
import asyncio
import gc
import pathlib

# ---------------------------------------------------------------------------
# Scope: the design-agent backend package + the design-agent routes module.
# Resolved from this test file's location so the scan is drift-immune to line
# numbers (P5-02 shifted every literal line ref the ticket cited).
# ---------------------------------------------------------------------------
_BACKEND_APP = pathlib.Path(__file__).resolve().parent.parent / "app"
_DESIGN_AGENT_PKG = _BACKEND_APP / "design_agent"
_DESIGN_AGENT_ROUTES = _BACKEND_APP / "routes" / "design_agent.py"

# The known compliant call-sites at HEAD. The inventory does NOT assert this is
# the *exact* set (a future compliant create_task site must be allowed to pass);
# it asserts these are present as a sanity floor that the scanner is actually
# finding the real sites, and that EVERY site found is compliant.
_KNOWN_SITES = {
    "generate",
    "post_iterate",
    "post_confirm_plan",
    "post_manual_edit",
    "drain_iteration_queue",
}


def _source_files_in_scope() -> list[pathlib.Path]:
    """Every ``.py`` in the design-agent package + the design-agent routes file.

    Scanning the whole package (not just the files that happen to have a site
    today) is what makes the guardrail catch a new ``create_task`` added to any
    design-agent backend module tomorrow.
    """
    files = sorted(_DESIGN_AGENT_PKG.glob("*.py"))
    files.append(_DESIGN_AGENT_ROUTES)
    return files


class _CreateTaskScanner(ast.NodeVisitor):
    """Attribute each ``create_task`` / ``_inflight_tasks`` usage to its
    *innermost* enclosing function, so co-location of the discipline is checked
    per-function and a nested helper cannot borrow its parent's compliance."""

    def __init__(self) -> None:
        self.functions: list[dict] = []
        self._stack: list[dict] = []

    def _visit_func(self, node: ast.AST) -> None:
        record = {
            "name": node.name,
            "lineno": node.lineno,
            "has_create_task": False,
            "has_inflight_add": False,
            "has_discard_callback": False,
        }
        self.functions.append(record)
        self._stack.append(record)
        self.generic_visit(node)
        self._stack.pop()

    visit_FunctionDef = _visit_func
    visit_AsyncFunctionDef = _visit_func

    def visit_Call(self, node: ast.Call) -> None:
        record = self._stack[-1] if self._stack else None
        if record is not None and isinstance(node.func, ast.Attribute):
            func = node.func
            # asyncio.create_task(...)
            if (
                func.attr == "create_task"
                and isinstance(func.value, ast.Name)
                and func.value.id == "asyncio"
            ):
                record["has_create_task"] = True
            # _inflight_tasks.add(...)
            if (
                func.attr == "add"
                and isinstance(func.value, ast.Name)
                and func.value.id == "_inflight_tasks"
            ):
                record["has_inflight_add"] = True
            # <task>.add_done_callback(_inflight_tasks.discard)
            if func.attr == "add_done_callback":
                for arg in node.args:
                    if (
                        isinstance(arg, ast.Attribute)
                        and arg.attr == "discard"
                        and isinstance(arg.value, ast.Name)
                        and arg.value.id == "_inflight_tasks"
                    ):
                        record["has_discard_callback"] = True
        self.generic_visit(node)


def _scan_create_task_functions(source: str) -> list[dict]:
    """Parse ``source`` and return one record per function that *calls*
    ``asyncio.create_task``. Docstring/comment mentions are not Call nodes, so
    they never appear here — this is the AST advantage over naive grep
    (``runner.py``'s docstring mentions ``asyncio.create_task`` three times)."""
    scanner = _CreateTaskScanner()
    scanner.visit(ast.parse(source))
    return [f for f in scanner.functions if f["has_create_task"]]


def _is_compliant(record: dict) -> bool:
    return record["has_inflight_add"] and record["has_discard_callback"]


# ---------------------------------------------------------------------------
# AC1 — inventory: every real create_task site registers the strong-ref.
# ---------------------------------------------------------------------------
def test_all_create_task_sites_register_strong_ref():
    found: dict[str, dict] = {}
    noncompliant: list[str] = []
    for path in _source_files_in_scope():
        for record in _scan_create_task_functions(path.read_text()):
            key = f"{path.name}::{record['name']}"
            found[key] = record
            if not _is_compliant(record):
                missing = []
                if not record["has_inflight_add"]:
                    missing.append("_inflight_tasks.add(task)")
                if not record["has_discard_callback"]:
                    missing.append("task.add_done_callback(_inflight_tasks.discard)")
                noncompliant.append(f"{key} (line {record['lineno']}) missing: {', '.join(missing)}")

    # Sanity floor: the scanner actually located the real sites. If this fails,
    # the source moved out of scope or the scanner regressed — not a pass.
    assert found, "no asyncio.create_task call-sites found in the design-agent scope"
    found_names = {key.split("::", 1)[1] for key in found}
    assert _KNOWN_SITES <= found_names, (
        f"expected known create_task sites missing from scan: {_KNOWN_SITES - found_names}"
    )

    # The guardrail: every create_task site is accompanied by the discipline.
    assert not noncompliant, (
        "asyncio.create_task site(s) without the _inflight_tasks strong-ref discipline "
        "(add() + add_done_callback(_inflight_tasks.discard)):\n  " + "\n  ".join(noncompliant)
    )


# ---------------------------------------------------------------------------
# AC3 — the inventory helper actually flags a non-compliant site (proves the
# guardrail would catch a future regression). Exercised on inline fixtures, not
# the real files.
# ---------------------------------------------------------------------------
def test_inventory_detects_noncompliant_snippet():
    bad = (
        "import asyncio\n"
        "async def spawn():\n"
        "    asyncio.create_task(work())\n"  # bare — no strong ref
    )
    records = _scan_create_task_functions(bad)
    assert len(records) == 1
    assert records[0]["name"] == "spawn"
    assert not _is_compliant(records[0]), "bare create_task should be flagged non-compliant"


def test_inventory_accepts_compliant_snippet():
    good = (
        "import asyncio\n"
        "_inflight_tasks = set()\n"
        "async def spawn():\n"
        "    task = asyncio.create_task(work())\n"
        "    _inflight_tasks.add(task)\n"
        "    task.add_done_callback(_inflight_tasks.discard)\n"
    )
    records = _scan_create_task_functions(good)
    assert len(records) == 1
    assert _is_compliant(records[0]), "compliant site should pass the inventory"


def test_inventory_excludes_docstring_mentions():
    """A function whose DOCSTRING mentions asyncio.create_task but never calls it
    must not be counted (the runner.py:drain_iteration_queue docstring case)."""
    docstring_only = (
        "import asyncio\n"
        "async def documented():\n"
        '    """Chains the next row via asyncio.create_task until empty."""\n'
        "    return None\n"
    )
    assert _scan_create_task_functions(docstring_only) == []


# ---------------------------------------------------------------------------
# AC2 — GC-pressure regression: a task held only by the strong-ref set survives
# a forced gc.collect() mid-flight and completes cleanly.
# ---------------------------------------------------------------------------
async def test_background_task_survives_gc_collect():
    started = asyncio.Event()
    completed = asyncio.Event()
    strong_refs: set[asyncio.Task] = set()

    async def _slow():
        started.set()
        await asyncio.sleep(0.05)
        completed.set()
        return "done"

    def _spawn() -> None:
        # The discipline, in a scope that returns None: after _spawn() returns,
        # the ONLY strong reference to the task is `strong_refs`. The local
        # `task` here goes out of scope, reproducing the real-world shape where
        # the route handler returns immediately and the set is all that's left.
        task = asyncio.create_task(_slow())
        strong_refs.add(task)
        task.add_done_callback(strong_refs.discard)

    _spawn()
    await started.wait()  # task is genuinely mid-flight (not done) before we collect

    # Force a collection cycle while the task is running. Without the strong ref
    # in `strong_refs`, asyncio's weak bookkeeping could let this be collected.
    gc.collect()

    assert len(strong_refs) == 1, "strong-ref set lost the task across gc.collect()"
    (task,) = tuple(strong_refs)
    assert not task.done(), "task was collected/cancelled mid-flight despite the strong ref"

    result = await task
    assert result == "done"
    assert completed.is_set()
    # The done-callback discarded the task from the set — no leak.
    assert len(strong_refs) == 0, "done-callback did not discard the completed task"


async def test_bare_task_is_at_risk_documented():
    """Negative control (documentary, NOT load-bearing).

    A task created WITHOUT a strong ref is the failure mode this discipline
    guards against. We do NOT assert it gets collected: CPython may keep it
    alive via the running event loop's internal bookkeeping, so asserting
    collection would be flaky (the ticket calls this out explicitly). We only
    assert the discipline-free task still *exists as a Task object*; the
    load-bearing guarantee lives in test_background_task_survives_gc_collect.
    """
    async def _noop():
        await asyncio.sleep(0)
        return "ok"

    task = asyncio.create_task(_noop())  # no set, no done-callback
    gc.collect()
    # Whatever GC did, we can still observe + await our local handle here.
    result = await task
    assert result == "ok"


# ---------------------------------------------------------------------------
# AC5 — the resize decision: no new inflight.py module was created.
# ---------------------------------------------------------------------------
def test_no_inflight_module_created():
    assert not (_DESIGN_AGENT_PKG / "inflight.py").exists(), (
        "P5-05 was resized to verify-the-existing-discipline; no new "
        "design_agent/inflight.py module should be created (it would be dead code)."
    )
