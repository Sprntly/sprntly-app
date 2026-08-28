"""PRDs the pipeline wrote unbidden, and nobody has read, stay out of the way.

WHAT THIS IS NOT KEYED ON, AND WHY. The first cut of this filtered on
`prds.source in ('brief','backlog')`, reasoning that `brief` is the weekly
pipeline's output. It is — and it is ALSO what a user gets when they click
Generate on a Top Insights card, and what the multi-agent run writes, and what
the runner writes. Five call sites, one column default, no way to tell them
apart from the finished row. Filtering on it hid documents people had
deliberately created — the failure it was written to avoid — and the first
person to open the Artifacts screen found their own PRDs gone.

`source` records where a PRD's SUBJECT came from. It has never recorded who
set it going. Only the code that STARTS a generation knows that, so that is
the only place that can say so: `prds.auto_generated`, set by the brief's
full-regen fan-out and by nothing else.

The rule is AUTO-GENERATED **and** NEVER READ. Both halves matter: a document
someone asked for is theirs however long it sits unread, and one the pipeline
wrote that somebody went and read has earned its place.

Nothing is deleted and no existing row was stamped. Rows written before the
column read as user-initiated, deliberately — their origin genuinely is not
recorded, and showing a machine-written PRD is a smaller harm than hiding one
somebody wrote themselves.
"""
from __future__ import annotations

import json

from app.db import prds as prds_db


def _row(**over) -> dict:
    row = {
        "id": 1, "brief_id": 7, "insight_index": 0, "theme_id": None,
        "source": "brief", "auto_generated": True, "title": "Auto PRD",
        "status": "ready", "generated_at": "2026-08-25T00:00:00Z",
        "first_read_at": None,
    }
    row.update(over)
    return row


# ── the rule itself ─────────────────────────────────────────────────────────

def test_an_unread_auto_generated_prd_is_hidden():
    assert prds_db.is_hidden_from_library(_row()) is True


def test_reading_one_claims_it_into_the_library():
    """The pipeline's output earns its place by someone going to look at it."""
    assert prds_db.is_hidden_from_library(
        _row(first_read_at="2026-08-25T10:00:00Z")
    ) is False


def test_a_document_a_person_brought_is_never_hidden():
    """However long it sits unread. `chat`/`upload`/`ideation` are the three
    a person actually initiates — see the test below for the full set."""
    assert prds_db.is_hidden_from_library(
        _row(source="chat", auto_generated=False)
    ) is False


def test_a_brief_prd_is_auto_whoever_pressed_the_button():
    """Owner's call, 2026-08-25.

    `source='brief'` is written both by the fan-out and by a user clicking
    Generate on a Top Insights card, and the two are indistinguishable
    afterwards. The decision is that the distinction does not matter: the
    insight was the machine's suggestion either way, so the whole population
    is auto. That is also what covers every row already in the table without a
    backfill.
    """
    for source in ("brief", "backlog"):
        assert prds_db.is_hidden_from_library(
            _row(source=source, auto_generated=False)
        ) is True, source


def test_the_three_a_person_actually_initiates_are_kept():
    """They asked in words, brought the document, or picked the idea off their
    own list. None of those is the machine proposing work."""
    for source in ("chat", "upload", "ideation"):
        assert prds_db.is_hidden_from_library(
            _row(source=source, auto_generated=False)
        ) is False, source


def test_a_row_from_before_the_source_column_reads_as_auto():
    """`brief` is the column default, so a row predating it is a brief PRD —
    which is exactly the population being cleared out."""
    row = _row(auto_generated=False)
    row.pop("source")
    assert prds_db.is_hidden_from_library(row) is True


def test_the_explicit_marker_alone_is_enough():
    """Redundant with the source rule today, and kept: it is the one signal
    that survives if `source` is ever reused, and the fan-out is the only
    caller that knows for certain nobody asked."""
    assert prds_db.is_hidden_from_library(
        _row(source="chat", auto_generated=True)
    ) is True


# ── the listing, end to end ─────────────────────────────────────────────────
#
# `list_document_artifacts` is where the filter lives — the PRD/evidence half
# of the library, which `list_artifacts_for_company` (Artifacts screen) and
# `list_artifacts_for_project` (Projects) both build on. Filtering here is what
# makes one rule serve every surface instead of three that can drift.

def _seed_brief(dataset: str = "acme") -> int:
    from app.db.client import require_client

    return (
        require_client().table("briefs").insert({
            "dataset": dataset, "week_label": "Week of Aug 1",
            "payload": json.dumps({}), "is_current": True,
        }).execute().data[0]["id"]
    )


def _seed_prd(*, brief_id: int, title: str, auto_generated: bool = False,
              source: str = "brief", insight_index: int = 0,
              first_read_at: str | None = None) -> int:
    from app.db.client import require_client

    return (
        require_client().table("prds").insert({
            "brief_id": brief_id, "insight_index": insight_index,
            "title": title, "status": "ready", "source": source,
            "auto_generated": auto_generated,
            "theme_id": f"theme:{title}",
            "generated_at": "2026-08-25T00:00:00Z",
            "first_read_at": first_read_at,
        }).execute().data[0]["id"]
    )


def _seed_evidence(*, brief_id: int, title: str, insight_index: int = 0) -> int:
    from app.db.client import require_client

    return (
        require_client().table("evidences").insert({
            "brief_id": brief_id, "insight_index": insight_index,
            "title": title, "status": "ready",
            "generated_at": "2026-08-25T00:00:00Z",
        }).execute().data[0]["id"]
    )


