"""The Goal Analysis report as an editable document.

The design this pins is one sentence: **the run is immutable, and the report is
a document ABOUT the run.** Everything here is that sentence being enforced, so
each test names the failure it would catch rather than the code path it walks.

  1. IMMUTABILITY. Editing the report must not touch `crucible_findings`. If it
     did, the product's central claim — that a run is reproducible, that every
     finding traces to claim ids and source documents — would be false for any
     run someone had tidied the prose of, and nothing on screen would say so.
  2. NO SILENT RE-RENDER. Opening the report is what calls the create endpoint,
     so a create that re-rendered would mean reopening your own edited report is
     the thing that destroys it.
  3. THE DETACH MARKER. A report that has been edited must be distinguishable
     from one that has not, from the API, without reading the prose.
  4. TENANCY. A foreign run id is 404 and never 403 — a 403 is itself a
     disclosure.
  5. THE CHAT EDIT CANNOT PICK ITS OWN TARGET. The model gets an instruction and
     no id, and the writer refuses anything that is not a report on one of this
     company's runs.
"""
from __future__ import annotations

import uuid

import pytest

from tests import _fake_supabase
from tests._company_helpers import company_client, seed_company, supabase_bearer
from tests.test_routes_crucible import _DDL


def _enable(company_id: str, on: bool = True) -> None:
    from app.db.client import require_client

    require_client().table("companies").update(
        {"feature_flags": {"crucible": on}}
    ).eq("id", company_id).execute()


@pytest.fixture
def crucible_env(isolated_settings):
    _fake_supabase.get_fake_db().executescript(_DDL)
    yield


@pytest.fixture
def ctx(crucible_env, monkeypatch):
    c = company_client(monkeypatch)
    _enable(c.company_id)
    return c


_PLAN = {
    "goal_text": "raise renewal rate",
    "definition_text": "renewals closed in the quarter, net of downgrades",
    "currency": "accounts",
    "total_signals": 1200,
    "sources": [
        {"source_type": "call_transcripts", "signal_count": 900,
         "label": "Customer calls", "witnesses": "what customers say happened"},
        {"source_type": "support", "signal_count": 300,
         "label": "Support tickets", "witnesses": "what broke, and for whom"},
    ],
    "cannot_answer": [
        {"question": "How much revenue would fixing this recover?",
         "because": "nothing connected carries account revenue",
         "remedy": "connect the billing system"},
    ],
    "will_produce": ["a ranked reading"],
    "excluded_sources": ["project_mgmt"],
    "hypotheses": ["onboarding is the problem"],
}

#: One SIZED finding and one UNSIZED one, because the rule that matters most —
#: an unsized finding is not a zero — is only testable when both are present.
_FINDINGS = [
    {
        "statement": "Renewal conversations stall on the parts request flow",
        "claim_ids": ["c1", "c2"],
        "adjudication": "conflict",
        "impact_value": 14,
        "currency": "accounts",
        "confidence_band": "medium",
        "surfaced_by": ["calls/renewals-q3", "support/parts"],
        "assumed_params": [{"name": "seat count", "basis": "median of the cohort"}],
        "impact": {"value": 14, "affected_population": 14},
        "confidence": {"band": "medium", "weakest_leg": "recency",
                       "weakest_leg_reason": "most of this is from one quarter",
                       "cap_reason": "capped: no authoritative source"},
        "tier": "deep",
    },
    {
        "statement": "Admins cannot delegate seat management",
        "claim_ids": ["c3"],
        "adjudication": "corroborated",
        "impact_value": None,          # NOT ZERO. The whole of I3.
        "currency": "accounts",
        "confidence_band": "low",
        "surfaced_by": ["calls/admin-feedback"],
        "assumed_params": [],
        "impact": {"value": None, "affected_population": None},
        "confidence": {"band": "low", "weakest_leg": "volume",
                       "weakest_leg_reason": "three accounts said this",
                       "cap_reason": None},
        "tier": "shallow",
    },
]

_LEDGER = [
    {"label": "Mobile parity", "reason": "no claim survived the echo check",
     "stopped_at_stage": "verification", "claim_ids": ["c9"]},
]


