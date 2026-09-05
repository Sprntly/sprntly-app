"""Every answer prompt carries the same per-company facts.

WHY THIS FILE EXISTS. `open_goal_gate_line` was added to three `ASK_SYSTEM`
assemblies and there are seven. The three that were missed included every one
`qa_agent.answer` builds — the ladder main chat actually answers through — so a
run waiting for approval was announced to the paths nobody used, and the answer
that invented a four-item plan never saw the line telling it not to.

The bug was not that somebody missed a grep. It was that the composition was
open-coded seven times, so nothing named the set and nothing could be complete.
`ask_system_suffix` names it; this is what stops the next fact being added to
six of seven places.
"""
from __future__ import annotations

import re

import pytest

from app import prompts

FILES = ("app/ask_runner.py", "app/qa_agent.py")
#: `system = (ASK_SYSTEM`, `system=ASK_SYSTEM`, or `ASK_SYSTEM` opening a
#: parenthesised concatenation on its own line.
_ASSEMBLY = re.compile(
    r"(system\s*=\s*\(?\s*ASK_SYSTEM|system=\(?\s*ASK_SYSTEM|^\s+ASK_SYSTEM$)",
    re.M,
)


def _statement(src: str, at: int) -> str:
    """The whole assembly expression, not a fixed slice of it.

    A character window is the wrong tool here and this file proved it: the
    PRD-tab assembly runs past 900 characters of conditional addenda before it
    reaches its suffix, so a windowed check reported it as missing when it was
    not. Reading to the end of the parenthesised expression is exact.
    """
    depth = 0
    for i in range(at, len(src)):
        ch = src[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return src[at:i + 1]
        elif ch == "\n" and depth == 0 and i > at:
            return src[at:i]
    return src[at:]


def _assemblies() -> list[tuple[str, int, str]]:
    out = []
    for path in FILES:
        src = open(path).read()
        for m in _ASSEMBLY.finditer(src):
            line = src[:m.start()].count("\n") + 1
            out.append((path, line, _statement(src, m.start())))
    return out


def test_there_are_still_seven_of_them():
    """Not a magic number — a tripwire. A new assembly is exactly the event
    that needs a human to check it carries the suffix, and the count is the
    cheapest way to notice one appeared."""
    assert len(_assemblies()) == 7


def test_every_assembly_uses_the_suffix():
    missing = [f"{p}:{n}" for p, n, w in _assemblies()
               if "ask_system_suffix" not in w]
    assert missing == []


@pytest.mark.parametrize("helper", [
    "today_line", "connected_sources_line", "open_goal_gate_line",
])
def test_no_assembly_reaches_past_the_suffix_for_a_fact(helper):
    """THE DECISIVE CHECK. Counting sites can be fooled by a window that is too
    short; this cannot. If neither file calls the parts directly, every one of
    them is reached through the suffix and nowhere else."""
    for path in FILES:
        src = open(path).read()
        assert f"{helper}(" not in src, f"{path} still calls {helper} directly"


def test_the_suffix_carries_all_three_in_the_order_the_sites_had():
    """Order is preserved because these append to a cached prompt PREFIX, and
    reordering them would cost every warm cache for nothing."""
    src = open("app/prompts.py").read()
    start = src.index("def ask_system_suffix(")
    body = src[start:src.index("\ndef ", start + 1)]
    # THE RETURN, NOT THE WHOLE FUNCTION. The docstring explains the bug that
    # produced this helper and names `open_goal_gate_line` in its first
    # sentence, so scanning the body read the order out of the prose rather
    # than out of the code.
    expression = body[body.index("return ("):]
    positions = [expression.index(name) for name in
                 ("today_line", "connected_sources_line", "open_goal_gate_line")]
    assert positions == sorted(positions)


def test_a_company_we_cannot_identify_gets_no_facts_at_all():
    """Saying nothing is the only safe answer: emitting "nothing is connected"
    for a company whose connections we simply failed to look up would assert a
    falsehood with the authority of a system prompt."""
    assert prompts.connected_sources_line(None) == ""
    assert prompts.open_goal_gate_line(None) == ""
