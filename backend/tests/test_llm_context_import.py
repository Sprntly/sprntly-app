"""Tests for the "bring your own LLM context" import (client feedback, 2026-07-22).

The user runs our prompt in whichever assistant they already use and uploads
the Markdown it returns; skipping means typing onboarding out by hand. There is
deliberately no OAuth path (see app/llm_context.py for why), so what carries
code is the prompt, the parser that reads an export back, and the upload route.

These lean on the properties that make the feature safe to ship:

  * the parser NEVER invents a value ("UNKNOWN" and unknown headings do not
    become field content), and never silently drops one (unrecognised sections
    land in `unmapped`)
  * a file we understood nothing in reports `ok: false` with an explanation,
    rather than a cheerful no-op
  * the prompt and the parser cannot drift apart without failing a test
"""
from __future__ import annotations

import importlib
import io
import sys
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from tests._company_helpers import company_client


def _reload_app_modules():
    for name in (
        "app.config",
        "app.connectors.tokens",
        "app.llm_context",
        "app.routes.connectors",
        "app.main",
    ):
        if name in sys.modules:
            importlib.reload(sys.modules[name])


@pytest.fixture
def import_env(isolated_settings, monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    _reload_app_modules()
    yield


# A well-formed export, as the prompt instructs the assistant to produce it.
FULL_EXPORT = """\
<!-- sprntly-context v1 -->

## Company
- Name: Samsung Health
- Website: https://www.samsung.com/health

## Mission and vision
Turn continuous sensing from the Galaxy wearable portfolio into daily health
guidance for everyone.

## Strategy and OKRs
Passive tracking to proactive AI guidance. Hold ~77M MAU through Watch 9.

## Portfolio
Samsung Health app, Galaxy Watch, Galaxy Ring, Xealth.

## Planning cycle
Every half

## Product
- Name: Samsung Health
- Website: samsung.com/health
- Surfaces: Web, Mobile app, Hardware
- Monetization: Partner rev-share

## Users
Wearable-attached athletes, casual Galaxy-phone trackers, care-adjacent users.

## Competitors
Apple Health, Fitbit, Oura, Garmin

## Metrics
Monthly Active Users, Day-30 retention, Activation rate

## Prioritization
Based on goal.

## Team
Owns the Nutrition and Sleep pillars end to end.

## Anything else
We call the pairing flow "sleep sync" internally.
"""


# ─────────────────────────── Parser ───────────────────────────


def test_parses_every_documented_section():
    from app.llm_context import parse_context_markdown

    parsed = parse_context_markdown(FULL_EXPORT)
    f = parsed.fields

    assert parsed.format_version == "1"
    assert f["company_name"] == "Samsung Health"
    assert f["company_website"] == "https://www.samsung.com/health"
    assert f["product_name"] == "Samsung Health"
    # Closed-vocabulary fields are canonicalised to what the form/DB accept, not
    # written as the assistant phrased them — "Every half" is the chip labelled
    # so, whose stored value is "half".
    assert f["planning_cycle"] == "half"
    assert f["monetization"] == "partner-rev-share"
    assert "proactive AI guidance" in f["strategy"]
    assert "Galaxy wearable" in f["mission"]
    assert f["competitors"] == ["Apple Health", "Fitbit", "Oura", "Garmin"]
    assert f["metrics"] == [
        "Monthly Active Users",
        "Day-30 retention",
        "Activation rate",
    ]
    assert f["prioritization_framework"] == "goal-based"
    assert "Nutrition and Sleep" in f["team_scope"]
    assert "sleep sync" in f["notes"]


def test_surfaces_map_to_the_product_step_vocabulary():
    """The product step accepts a fixed set of surface values, so free-text
    from an assistant must be mapped — and anything unmappable dropped from
    the field rather than pushed in as an invalid chip."""
    from app.llm_context import parse_context_markdown

    parsed = parse_context_markdown(FULL_EXPORT)
    assert parsed.fields["surfaces"] == ["web", "mobile", "hardware"]

    odd = parse_context_markdown(
        "## Product\n- Surfaces: Web, website, smart fridge, iOS\n"
    )
    # De-duplicated (web/website collapse) and order-preserving.
    assert odd.fields["surfaces"] == ["web", "mobile"]
    # The value we could not classify is reported, never silently discarded.
    assert "smart fridge" in "".join(odd.unmapped.values())


def test_free_text_in_a_constrained_field_is_dropped_not_written_raw():
    """Regression (client upload, 2026-07-22): companies.planning_cycle and
    prioritization_framework carry a DB CHECK. A real export described a
    six-week cadence and a RICE-with-caveats process in prose; writing those
    verbatim violated the constraint and sank the ENTIRE workspace write, so
    the whole import surfaced as "couldn't save it to your workspace."

    An unmappable value must therefore be LEFT OUT of the fields (blank is
    safe; the user picks it) and preserved in `unmapped`, never emitted raw.
    """
    from app.llm_context import parse_context_markdown

    parsed = parse_context_markdown(
        "## Planning cycle\n"
        "Six-week build cycles with a one-week cooldown. Quarterly OKR reviews.\n\n"
        "## Prioritization\n"
        "We mostly wing it based on who shouts loudest.\n\n"
        "## Product\n- Monetization: Seat-based subscription with an annual "
        "enterprise tier; free tier for individuals.\n"
    )
    # None of the three reaches the form as a raw phrase.
    assert "planning_cycle" not in parsed.fields
    assert "prioritization_framework" not in parsed.fields
    assert "monetization" not in parsed.fields
    # …and nothing is silently lost — each is kept for the reviewer.
    joined = " ".join(parsed.unmapped.values())
    assert "Six-week build cycles" in joined
    assert "shouts loudest" in joined
    assert "enterprise tier" in joined


def test_constrained_fields_map_common_phrasings_to_canonical_values():
    """The clean cases still land: canonical tokens, the chip labels, and a
    distinctive framework acronym buried in a sentence all resolve."""
    from app.llm_context import parse_context_markdown

    parsed = parse_context_markdown(
        "## Planning cycle\nQuarterly\n\n"
        "## Prioritization\nRICE scoring for anything above two engineer-weeks.\n\n"
        "## Product\n- Monetization: Usage-based\n"
    )
    assert parsed.fields["planning_cycle"] == "quarterly"
    assert parsed.fields["prioritization_framework"] == "rice"
    assert parsed.fields["monetization"] == "usage"


def test_unknown_placeholders_never_become_field_values():
    """The prompt tells the assistant to write UNKNOWN rather than guess. That
    must leave the onboarding field EMPTY — writing the literal word in would
    be worse than the blank the user can fill themselves."""
    from app.llm_context import parse_context_markdown

    parsed = parse_context_markdown(
        "## Company\n- Name: UNKNOWN\n- Website: unknown\n\n"
        "## Mission and vision\nUNKNOWN\n\n"
        "## Portfolio\nN/A\n"
    )
    assert parsed.fields == {}
    assert parsed.is_empty


def test_heading_punctuation_variants_are_tolerated():
    """We do not control the assistant. `&` vs `and`, a trailing colon, and
    `###` instead of `##` are all the same section."""
    from app.llm_context import parse_context_markdown

    parsed = parse_context_markdown(
        "### Mission & Vision:\nWhy we exist.\n\n## Strategy / OKRs\nGrow.\n"
    )
    assert parsed.fields["mission"] == "Why we exist."
    assert parsed.fields["strategy"] == "Grow."


def test_unrecognised_sections_are_kept_not_dropped():
    from app.llm_context import parse_context_markdown

    parsed = parse_context_markdown(
        "## Mission and vision\nWhy we exist.\n\n## Regulatory posture\nHIPAA, GDPR.\n"
    )
    assert parsed.fields["mission"] == "Why we exist."
    assert parsed.unmapped["Regulatory posture"] == "HIPAA, GDPR."


def test_a_file_we_understand_nothing_in_is_reported_as_empty():
    from app.llm_context import parse_context_markdown

    for junk in ("", "   \n\n", "Just some prose with no headings at all."):
        assert parse_context_markdown(junk).is_empty


def test_preamble_before_the_first_heading_is_ignored():
    """Assistants add "Here's your export:" despite being told not to. That
    must not be mistaken for a section body."""
    from app.llm_context import parse_context_markdown

    parsed = parse_context_markdown(
        "Sure! Here is the document you asked for:\n\n## Portfolio\nOne app.\n"
    )
    assert parsed.fields == {"portfolio": "One app."}


def test_prompt_and_parser_agree_on_every_heading():
    """Integrity guard: each `##` heading the prompt tells the assistant to
    emit must be one the parser knows how to place. Drift between the two is
    silent data loss, so it fails here instead."""
    import re

    from app.llm_context import (
        _BULLET_FIELDS,
        _NON_FIELD_SECTIONS,
        _SECTION_FIELDS,
        _normalise,
        CONTEXT_PROMPT,
    )

    headings = [
        _normalise(h) for h in re.findall(r"^## (.+)$", CONTEXT_PROMPT, re.MULTILINE)
    ]
    assert headings, "the prompt must contain the heading contract"
    bullet_sections = {section for section, _ in _BULLET_FIELDS}
    for heading in headings:
        assert (
            heading in _SECTION_FIELDS
            or heading in bullet_sections
            or heading in _NON_FIELD_SECTIONS
        ), heading

    # The reverse direction: every field the parser knows how to place must
    # still be asked for. A heading dropped from the prompt is silent data
    # loss — the parser keeps working and simply never sees that field again.
    for section in _SECTION_FIELDS:
        assert section in headings, f"prompt no longer asks for: {section}"
    for section in bullet_sections:
        assert section in headings, f"prompt no longer asks for: {section}"


def test_parses_an_export_in_the_full_stage_5_shape():
    """End-to-end on what the current prompt actually asks for.

    Stage 5 emits the field headings, then a `## Review checklist` and a
    `## Status` block for the human. The fields must map; the two trailing
    blocks must be PRESERVED in `unmapped` (the route files them with the
    export) rather than being mistaken for company facts — and a section the
    assistant answered UNKNOWN must stay blank.
    """
    from app.llm_context import parse_context_markdown

    parsed = parse_context_markdown(
        "<!-- sprntly-context v1 -->\n\n"
        "## Company\n- Name: Samsung Health\n- Website: UNKNOWN\n\n"
        "## Mission and vision\nDaily health guidance for everyone.\n\n"
        "## Strategy and OKRs\nUNKNOWN\n\n"
        "## Portfolio\nWatch, Ring, Buds.\n\n"
        "## Planning cycle\nEvery half\n\n"
        "## Product\n- Name: Samsung Health\n- Surfaces: Mobile app, Hardware\n"
        "- Monetization: Partner rev-share\n\n"
        "## Users\nWearable-attached athletes and casual trackers.\n\n"
        "## Competitors\nApple Health, Oura\n\n"
        "## Metrics\nMonthly Active Users, Day-30 retention\n\n"
        "## Prioritization\nBased on goal.\n\n"
        "## Team\nOwns Nutrition and Sleep.\n\n"
        "## Anything else\n### Not doing\nNo subscription tier this half.\n\n"
        "## Review checklist\n- Planning cycle is Medium (last confirmed Q1 2026).\n\n"
        "## Status\nentity: Samsung Health\nentity_confidence: high\n"
    )

    assert parsed.format_version == "1"
    assert parsed.fields["company_name"] == "Samsung Health"
    assert parsed.fields["surfaces"] == ["mobile", "hardware"]
    assert parsed.fields["competitors"] == ["Apple Health", "Oura"]
    assert "Not doing" in parsed.fields["notes"]
    # UNKNOWN answers leave the field blank rather than writing the word in.
    assert "company_website" not in parsed.fields
    assert "strategy" not in parsed.fields
    # The reviewer's blocks survive, unmixed with the company's facts.
    assert "Review checklist" in parsed.unmapped
    assert "Status" in parsed.unmapped
    assert "entity_confidence: high" in parsed.unmapped["Status"]


def test_prompt_carries_the_no_guessing_discipline():
    """The import is only trustworthy because the prompt forbids invention.

    These instructions are the reason a parsed field can be believed at all —
    if a rewrite drops them, the export starts arriving full of plausible
    fabrications that look exactly like facts.
    """
    from app.llm_context import CONTEXT_PROMPT

    lowered = CONTEXT_PROMPT.lower()
    assert "unknown" in lowered
    assert "do not infer" in lowered
    # Synthetic/demo data is the most common source of false product metrics.
    assert "synthetic data" in lowered
    # A wrong value must be framed as worse than a blank one.
    assert "worse than a blank one" in lowered


# ─────────────────────────── Upload route ───────────────────────────


def _md_file(body: str, name: str = "context.md") -> dict:
    return {"file": (name, io.BytesIO(body.encode("utf-8")), "text/markdown")}


def _no_llm():
    """Patch the extraction's LLM call to fail, so a route test exercises the
    DETERMINISTIC read alone. `extract_context_fields` degrades to the heading
    parse on any error, so the background job still completes — just with
    nothing beyond what the parser found. Keeps these tests offline."""
    return patch("app.llm.call_json", side_effect=RuntimeError("no LLM in tests"))


def test_upload_returns_prefill_fields_and_files_the_export(import_env, monkeypatch):
    client = company_client(monkeypatch).client
    with patch("app.document_sources.create_document_source") as create, patch(
        "app.document_sources.add_document_file"
    ) as add, patch("app.routes.connectors.kickoff_sync"), _no_llm():
        create.return_value = type("S", (), {"id": "src-1"})()
        r = client.post(
            "/v1/connectors/llm-context/import", files=_md_file(FULL_EXPORT)
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["fields"]["company_name"] == "Samsung Health"
    # The context also becomes a document source, so it grounds the agents
    # rather than only pre-filling a form.
    add.assert_called_once()
    # …and the response says so: `filed` is the "reached the knowledge graph"
    # signal the Business Context card reports success from.
    assert body["filed"] is True
    # The upload also hands off a background LLM extraction for the fields the
    # heading walk can't reach; its job id rides back on the response.
    assert isinstance(body["job_id"], int)


def test_upload_of_an_unreadable_file_defers_to_the_extraction_job(import_env, monkeypatch):
    """A file the heading walk reads nothing from is NO LONGER a definitive
    failure: the background LLM pass may still recognise it. So while a job is
    live the synchronous response withholds the "found nothing" verdict (note
    is None) and hands back a job id — the poll settles ok/note either way."""
    client = company_client(monkeypatch).client
    with patch("app.document_sources.create_document_source"), patch(
        "app.document_sources.add_document_file"
    ), patch("app.routes.connectors.kickoff_sync"), _no_llm():
        r = client.post(
            "/v1/connectors/llm-context/import",
            files=_md_file("nothing we recognise here"),
        )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["fields"] == {}
    # A job is running, so the premature "we found nothing" note is suppressed.
    assert body["note"] is None
    assert isinstance(body["job_id"], int)
    # The raw file still reached the knowledge graph even though the heading
    # walk read nothing — that's exactly the case the Business Context card
    # reports as a success rather than "couldn't read that file".
    assert body["filed"] is True

    # With the LLM patched to fail, the job settles on the same honest verdict
    # the old synchronous response used to give directly.
    status = client.get(f"/v1/connectors/llm-context/import/{body['job_id']}")
    assert status.status_code == 200
    done = status.json()
    assert done["status"] == "ready"
    assert done["result"]["ok"] is False
    assert done["result"]["note"], "a job that found nothing must say so"


def test_upload_reports_a_filing_failure_instead_of_hiding_it(import_env, monkeypatch):
    """If the raw .md can't be filed as a document source, it never reached the
    knowledge graph — and the Business Context card, which only cares about the
    KG feed, must not claim success. So `filed` is False and the explanatory
    note survives even though a background job is running: a filing failure is
    NOT the "found nothing" verdict the job can overturn, so it must NOT be
    swept under the note-suppression that unreadable-but-filed uploads get.

    Uses an unreadable body on purpose (ok is False), which is exactly the path
    that previously wiped the note whenever a job was live."""
    client = company_client(monkeypatch).client
    with patch(
        "app.document_sources.create_document_source",
        side_effect=RuntimeError("storage down"),
    ), patch("app.routes.connectors.kickoff_sync"), _no_llm():
        r = client.post(
            "/v1/connectors/llm-context/import",
            files=_md_file("nothing we recognise here"),
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    # It never got filed, so it isn't in the knowledge graph…
    assert body["filed"] is False
    # …and the note that says so is preserved, not wiped by the live job.
    assert body["note"]
    assert "couldn't also save" in body["note"]


def test_upload_rejects_binary_with_a_useful_message(import_env, monkeypatch):
    client = company_client(monkeypatch).client
    r = client.post(
        "/v1/connectors/llm-context/import",
        files={"file": ("deck.pdf", io.BytesIO(b"\x89PNG\r\n\x1a\n\xff\xfe"), "application/pdf")},
    )
    assert r.status_code == 415
    assert ".md" in r.json()["detail"]


def test_upload_rejects_an_empty_file(import_env, monkeypatch):
    client = company_client(monkeypatch).client
    r = client.post(
        "/v1/connectors/llm-context/import",
        files={"file": ("context.md", io.BytesIO(b""), "text/markdown")},
    )
    assert r.status_code == 400


def test_prompt_endpoint_serves_the_parser_s_contract(import_env, monkeypatch):
    client = company_client(monkeypatch).client
    r = client.get("/v1/connectors/llm-context/prompt")
    assert r.status_code == 200
    body = r.json()
    assert "## Company" in body["prompt"]
    # The no-guessing instruction is load-bearing, not decoration.
    assert "UNKNOWN" in body["prompt"]
    assert body["format_version"] == "1"


# ─────────────────────── LLM extraction pass ───────────────────────
#
# The heading walk above only reads files OUR prompt produced. The extraction
# pass reads context documents of any shape, so it is what a real user's
# arbitrary export actually goes through. It runs as a background job; these
# cover the merge rules and the validation that stands between a model's
# free-text answer and the onboarding form.


def _extract(markdown, fake_return):
    """Run the extraction with the LLM stubbed to `fake_return`."""
    from app.llm_context import extract_context_fields

    with patch("app.llm.call_json", return_value=fake_return):
        return extract_context_fields(markdown)


def test_extraction_reads_a_file_the_heading_walk_cannot():
    """A plain prose brief with none of our headings parses to nothing
    deterministically, but the LLM pass fills the fields from it."""
    from app.llm_context import parse_context_markdown

    prose = "We're Acme. We sell a subscription web app to ops teams at SMB fintechs."
    assert parse_context_markdown(prose).is_empty

    parsed = _extract(
        prose,
        {
            "company_name": "Acme",
            "company_website": "",
            "mission": "",
            "strategy": "",
            "portfolio": "",
            "planning_cycle": "",
            "product_name": "Acme",
            "product_website": "",
            "surfaces": ["web"],
            "monetization": "subscription",
            "users_description": "Ops teams at SMB fintechs",
            "competitors": [],
            "metrics": [],
            "prioritization_framework": "",
            "team_scope": "",
            "notes": "",
        },
    )
    assert parsed.fields["company_name"] == "Acme"
    assert parsed.fields["surfaces"] == ["web"]
    assert parsed.fields["monetization"] == "subscription"
    assert parsed.fields["users_description"] == "Ops teams at SMB fintechs"


def test_deterministic_parse_wins_over_the_llm():
    """When both reads have a value for a field, the exact heading parse wins —
    the LLM may only FILL blanks, never overwrite what the contract nailed."""
    from app.llm_context import extract_context_fields, parse_context_markdown

    source = "## Company\n- Name: Samsung Health\n\n## Portfolio\nWatch, Ring.\n"
    base = parse_context_markdown(source)
    with patch(
        "app.llm.call_json",
        return_value={
            "company_name": "WRONG CO",  # must be ignored — base already has it
            "portfolio": "wrong portfolio",  # ditto
            "mission": "Filled from prose.",  # base blank → taken
            "company_website": "", "strategy": "", "planning_cycle": "",
            "product_name": "", "product_website": "", "surfaces": [],
            "monetization": "", "users_description": "", "competitors": [],
            "metrics": [], "prioritization_framework": "", "team_scope": "",
            "notes": "",
        },
    ):
        merged = extract_context_fields(source, base)

    assert merged.fields["company_name"] == "Samsung Health"
    assert merged.fields["portfolio"] == "Watch, Ring."
    assert merged.fields["mission"] == "Filled from prose."


def test_extraction_drops_values_outside_the_closed_vocabularies():
    """A monetization the form can't render, an invented framework, and a
    bogus surface are DROPPED — snapping them to the nearest option would read
    as the user's own answer. Placeholders never make it through either."""
    parsed = _extract(
        "some prose",
        {
            "company_name": "[Company Name]",  # placeholder → blank
            "company_website": "", "mission": "", "strategy": "", "portfolio": "",
            "planning_cycle": "biweekly",  # not in the vocabulary → dropped
            "product_name": "Acme", "product_website": "",
            "surfaces": ["web", "smart fridge"],  # fridge dropped, web kept
            "monetization": "crypto airdrops",  # not in the vocabulary → dropped
            "users_description": "", "competitors": [],
            "metrics": [], "prioritization_framework": "vibes",  # dropped
            "team_scope": "", "notes": "",
        },
    )
    assert "company_name" not in parsed.fields  # placeholder rejected
    assert "planning_cycle" not in parsed.fields
    assert "monetization" not in parsed.fields
    assert "prioritization_framework" not in parsed.fields
    assert parsed.fields["surfaces"] == ["web"]
    assert parsed.fields["product_name"] == "Acme"


def test_extraction_failure_degrades_to_the_heading_parse():
    """The pass is a background job whose only job is to WIDEN a prefill the
    user already has. Any LLM failure must return the deterministic parse
    unchanged, never raise — a raise would strand the job row."""
    from app.llm_context import extract_context_fields, parse_context_markdown

    base = parse_context_markdown("## Company\n- Name: Acme\n")
    with patch("app.llm.call_json", side_effect=RuntimeError("boom")):
        out = extract_context_fields("## Company\n- Name: Acme\n", base)
    assert out.fields == base.fields  # unchanged, no exception


def test_extraction_ignores_a_non_dict_response():
    """A model that returns something other than an object must not corrupt
    the parse or throw."""
    from app.llm_context import extract_context_fields, parse_context_markdown

    base = parse_context_markdown("## Company\n- Name: Acme\n")
    with patch("app.llm.call_json", return_value=["not", "a", "dict"]):
        out = extract_context_fields("## Company\n- Name: Acme\n", base)
    assert out.fields == base.fields


# ─────────────────────── Extraction job endpoint ───────────────────────


def test_import_job_status_returns_the_merged_result(import_env, monkeypatch):
    """End-to-end: upload a prose file the heading walk can't read, with the
    LLM stubbed to recognise it, and confirm the job the upload started lands
    the extracted fields under a `ready` status."""
    client = company_client(monkeypatch).client
    fake = {
        "company_name": "Acme", "company_website": "", "mission": "",
        "strategy": "", "portfolio": "", "planning_cycle": "",
        "product_name": "Acme", "product_website": "", "surfaces": ["web"],
        "monetization": "subscription", "users_description": "Ops leads",
        "competitors": [], "metrics": [], "prioritization_framework": "",
        "team_scope": "", "notes": "",
    }
    with patch("app.document_sources.create_document_source"), patch(
        "app.document_sources.add_document_file"
    ), patch("app.routes.connectors.kickoff_sync"), patch(
        "app.llm.call_json", return_value=fake
    ):
        r = client.post(
            "/v1/connectors/llm-context/import",
            files=_md_file("We're Acme, a subscription web app for ops leads."),
        )
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    assert isinstance(job_id, int)

    status = client.get(f"/v1/connectors/llm-context/import/{job_id}")
    assert status.status_code == 200
    done = status.json()
    assert done["status"] == "ready"
    assert done["result"]["ok"] is True
    assert done["result"]["fields"]["company_name"] == "Acme"
    assert done["result"]["fields"]["monetization"] == "subscription"


def test_import_job_status_404s_for_another_tenant(import_env, monkeypatch):
    """The job endpoint must not disclose another company's jobs — a missing /
    cross-tenant id is a flat 404, no existence leak."""
    client = company_client(monkeypatch).client
    r = client.get("/v1/connectors/llm-context/import/999999")
    assert r.status_code == 404