def _ready_run(ctx, goal: str = "raise renewal rate") -> int:
    """A finished run, written straight to storage.

    THE PIPELINE IS NOT UNDER TEST HERE. Driving a real run needs a seeded
    knowledge graph and exercises clustering, scoring and the causal lint —
    every one of which has its own suite, and none of which this surface can
    break. What this surface can break is what happens to a finished run's
    prose, so the run is manufactured and the prose is the subject.
    """
    from app.db import crucible_runs as runs_db

    run_id = ctx.client.post("/v1/crucible", json={"goal_text": goal}).json()["id"]
    runs_db.update(
        run_id, ctx.company_id,
        status="ready",
        prioritisation={"plan": _PLAN},
        coverage_notes=[{"reason": "undated evidence",
                         "actual": "40 of 1200 signals carried no usable date"}],
        claim_count=1200,
    )
    runs_db.save_findings(run_id, ctx.company_id, list(_FINDINGS), list(_LEDGER))
    return run_id


def _findings_snapshot(run_id: int, company_id: str):
    from app.db import crucible_runs as runs_db

    return runs_db.load_findings(run_id, company_id)


# ─── 1. Creating the document ───────────────────────────────────────────────

def test_the_report_renders_what_the_panel_renders(ctx):
    """The document is the run, in prose. Every rule the panel keeps has to
    survive the trip: an unsized finding says so, the sources are beside the
    claim they support, the coverage notes are there, the ledger is there with
    its reasons, and the limits section is built from the plan's own gaps."""
    run_id = _ready_run(ctx)
    body = ctx.client.post(f"/v1/crucible/{run_id}/document").json()
    html = body["body_html"]

    assert "raise renewal rate" in html
    assert "renewals closed in the quarter" in html      # the confirmed definition
    assert "Could not be sized" in html                  # I3, the unsized finding
    assert "calls/renewals-q3" in html                   # provenance, per finding
    assert "40 of 1200 signals" in html                  # coverage, above findings
    assert "Mobile parity" in html                       # the ruled-out ledger
    assert "no claim survived the echo check" in html
    assert "connect the billing system" in html          # the plan's own gaps
    assert "onboarding is the problem" in html           # the user's hypotheses
    assert "project mgmt" in html                        # the source they dropped


def test_an_unsized_finding_is_never_rendered_as_zero(ctx):
    """I3, stated as its own case because it is the one that changes a
    decision. A theme nobody could size and a theme measured at zero lead to
    opposite actions, and a renderer that prints 0 for the first asserts the
    second."""
    run_id = _ready_run(ctx)
    html = ctx.client.post(f"/v1/crucible/{run_id}/document").json()["body_html"]
    unsized = "Admins cannot delegate seat management"
    assert unsized in html
    # The unsized finding's own paragraph must not carry a count.
    section = html.split(unsized, 1)[1][:400]
    assert "Could not be sized" in section
    assert "0 account" not in section


def test_creating_the_document_is_idempotent(ctx):
    """The panel calls this to OPEN the report, so a second call has to return
    the first document. A create that made a second one would leave a copy in
    the shared library on every open."""
    run_id = _ready_run(ctx)
    first = ctx.client.post(f"/v1/crucible/{run_id}/document").json()
    second = ctx.client.post(f"/v1/crucible/{run_id}/document").json()
    assert first["id"] == second["id"]

    from app.db.custom_artifacts import list_artifacts_for_company

    reports = [
        a for a in list_artifacts_for_company(ctx.company_id)
        if a["kind"] == "goal_analysis"
    ]
    assert len(reports) == 1


def test_a_run_that_has_not_finished_has_nothing_to_report(ctx):
    """A report of a run that produced nothing yet would be a document making
    claims about an analysis that has not happened."""
    run_id = ctx.client.post(
        "/v1/crucible", json={"goal_text": "too early"}
    ).json()["id"]
    assert ctx.client.post(f"/v1/crucible/{run_id}/document").status_code == 409


