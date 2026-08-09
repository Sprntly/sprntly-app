"""`must_reach` — make "did this test actually run the code it is named for?"
a one-line assertion instead of a review question.

THE DEFECT CLASS THIS SERVES (2026-08-07, four instances in one batch). A test
whose FIXTURE does not contain the thing its NAME claims. It passes, forever,
without ever exercising the combination it exists to cover:

  - `test_a_dead_connection_falls_back_to_stored_even_with_a_selection` set
    `rows = []`, so there was no connection and therefore no selection. "Even
    with a selection" was never true of the fixture.
  - `test_a_document_opening_on_an_unrecognised_tag_is_made_to_sniff` passed
    against PRE-FIX code, because CSS injection already prepended `<style>` for
    that input. The `_SNIFF_RE` fallback it named had ZERO coverage.
  - 6 of 10 of one PR's false-positive tests passed via a content-term floor,
    not the cue gate they were named for.
  - A `has_anaphor` rule had zero coverage: every negative case also lacked a
    document cue, so the earlier guard returned first and the anaphor
    precondition was never reached.

WHY THERE IS NO GENERAL AUTOMATED CHECK FOR THIS, and why this is a helper
rather than a scanner. The relationship between a test's NAME and its FIXTURE is
semantic. Any heuristic over ~6,600 test names — keyword matching, embedding
similarity, "the name says X so assert X appears" — would produce far more false
positives than findings, and a guard that cries wolf is deleted, which leaves
the class less covered than before. Two things genuinely find this class:
mutation testing (which found two of the four above) and a human reading the
fixture. Neither is a cheap per-PR check.

What IS cheap is making the claim CHECKABLE once someone thinks to check it. If
a test is named for a branch, one line can pin that the branch was entered:

    from tests._must_reach import must_reach

    def test_an_unrecognised_tag_falls_back_to_sniffing():
        with must_reach(html_render, "_sniff_document_type"):
            html_render.render(payload)

Now the test fails if the fixture stops reaching the fallback — including if a
future change makes an EARLIER branch handle the input, which is exactly how the
`_SNIFF_RE` case went uncovered without anybody noticing.

This module is opt-in: it flags nothing on its own. It is the mechanical half of
the review practice; the other half is still reading the fixture.

IT *CAN* PRODUCE A FALSE POSITIVE, and an earlier draft of this docstring
claimed otherwise. `must_reach` swaps the attribute on the MODULE OBJECT, so it
only observes callers that resolve the name through the module at call time
(`mod.fn(...)`). A caller that bound the function at import time —
`from mod import fn` at the top of the calling module, then a bare `fn(...)` —
holds a direct reference this never replaces, so the function genuinely runs and
this still reports "never reached". Practical risk is low (it is opt-in, and the
failure is loud and immediately obvious to whoever wrote the assertion), but
"cannot produce a false positive" was simply wrong.

Workaround when you hit it: patch the name in the CALLING module's namespace
(`must_reach(caller_module, "fn")`) rather than in the module that defines it.

WHAT IT DOES NOT COVER AT ALL: branch granularity. It proves a function was
ENTERED, never that the interesting branch inside it ran. One of this batch's
four instances — a `has_anaphor` rule with zero coverage — would NOT be caught,
because an earlier guard returned first inside the same call path.
"""
from __future__ import annotations

import contextlib
from typing import Any, Iterator


@contextlib.contextmanager
def must_reach(module: Any, name: str, *, at_least: int = 1) -> Iterator[list]:
    """Fail unless `module.name` is called at least `at_least` times in the block.

    Yields the list of `(args, kwargs)` each call was made with — so a test can
    also assert on the ARGUMENTS, which is the stronger form. (Asserting an
    effect did not happen only tells you about the fixture; asserting what the
    call was made WITH tells you about the code. That distinction is why
    `auto_join=True` survived a passing Slack read-path suite.)

    The original callable is invoked, so behaviour is unchanged — this observes,
    it does not stub.
    """
    original = getattr(module, name)
    calls: list[tuple[tuple, dict]] = []

    def _observed(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    setattr(module, name, _observed)
    try:
        yield calls
    finally:
        setattr(module, name, original)

    module_name = getattr(module, "__name__", repr(module))
    assert len(calls) >= at_least, (
        f"this test never reached {module_name}.{name} "
        f"(called {len(calls)}x, needed {at_least}). Whatever it asserts, it is "
        "not asserting anything about that code — the fixture short-circuits "
        "before it. Fix the fixture, or rename the test to say what it really "
        "covers."
    )