def _listed(kind: str) -> set[str]:
    from app.db.artifacts import list_document_artifacts

    return {
        i["title"] for i in list_document_artifacts(dataset="acme")
        if i["type"] == kind
    }


def test_the_library_hides_the_fan_outs_output_and_keeps_everything_else(
    isolated_settings,
):
    brief_id = _seed_brief()
    _seed_prd(brief_id=brief_id, title="Fan-out PRD", auto_generated=True)
    # Same `source`, different origin — and both are auto by decision, which
    # is what makes every existing row covered without a backfill.
    _seed_prd(brief_id=brief_id, title="I clicked Generate", insight_index=1)
    _seed_prd(brief_id=brief_id, title="From chat", source="chat",
              insight_index=2)
    _seed_prd(brief_id=brief_id, title="Brief PRD I read", insight_index=3,
              first_read_at="2026-08-25T10:00:00Z")

    titles = _listed("prd")

    assert "From chat" in titles
    assert "Brief PRD I read" in titles
    assert "Fan-out PRD" not in titles
    assert "I clicked Generate" not in titles


def test_the_evidence_for_a_hidden_prd_goes_with_it(isolated_settings):
    """`evidences` carries no prd_id — it is keyed on the same (brief_id,
    insight_index) the PRD hangs off, and that pair is the only link between
    them. Hiding the PRD and leaving its evidence behind would be a finding
    half-hidden."""
    brief_id = _seed_brief()
    _seed_prd(brief_id=brief_id, title="Fan-out PRD", auto_generated=True)
    _seed_evidence(brief_id=brief_id, title="Evidence for the fan-out PRD")

    assert _listed("evidence") == set()


def test_evidence_for_a_prd_that_stays_is_kept(isolated_settings):
    """Mutation proof: the cascade follows the PRD, it does not hide evidence
    on its own."""
    brief_id = _seed_brief()
    _seed_prd(brief_id=brief_id, title="Mine", source="chat")
    _seed_evidence(brief_id=brief_id, title="Evidence for mine")

    assert "Evidence for mine" in _listed("evidence")


def test_evidence_survives_when_a_read_generation_keeps_the_finding(
    isolated_settings,
):
    """A finding whose PRD survives keeps its evidence even though an OLDER
    generation of that same PRD is hidden — the family is what the reader
    sees, not the row."""
    brief_id = _seed_brief()
    _seed_prd(brief_id=brief_id, title="Fan-out PRD", auto_generated=True)
    _seed_prd(brief_id=brief_id, title="Fan-out PRD", auto_generated=True,
              first_read_at="2026-08-25T10:00:00Z")
    _seed_evidence(brief_id=brief_id, title="Its evidence")

    assert "Its evidence" in _listed("evidence")


# ── hidden from the shelf, still on it ──────────────────────────────────────

def test_a_hidden_prd_is_still_resolvable_by_name(isolated_settings):
    """RESOLVING one is not LISTING the library.

    `app.artifact_open` answers "open the checkout PRD" from this same
    function. If the hide applied there too, a hidden PRD would be genuinely
    unreachable — and since OPENING one is what claims it back into the
    library, it could never stop being hidden either.
    """
    from app.db.artifacts import list_document_artifacts

    brief_id = _seed_brief()
    _seed_prd(brief_id=brief_id, title="Checkout PRD", auto_generated=True)

    assert "Checkout PRD" not in _listed("prd")

    openable = {
        i["title"] for i in list_document_artifacts(
            dataset="acme", openable_only=True
        ) if i["type"] == "prd"
    }
    assert "Checkout PRD" in openable


# ── the read stamp ──────────────────────────────────────────────────────────

def test_opening_a_prd_stamps_it_once(isolated_settings):
    """Advance-only: the first open records the moment, a second cannot move
    it — this measures whether a document was ever engaged with, not when it
    was last touched."""
    brief_id = _seed_brief()
    prd_id = _seed_prd(brief_id=brief_id, title="Fan-out PRD", auto_generated=True)

    assert prds_db.get_prd(prd_id).get("first_read_at") in (None, "")

    prds_db.mark_prd_read(prd_id)
    first = prds_db.get_prd(prd_id).get("first_read_at")
    assert first

    prds_db.mark_prd_read(prd_id)
    assert prds_db.get_prd(prd_id).get("first_read_at") == first


def test_reading_a_prd_returns_it_to_the_library(isolated_settings):
    """The whole point of the rule being derived rather than stamped: it is
    reversed by the user simply looking at the document."""
    brief_id = _seed_brief()
    prd_id = _seed_prd(brief_id=brief_id, title="Fan-out PRD", auto_generated=True)

    assert "Fan-out PRD" not in _listed("prd")
    prds_db.mark_prd_read(prd_id)
    assert "Fan-out PRD" in _listed("prd")


def test_the_fan_out_marks_what_it_writes(isolated_settings):
    """The marker only means anything if the one auto path actually sets it.
    Pinned against the source, because a fan-out that stopped marking would
    silently disable the whole feature with every test still green."""
    import inspect

    from app.routes import brief as brief_route

    src = inspect.getsource(brief_route)
    assert "auto_generated=True" in src, (
        "the brief full-regen fan-out no longer marks its PRDs as "
        "auto-generated — nothing else does, so the library filter is now a "
        "no-op."
    )