def test_the_run_row_carries_the_link_so_the_panel_can_find_it(ctx):
    run_id = _ready_run(ctx)
    assert ctx.client.get(f"/v1/crucible/{run_id}").json()["artifact_id"] is None
    doc = ctx.client.post(f"/v1/crucible/{run_id}/document").json()
    assert ctx.client.get(f"/v1/crucible/{run_id}").json()["artifact_id"] == doc["id"]


# ─── 2. Detachment — the heart of it ────────────────────────────────────────

def test_a_freshly_rendered_report_is_not_detached(ctx):
    """Nothing has happened to it, so the banner must not fire. A notice that
    appears before there is anything to notice is one people learn to skip."""
    run_id = _ready_run(ctx)
    assert ctx.client.post(f"/v1/crucible/{run_id}/document").json()["detached"] is False
    assert ctx.client.get(f"/v1/crucible/{run_id}/document").json()["detached"] is False


def test_a_hand_edit_through_the_ordinary_document_route_detaches_it(ctx):
    """THE LOAD-BEARING ONE. The hand edit arrives through
    PATCH /v1/custom-artifacts/{id} — a route that knows nothing about Goal
    Analysis. Detachment is derived from a hash rather than set by a flag
    exactly so that route does not have to learn, and this is the test that
    says the derivation works."""
    run_id = _ready_run(ctx)
    doc = ctx.client.post(f"/v1/crucible/{run_id}/document").json()

    saved = ctx.client.patch(
        f"/v1/custom-artifacts/{doc['id']}",
        json={"body_html": "<h1>My own words</h1><p>Rewritten.</p>",
              "base_version": doc["version"]},
    )
    assert saved.status_code == 200

    after = ctx.client.get(f"/v1/crucible/{run_id}/document").json()
    assert after["detached"] is True
    assert "My own words" in after["body_html"]


def test_renaming_a_report_does_not_detach_it(ctx):
    """"Edited" means the BODY changed. A title someone tidied in the library
    has not diverged from the run, and marking it as though it had would make
    the marker mean nothing."""
    run_id = _ready_run(ctx)
    doc = ctx.client.post(f"/v1/crucible/{run_id}/document").json()
    ctx.client.patch(
        f"/v1/custom-artifacts/{doc['id']}", json={"title": "Q3 renewals read"}
    )
    assert ctx.client.get(f"/v1/crucible/{run_id}/document").json()["detached"] is False


def test_a_missing_fingerprint_reads_as_detached_not_as_clean(ctx):
    """A run linked before the hash column existed has nothing to compare. The
    two possible mistakes are not symmetric: a needless banner costs a glance,
    while calling an edited report untouched tells the reader their prose is
    the run's own output."""
    from app.db import crucible_runs as runs_db

    run_id = _ready_run(ctx)
    ctx.client.post(f"/v1/crucible/{run_id}/document")
    runs_db.update(run_id, ctx.company_id, report_body_hash=None)
    assert ctx.client.get(f"/v1/crucible/{run_id}/document").json()["detached"] is True


def test_reopening_an_edited_report_does_not_re_render_over_it(ctx):
    """"Re-running does not clobber an edited document", in the form the user
    actually meets it: the panel calls the create endpoint every time the
    report is opened."""
    run_id = _ready_run(ctx)
    doc = ctx.client.post(f"/v1/crucible/{run_id}/document").json()
    ctx.client.patch(
        f"/v1/custom-artifacts/{doc['id']}",
        json={"body_html": "<p>Only my sentence survives.</p>",
              "base_version": doc["version"]},
    )
    again = ctx.client.post(f"/v1/crucible/{run_id}/document").json()
    assert again["id"] == doc["id"]
    assert again["body_html"] == "<p>Only my sentence survives.</p>"
    assert again["detached"] is True


def test_a_second_run_gets_its_own_document_and_leaves_the_first_alone(ctx):
    """Asking the same question again is a NEW run. It renders its own report;
    the edited one from last time is not touched, and is not reused."""
    first = _ready_run(ctx)
    doc = ctx.client.post(f"/v1/crucible/{first}/document").json()
    ctx.client.patch(
        f"/v1/custom-artifacts/{doc['id']}",
        json={"body_html": "<p>Edited last week.</p>", "base_version": doc["version"]},
    )

    second = _ready_run(ctx)
    fresh = ctx.client.post(f"/v1/crucible/{second}/document").json()
    assert fresh["id"] != doc["id"]
    assert fresh["detached"] is False
    assert "Edited last week." not in fresh["body_html"]

    unchanged = ctx.client.get(f"/v1/crucible/{first}/document").json()
    assert unchanged["body_html"] == "<p>Edited last week.</p>"


# ─── 3. The run itself never moves ──────────────────────────────────────────

def test_editing_the_report_leaves_the_runs_findings_untouched(ctx):
    """THE IMMUTABILITY CLAIM. A run's reproducibility is the whole argument
    for it over asking a general model, and it survives editing only because
    the prose lives somewhere else."""
    run_id = _ready_run(ctx)
    before_findings, before_ledger = _findings_snapshot(run_id, ctx.company_id)
    doc = ctx.client.post(f"/v1/crucible/{run_id}/document").json()

    ctx.client.patch(
        f"/v1/custom-artifacts/{doc['id']}",
        json={"body_html": "<p>Everything above is wrong.</p>",
              "base_version": doc["version"]},
    )

    after_findings, after_ledger = _findings_snapshot(run_id, ctx.company_id)
    assert after_findings == before_findings
    assert after_ledger == before_ledger
    # And the run still reports them, so the way back is real.
    detail = ctx.client.get(f"/v1/crucible/{run_id}").json()
    assert [f["statement"] for f in detail["findings"]] == [
        f["statement"] for f in before_findings
    ]


# ─── 4. Forking ─────────────────────────────────────────────────────────────

def test_saving_a_copy_leaves_the_runs_own_report_alone(ctx):
    run_id = _ready_run(ctx)
    doc = ctx.client.post(f"/v1/crucible/{run_id}/document").json()
    copy = ctx.client.post(f"/v1/crucible/{run_id}/document/fork").json()

    assert copy["id"] != doc["id"]
    assert copy["run_id"] is None
    # The run still points at its OWN report, not the copy.
    assert ctx.client.get(f"/v1/crucible/{run_id}").json()["artifact_id"] == doc["id"]


def test_a_copy_carries_the_edits_it_was_forked_from(ctx):
    """A fork of the ORIGINAL rendering would be a "revert" wearing a "save a
    copy" label — it would discard the user's edits at the moment they asked to
    keep them."""
    run_id = _ready_run(ctx)
    doc = ctx.client.post(f"/v1/crucible/{run_id}/document").json()
    ctx.client.patch(
        f"/v1/custom-artifacts/{doc['id']}",
        json={"body_html": "<p>My version.</p>", "base_version": doc["version"]},
    )
    copy = ctx.client.post(f"/v1/crucible/{run_id}/document/fork").json()

    from app.db.custom_artifacts import get_artifact

    assert "My version." in get_artifact(ctx.company_id, copy["id"])["body_html"]


def test_a_copy_is_not_a_report_so_the_chat_editor_cannot_reach_it(ctx):
    """The fork's `kind` is deliberately not `goal_analysis`. The chat edit
    tool resolves its target by kind AND by a run behind it; a fork has no run,
    and letting it match would hand the tool a document with no analysis to be
    honest about."""
    run_id = _ready_run(ctx)
    ctx.client.post(f"/v1/crucible/{run_id}/document")
    copy = ctx.client.post(f"/v1/crucible/{run_id}/document/fork").json()
    assert copy["kind"] != "goal_analysis"


# ─── 5. Tenancy ─────────────────────────────────────────────────────────────

def _intruder(ctx):
    from fastapi.testclient import TestClient
    import app.main as main_mod

    other_user = "other-" + uuid.uuid4().hex[:8]
    other_company = seed_company(user_id=other_user, slug="other")
    _enable(other_company)
    return TestClient(main_mod.app, headers=supabase_bearer(other_user))


def test_another_companys_run_is_404_on_every_document_route(ctx):
    """404, never 403. A 403 confirms the run exists, which is the disclosure
    the tenant filter is there to prevent."""
    run_id = _ready_run(ctx)
    ctx.client.post(f"/v1/crucible/{run_id}/document")
    them = _intruder(ctx)

    for method, path in (
        ("get", f"/v1/crucible/{run_id}/document"),
        ("post", f"/v1/crucible/{run_id}/document"),
        ("post", f"/v1/crucible/{run_id}/document/fork"),
    ):
        res = getattr(them, method)(path)
        assert res.status_code == 404, (method, path, res.status_code)

    res = them.post(
        f"/v1/crucible/{run_id}/document/chat-edit", json={"instruction": "mine now"}
    )
    assert res.status_code == 404


def test_another_company_cannot_read_the_report_body_through_documents(ctx):
    """The report is a `custom_artifacts` row, so it inherits that table's
    tenant filter too — belt AND braces, because the report body is a
    company's own analysis of its own customers."""
    run_id = _ready_run(ctx)
    doc = ctx.client.post(f"/v1/crucible/{run_id}/document").json()
    assert _intruder(ctx).get(f"/v1/custom-artifacts/{doc['id']}").status_code == 404


def test_a_company_without_the_flag_is_refused_at_the_document_routes(
    crucible_env, monkeypatch
):
    """The UI gate is not the gate. A company off the allowlist gets 403 from
    the route itself, whatever the client chose to render."""
    c = company_client(monkeypatch)          # no _enable
    assert c.client.post("/v1/crucible/1/document").status_code == 403
    assert c.client.get("/v1/crucible/1/document").status_code == 403


# ─── 6. The chat edit ───────────────────────────────────────────────────────

@pytest.fixture
def stub_editor(monkeypatch):
    """The scoped editor, stubbed. Records what it was asked to edit so a test
    can assert the TARGET as well as the result."""
    seen: list[dict] = []

    def fake(report_html: str, instruction: str, enterprise_id: str) -> dict:
        seen.append({"html": report_html, "instruction": instruction,
                     "company": enterprise_id})
        return {
            "html": f"<h1>Edited</h1><p>{instruction}</p>",
            "sections_changed": ["The short version"],
            "summary": "Rewrote the summary.",
        }

    monkeypatch.setattr("app.goal_report_chat_edit.apply_report_edit", fake)
    return seen


def test_a_chat_edit_writes_the_report_and_detaches_it(ctx, stub_editor):
    run_id = _ready_run(ctx)
    ctx.client.post(f"/v1/crucible/{run_id}/document")

    res = ctx.client.post(
        f"/v1/crucible/{run_id}/document/chat-edit",
        json={"instruction": "rewrite the summary for an exec"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["sections_changed"] == ["The short version"]
    assert body["document"]["detached"] is True
    assert "rewrite the summary for an exec" in body["document"]["body_html"]
    # It edited THE REPORT — it was handed the rendered report, not a blank.
    assert "raise renewal rate" in stub_editor[0]["html"]


def test_a_chat_edit_leaves_the_runs_findings_untouched(ctx, stub_editor):
    """The same immutability claim, on the LLM path. An edit that reached the
    findings would be undetectable afterwards: the report would still read
    correctly, and would no longer be about the run it names."""
    run_id = _ready_run(ctx)
    ctx.client.post(f"/v1/crucible/{run_id}/document")
    before = _findings_snapshot(run_id, ctx.company_id)

    ctx.client.post(
        f"/v1/crucible/{run_id}/document/chat-edit", json={"instruction": "tighten it"}
    )
    assert _findings_snapshot(run_id, ctx.company_id) == before


def test_a_chat_edit_bumps_the_version_so_a_concurrent_save_is_caught(
    ctx, stub_editor
):
    """The edit goes through the same compare-and-set every other write to this
    table does, so an editor tab holding the old version is told rather than
    silently overwritten."""
    run_id = _ready_run(ctx)
    doc = ctx.client.post(f"/v1/crucible/{run_id}/document").json()
    ctx.client.post(
        f"/v1/crucible/{run_id}/document/chat-edit", json={"instruction": "tighten it"}
    )
    stale = ctx.client.patch(
        f"/v1/custom-artifacts/{doc['id']}",
        json={"body_html": "<p>typed before the chat edit landed</p>",
              "base_version": doc["version"]},
    )
    assert stale.status_code == 409


def test_a_chat_edit_on_a_run_with_no_report_is_refused(ctx, stub_editor):
    """There is no target, so there is nothing to write. Rendering one here
    would mean asking a question about a report you never opened silently
    creates one."""
    run_id = _ready_run(ctx)
    res = ctx.client.post(
        f"/v1/crucible/{run_id}/document/chat-edit", json={"instruction": "tighten it"}
    )
    assert res.status_code == 404
    assert stub_editor == []


def test_an_instruction_that_is_not_an_edit_writes_nothing(ctx, monkeypatch):
    """A no-op save would bump the version and DETACH a report nobody changed —
    the marker would then be firing on a question."""
    monkeypatch.setattr(
        "app.goal_report_chat_edit.apply_report_edit",
        lambda html, instruction, enterprise_id: {
            "html": html, "sections_changed": [], "summary": "No edit needed.",
        },
    )
    run_id = _ready_run(ctx)
    doc = ctx.client.post(f"/v1/crucible/{run_id}/document").json()
    ctx.client.post(
        f"/v1/crucible/{run_id}/document/chat-edit",
        json={"instruction": "what does this say about onboarding?"},
    )
    after = ctx.client.get(f"/v1/crucible/{run_id}/document").json()
    assert after["version"] == doc["version"]
    assert after["detached"] is False


# ─── 7. The tool: the model never picks the target ──────────────────────────

def test_the_tool_schema_has_no_id_for_a_model_to_supply():
    """THE STRUCTURAL HALF of "the model never picks the target". The prose in
    the description is the half a model can ignore; `additionalProperties:
    False` with `instruction` as the only property is the half it cannot."""
    from app.goal_report_chat_edit import EDIT_GOAL_REPORT_TOOL

    schema = EDIT_GOAL_REPORT_TOOL["input_schema"]
    assert set(schema["properties"]) == {"instruction"}
    assert schema["required"] == ["instruction"]
    assert schema["additionalProperties"] is False
    # And the description says so, because the model reads that and not this.
    description = EDIT_GOAL_REPORT_TOOL["description"]
    assert "do NOT choose or pass a report id" in description
    assert "open beside this chat" in description
    # No confirm gate, stated where the model will act on it — the PRD surface
    # retired that gate (e05577dc) and a report that kept one would diverge.
    assert "no confirmation step" in description


def test_the_tool_refuses_when_no_report_is_open(ctx, stub_editor):
    """A closure over `None` is what "nothing is open" looks like to the
    handler. It must refuse WITHOUT reading or writing anything — not fall back
    to the newest report, which is how a model ends up editing a document the
    user is not looking at."""
    from app.goal_report_chat_edit import NO_REPORT_OPEN, make_edit_goal_report_handler

    run_id = _ready_run(ctx)
    ctx.client.post(f"/v1/crucible/{run_id}/document")

    handle = make_edit_goal_report_handler(None, _ctx_company(ctx))
    narration, pending = handle({"instruction": "cut the ruled-out list"})
    assert narration == NO_REPORT_OPEN
    assert pending is None
    assert stub_editor == []


def test_the_tool_refuses_a_document_that_is_not_a_report_on_a_run(ctx, stub_editor):
    """A surface handing the handler one of this company's OTHER documents must
    not get it rewritten by a prompt tuned for a report. Refused as a plain
    404 — the same answer as absent, so probing ids reveals nothing."""
    from app.goal_report_chat_edit import make_edit_goal_report_handler

    other = ctx.client.post(
        "/v1/custom-artifacts",
        json={"kind": "launch plan", "title": "Launch", "body_html": "<p>Plan.</p>"},
    ).json()

    handle = make_edit_goal_report_handler(other["id"], _ctx_company(ctx))
    narration, _ = handle({"instruction": "make it shorter"})
    assert "couldn't apply" in narration.lower()
    assert stub_editor == []
    from app.db.custom_artifacts import get_artifact

    assert get_artifact(ctx.company_id, other["id"])["body_html"] == "<p>Plan.</p>"


def test_the_tool_edits_the_report_it_was_given(ctx, stub_editor):
    from app.goal_report_chat_edit import make_edit_goal_report_handler

    run_id = _ready_run(ctx)
    doc = ctx.client.post(f"/v1/crucible/{run_id}/document").json()

    handle = make_edit_goal_report_handler(doc["id"], _ctx_company(ctx))
    narration, _ = handle({"instruction": "cut the ruled-out list"})
    assert "Updated the report" in narration
    # The narration tells the truth about what just happened to the document.
    assert "no longer regenerated" in narration
    assert ctx.client.get(f"/v1/crucible/{run_id}/document").json()["detached"] is True


def test_a_foreign_report_id_handed_to_the_tool_is_refused(ctx, stub_editor):
    """Even with a real report id, the writer's own tenant filter decides. The
    surface that builds the closure is not trusted to have got the company
    right."""
    from app.goal_report_chat_edit import make_edit_goal_report_handler

    run_id = _ready_run(ctx)
    doc = ctx.client.post(f"/v1/crucible/{run_id}/document").json()

    other_user = "other-" + uuid.uuid4().hex[:8]
    other_company = seed_company(user_id=other_user, slug="other")
    handle = make_edit_goal_report_handler(
        doc["id"], _Company(other_company, other_user)
    )
    narration, _ = handle({"instruction": "make it mine"})
    assert "couldn't apply" in narration.lower()
    assert stub_editor == []


class _Company:
    """The two attributes the writer reads. Enough on purpose — a writer that
    needed more of a request context than this would be reaching for something
    a chat turn does not have."""

    def __init__(self, company_id: str, user_id: str):
        self.company_id = company_id
        self.user_id = user_id


def _ctx_company(ctx) -> _Company:
    return _Company(ctx.company_id, ctx.user_id)


# ─── 7. The second writer ───────────────────────────────────────────────────
#
# Every test below exists because a deliberate mutation survived the suite
# without it: the code was right, and nothing here could tell. They share a
# shape — each concerns a SECOND writer, real or implied, and none can be seen
# by driving the happy path once.

def test_the_payload_body_is_the_document_and_nothing_appended(ctx):
    """THE ONE THAT WAS ACTUALLY BROKEN. `_document_payload` built its body as
    `body_html + title`, which did two wrong things at once: it shipped a
    document whose HTML had the title glued to the end, and it fingerprinted
    body+title against a hash stored over the body ALONE — so every report was
    born detached and the banner fired on prose nobody had touched.

    Five tests caught that, all of them THROUGH `detached`, which means a later
    change to how detachment is computed could carry the guard away with it.
    This one names the property directly: what the API calls the body is the
    document, byte for byte.
    """
    from app.db.custom_artifacts import get_artifact

    run_id = _ready_run(ctx)
    payload = ctx.client.post(f"/v1/crucible/{run_id}/document").json()
    stored = get_artifact(ctx.company_id, payload["id"])

    assert payload["body_html"] == stored["body_html"]
    # And the title is not hiding on the end of it. Equality alone would still
    # pass if BOTH sides had been concatenated by the same helper.
    assert not payload["body_html"].endswith(payload["title"])


def test_the_linked_report_is_not_stamped_with_the_conversation(ctx):
    """A linked report carries NO `conversation_id`, though its run has one.

    This is invisible from the Goal Analysis panel, which is why it needs its
    own test. `useThreadDocumentSync` attaches the newest document of a
    conversation to the panel's DOCUMENT tab on reload — stamp the report and
    every Goal Analysis run grows a phantom Document tab beside it, holding the
    same report the analysis tab already shows.

    The FORK is stamped, deliberately: that one is a document the user asked
    for. Asserting both halves is what makes this a distinction rather than a
    column nobody writes.
    """
    from app.db import crucible_runs as runs_db
    from app.db.custom_artifacts import get_artifact

    run_id = _ready_run(ctx)
    convo = ctx.client.post("/v1/conversations", json={"title": "goal chat"}).json()
    runs_db.update(run_id, ctx.company_id, conversation_id=convo["id"])

    doc = ctx.client.post(f"/v1/crucible/{run_id}/document").json()
    assert get_artifact(ctx.company_id, doc["id"]).get("conversation_id") is None

    copy = ctx.client.post(f"/v1/crucible/{run_id}/document/fork").json()
    assert copy["conversation_id"] == convo["id"]


def test_a_lost_link_race_returns_the_winner_and_leaves_no_orphan(ctx, monkeypatch):
    """A double-click is the ordinary way to send two creates at once.

    The claim lives in `link_document`'s WHERE clause (`artifact_id IS NULL`),
    so the loser must hand back the WINNER'S report and delete the document it
    had already created — otherwise the shared library grows a stray report no
    run points at, which nobody can explain and nobody can find.

    The race is FORCED, not threaded: the rival links its own artifact from
    inside `create_artifact`, at exactly the moment a real concurrent request
    would have. A sleep-and-hope version of this passes on a fast machine and
    flakes in CI.
    """
    from app.db import crucible_runs as runs_db
    from app.db import custom_artifacts as ca

    run_id = _ready_run(ctx)
    real_create = ca.create_artifact
    rival: dict = {}

    def create_then_lose_the_race(company_id, **kw):
        mine = real_create(company_id, **kw)
        if not rival:
            other = real_create(company_id, **{**kw, "title": "rival report"})
            runs_db.link_document(
                run_id, company_id,
                artifact_id=other["id"], body_hash="rival-hash",
            )
            rival["id"] = other["id"]
        return mine

    monkeypatch.setattr(
        "app.db.custom_artifacts.create_artifact", create_then_lose_the_race
    )
    # NOT `monkeypatch.undo()` here: this monkeypatch is the same one `ctx`
    # used to install the fake Supabase client, so undoing it mid-test drops
    # the whole fixture and the next call goes to the real network. The patch
    # disarms itself after the first create instead.
    res = ctx.client.post(f"/v1/crucible/{run_id}/document")

    assert res.status_code == 200
    # The caller gets the report the run actually points at, not the one this
    # request just made.
    assert res.json()["id"] == rival["id"]
    assert ctx.client.get(f"/v1/crucible/{run_id}").json()["artifact_id"] == rival["id"]

    # And the loser's document is gone from the library — "a stray report I
    # cannot explain" is the form the user would report this in.
    listed = [d["id"] for d in
              ctx.client.get("/v1/custom-artifacts").json()["artifacts"]]
    assert listed == [rival["id"]]


def test_a_chat_edit_that_loses_a_race_is_refused_not_merged(ctx, monkeypatch):
    """The chat edit sends the version it READ as its `base_version`.

    It re-reads the row, hands the body to the model, and writes back — and the
    model call is slow, which is the entire window. The existing version test
    cannot see this: the edit bumps the version whether or not it passed a
    base, so a stale PATCH afterwards gets its 409 either way.

    What is only visible here is a human save landing DURING the model call.
    With the compare-and-set the edit is refused; without it, someone's typing
    is overwritten by prose generated from a body that no longer exists.
    """
    from app.db.custom_artifacts import get_artifact

    run_id = _ready_run(ctx)
    doc = ctx.client.post(f"/v1/crucible/{run_id}/document").json()

    def edit_while_someone_types(html, instruction, enterprise_id):
        ctx.client.patch(
            f"/v1/custom-artifacts/{doc['id']}",
            json={"body_html": "<p>Typed by hand mid-generation.</p>",
                  "base_version": doc["version"]},
        )
        return {"html": "<p>Model output over a stale body.</p>",
                "sections_changed": ["The short version"],
                "summary": "tightened"}

    monkeypatch.setattr(
        "app.goal_report_chat_edit.apply_report_edit", edit_while_someone_types
    )
    res = ctx.client.post(
        f"/v1/crucible/{run_id}/document/chat-edit",
        json={"instruction": "tighten it"},
    )

    assert res.status_code == 409
    # The human's words survive. A 409 that still wrote would be worse than no
    # gate at all, so the body is the assertion that matters.
    body = get_artifact(ctx.company_id, doc["id"])["body_html"]
    assert "Typed by hand mid-generation." in body
    assert "Model output over a stale body." not in body
