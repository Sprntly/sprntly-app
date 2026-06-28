"""Tests for app.prd_runner — the 2-part PRD generation. Part A (the human PRD)
is produced by the `prd-author` skill; Part B (the Implementation Spec) is now
produced by the dedicated `implementation-spec` skill, fed the FINISHED Part A
(chained), instead of a second prd-author call steered by a directive.

Contract under test:
  - Part A and Part B are produced by TWO separate `gateway.llm_call`
    invocations: Part A binds skill='prd-author' (steered to the human-PRD half
    via _PART_A_DIRECTIVE); Part B binds skill='implementation-spec' and is fed
    the finished Part A as its input.
  - The two calls are CHAINED (Part B runs after Part A, consuming its output) —
    asserted by Part B's prompt containing Part A's text and by call order.
  - Part A output → payload_md; Part B output → llm_part.
  - Part B empty (degenerate) still renders payload_md (Part A alone is valid).
  - Part B CALL failure → PRD still completes with Part A + empty llm_part, and
    the failure is logged (not silent); Part A failure fails the whole PRD.
  - The generation is decision-logged with both skills recorded and
    has_llm_part accurate.

These tests mock at the gateway/llm seam: most patch `prd_runner.llm_call`; a
couple let the REAL gateway run and patch `app.llm.call_md` to assert each
half's METHOD (prd-author for A, implementation-spec for B) reaches the prompt.

New rows are written with variant='v2' by the route; the runner itself
doesn't touch variant — it produces the two parts and calls
`complete_prd_2part` against the existing row.
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from app import prd_runner
from app.graph.gateway import LLMResult


def _seed_corpus(data_dir, dataset="asurion", body="corpus body"):
    ds = data_dir / dataset
    ds.mkdir(exist_ok=True)
    (ds / "a.md").write_text(body)


def _seed_brief(db_mod, dataset="asurion", insights=None):
    if insights is None:
        insights = [{"title": "Insight A", "subtitle": "behaviour"}]
    payload = {
        "summary_headline": "stub",
        "insights": insights,
        "_schema_version": 1,
    }
    return db_mod.save_brief(
        dataset=dataset, week_label="Week of stub", payload=payload, schema_version=1
    )


def _llm_result(output, model="claude-sonnet-4-6", prompt_version="prd-author-v1"):
    return LLMResult(
        output=output, model=model, prompt_version=prompt_version,
        input_tokens=10, output_tokens=5, cache_read_input_tokens=0,
        cache_creation_input_tokens=0, cost_usd=0.001, latency_ms=5,
        stop_reason="end_turn",
    )


# Realistic single-half outputs the two concurrent calls each return.
_PART_A = (
    "# Surface — Ship the thing\n\n"
    "# Part A — Product Requirements Document (human-readable)\n"
    "## 1. Problem & evidence\nUsers can't X.\n"
)
_PART_B = (
    "# Part B — Implementation Spec (LLM-readable / agent-executable)\n"
    "## B0. Available artifacts\nWHEN x THE SYSTEM SHALL y.\n"
)


def _two_call_mock(part_a=_PART_A, part_b=_PART_B, captured=None):
    """A `llm_call` stub that returns Part A or Part B based on the call's
    `purpose`, recording each call's kwargs into `captured` (a list)."""
    captured = [] if captured is None else captured

    def _call(**kwargs):
        captured.append(kwargs)
        if kwargs.get("purpose") == "generate_prd_part_b":
            return _llm_result(part_b)
        return _llm_result(part_a)

    return _call, captured


def _start_prd(db_mod, brief_id, title="t", insight_index=0):
    return db_mod.start_prd(
        brief_id=brief_id, insight_index=insight_index, title=title,
        template_version=1, variant="v2",
    )


# ── two-call structure ───────────────────────────────────────────────────

def test_run_sync_makes_two_separate_llm_calls(isolated_settings, monkeypatch):
    """Part A and Part B are produced by TWO distinct gateway invocations."""
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod)
    prd_id = _start_prd(db_mod, brief_id)

    call, captured = _two_call_mock()
    monkeypatch.setattr(prd_runner, "llm_call", call)
    prd_runner._run_sync(prd_id, brief_id, 0)

    assert len(captured) == 2
    purposes = {c["purpose"] for c in captured}
    assert purposes == {"generate_prd_part_a", "generate_prd_part_b"}


def test_part_a_binds_prd_author_part_b_binds_impl_spec(isolated_settings, monkeypatch):
    """Part A binds skill='prd-author'; Part B binds skill='implementation-spec'
    — the dedicated spec skill. Both calls are the 'prd' agent."""
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod)
    prd_id = _start_prd(db_mod, brief_id)

    call, captured = _two_call_mock()
    monkeypatch.setattr(prd_runner, "llm_call", call)
    prd_runner._run_sync(prd_id, brief_id, 0)

    by_purpose = {c["purpose"]: c for c in captured}
    assert by_purpose["generate_prd_part_a"]["skill"] == "prd-author"
    assert by_purpose["generate_prd_part_b"]["skill"] == "implementation-spec"
    assert all(c["agent"] == "prd" for c in captured)


def test_part_a_directed_part_b_fed_finished_part_a(isolated_settings, monkeypatch):
    """Part A is steered to the human-PRD half via its directive. Part B is NOT
    directive-steered — it is the implementation-spec skill fed the FINISHED
    Part A as its input (the chaining contract)."""
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod)
    prd_id = _start_prd(db_mod, brief_id)

    call, captured = _two_call_mock()
    monkeypatch.setattr(prd_runner, "llm_call", call)
    prd_runner._run_sync(prd_id, brief_id, 0)

    by_purpose = {c["purpose"]: c for c in captured}
    a_input = by_purpose["generate_prd_part_a"]["input"]
    b_input = by_purpose["generate_prd_part_b"]["input"]
    # Part-A call directs ONLY Part A and forbids the separator.
    assert "ONLY Part A" in a_input
    assert "do NOT emit the `---`" in a_input
    # Part-B is fed the finished human PRD (Part A's output text), not a directive.
    assert "HUMAN PRD (Part A" in b_input
    assert "Ship the thing" in b_input  # Part A's content (from _PART_A) flows in
    assert "ONLY Part B" not in b_input


def test_part_a_carries_the_rich_block_contract(isolated_settings, monkeypatch):
    """Part A (the human PRD the user reads) is generated against the typed
    `:::`-block contract (data/sprntly_prd_template.md), so its output renders
    as first-class components instead of degrading to a raw markdown doc. Lock
    the full block vocabulary + the no-degrade directive into the Part-A prompt."""
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod)
    prd_id = _start_prd(db_mod, brief_id)

    call, captured = _two_call_mock()
    monkeypatch.setattr(prd_runner, "llm_call", call)
    prd_runner._run_sync(prd_id, brief_id, 0)

    a_input = {c["purpose"]: c for c in captured}["generate_prd_part_a"]["input"]
    for block in (
        ":::context-chip", ":::tldr", ":::problem", ":::hypothesis",
        ":::requirements", ":::acceptance-criteria", ":::metrics", ":::risks",
        ":::milestones", ":::dod",
    ):
        assert block in a_input, f"Part A prompt missing {block} contract"
    assert "Emit every named block EXACTLY" in a_input


def test_part_b_derives_from_part_a_and_shared_evidence(isolated_settings, monkeypatch):
    """Coherence: Part A receives the insight + grounding; Part B derives from
    the finished Part A and the SAME evidence, so the two halves stay aligned."""
    _seed_corpus(isolated_settings["data_dir"], body="UNIQUE_CORPUS_MARK")
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(
        db_mod, insights=[{"title": "Insight A", "subtitle": "UNIQUE_INSIGHT_MARK"}]
    )
    prd_id = _start_prd(db_mod, brief_id)

    call, captured = _two_call_mock()
    monkeypatch.setattr(prd_runner, "llm_call", call)
    prd_runner._run_sync(prd_id, brief_id, 0)

    by_purpose = {c["purpose"]: c for c in captured}
    a_input = by_purpose["generate_prd_part_a"]["input"]
    b_input = by_purpose["generate_prd_part_b"]["input"]
    # Part A gets the insight directly.
    assert "UNIQUE_INSIGHT_MARK" in a_input
    # Both share the same evidence grounding.
    assert "UNIQUE_CORPUS_MARK" in a_input
    assert "UNIQUE_CORPUS_MARK" in b_input
    # Part B derives from the finished Part A (its output text flows in).
    assert "Part A — Product Requirements Document" in b_input


# ── chaining ─────────────────────────────────────────────────────────────

def test_part_b_runs_after_part_a_chained(isolated_settings, monkeypatch):
    """Part B runs AFTER Part A and consumes its output: the calls are ordered
    (A then B) and Part B's prompt carries Part A's finished text."""
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod)
    prd_id = _start_prd(db_mod, brief_id)

    order: list[str] = []

    def _call(**kwargs):
        order.append(kwargs["purpose"])
        if kwargs.get("purpose") == "generate_prd_part_b":
            # Part B must be fed the finished Part A text.
            assert "Ship the thing" in kwargs["input"]
            return _llm_result(_PART_B)
        return _llm_result(_PART_A)

    monkeypatch.setattr(prd_runner, "llm_call", _call)
    prd_runner._run_sync(prd_id, brief_id, 0)

    # Strict order: Part A completes before Part B is issued.
    assert order == ["generate_prd_part_a", "generate_prd_part_b"]
    row = db_mod.get_prd(prd_id)
    assert row["status"] == "ready"


# ── assembly + storage ───────────────────────────────────────────────────

def test_run_sync_stores_both_parts(isolated_settings, monkeypatch):
    """Part A (human) → payload_md; Part B (LLM) → llm_part column."""
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod)
    prd_id = _start_prd(db_mod, brief_id)

    call, _ = _two_call_mock()
    monkeypatch.setattr(prd_runner, "llm_call", call)
    prd_runner._run_sync(prd_id, brief_id, 0)

    row = db_mod.get_prd(prd_id)
    assert row["status"] == "ready"
    assert "Part A — Product Requirements Document" in row["payload_md"]
    assert "Users can't X." in row["payload_md"]
    # Part B is stored separately, not in the human-rendered payload.
    assert "Part B — Implementation Spec" not in row["payload_md"]
    assert "Part B — Implementation Spec" in row["llm_part"]
    assert "WHEN x THE SYSTEM SHALL y." in row["llm_part"]


def test_run_sync_part_a_renders_as_before(isolated_settings, monkeypatch):
    """Frontend-compat: payload_md is the human PRD only, no separator artifacts;
    get_prd_rendered returns the same."""
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod)
    prd_id = _start_prd(db_mod, brief_id)

    call, _ = _two_call_mock()
    monkeypatch.setattr(prd_runner, "llm_call", call)
    prd_runner._run_sync(prd_id, brief_id, 0)

    payload = db_mod.get_prd(prd_id)["payload_md"]
    assert payload.startswith("# Surface")
    assert not payload.rstrip().endswith("---")
    rendered = db_mod.get_prd_rendered(prd_id)
    assert rendered["payload_md"] == payload


def test_run_sync_uses_fallback_title(isolated_settings, monkeypatch):
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod, insights=[{}])
    prd_id = _start_prd(db_mod, brief_id, title="placeholder")

    call, _ = _two_call_mock()
    monkeypatch.setattr(prd_runner, "llm_call", call)
    prd_runner._run_sync(prd_id, brief_id, 0)
    assert db_mod.get_prd(prd_id)["title"] == "Insight #1"


# ── Part-B degenerate / failure resilience ───────────────────────────────

def test_part_b_empty_still_renders_payload(isolated_settings, monkeypatch):
    """Part B comes back empty → payload_md (Part A) still renders, llm_part
    empty, PRD ready — mirrors the old degenerate-output resilience."""
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod)
    prd_id = _start_prd(db_mod, brief_id)

    call, _ = _two_call_mock(part_b="")
    monkeypatch.setattr(prd_runner, "llm_call", call)
    prd_runner._run_sync(prd_id, brief_id, 0)

    row = db_mod.get_prd(prd_id)
    assert row["status"] == "ready"
    assert "Users can't X." in row["payload_md"]
    assert (row["llm_part"] or "") == ""


def test_part_b_call_failure_completes_with_part_a(isolated_settings, monkeypatch):
    """Part B CALL raises → PRD STILL completes with Part A + empty llm_part
    (not failed) — prefer completion over hard-failing the whole PRD."""
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod)
    prd_id = _start_prd(db_mod, brief_id)

    def _call(**kwargs):
        if kwargs.get("purpose") == "generate_prd_part_b":
            raise RuntimeError("part B exploded")
        return _llm_result(_PART_A)

    monkeypatch.setattr(prd_runner, "llm_call", _call)
    prd_runner._run_sync(prd_id, brief_id, 0)

    row = db_mod.get_prd(prd_id)
    assert row["status"] == "ready"
    assert "Users can't X." in row["payload_md"]
    assert (row["llm_part"] or "") == ""


def test_part_b_failure_is_logged_not_silent(isolated_settings, monkeypatch, caplog):
    """The Part-B failure must be LOGGED (audit), never silently dropped."""
    import logging
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod)
    prd_id = _start_prd(db_mod, brief_id)

    def _call(**kwargs):
        if kwargs.get("purpose") == "generate_prd_part_b":
            raise RuntimeError("part B exploded")
        return _llm_result(_PART_A)

    monkeypatch.setattr(prd_runner, "llm_call", _call)
    with caplog.at_level(logging.ERROR, logger="app.prd_runner"):
        prd_runner._run_sync(prd_id, brief_id, 0)

    assert any("Part B" in r.message and "exploded" in r.message
               for r in caplog.records)


def test_part_a_failure_fails_whole_prd(isolated_settings, monkeypatch):
    """Part A is required: if its call fails, the WHOLE PRD fails (not a
    half-complete row)."""
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod)
    prd_id = _start_prd(db_mod, brief_id)

    def _call(**kwargs):
        if kwargs.get("purpose") == "generate_prd_part_a":
            raise RuntimeError("part A exploded")
        return _llm_result(_PART_B)

    monkeypatch.setattr(prd_runner, "llm_call", _call)
    asyncio.run(prd_runner.generate_prd(prd_id, brief_id, 0))

    row = db_mod.get_prd(prd_id)
    assert row["status"] == "failed"
    assert "part A exploded" in (row["error"] or "")


# ── decision-log + version pin ───────────────────────────────────────────

def test_decision_log_pins_skill_hash_and_has_llm_part(isolated_settings, monkeypatch):
    """The generate_prd decision row pins prompt_version (+prd-author@<hash>),
    sets has_llm_part=True when Part B was produced."""
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod)
    prd_id = _start_prd(db_mod, brief_id)

    from app.skills.loader import get_skill
    skill_hash = get_skill("prd-author").content_hash

    # Let the REAL gateway run; patch only the model seam (call_md) so the
    # gateway computes the `+prd-author@<hash>` prompt_version itself.
    import app.graph.gateway as gw

    def _call_md(**kw):
        # The gateway folds the per-part directive into the system prompt; emit
        # the matching half so both columns populate.
        if "ONLY Part B" in kw.get("system", "") or "Part B" in kw.get("user", ""):
            return _PART_B
        return _PART_A

    monkeypatch.setattr(gw, "call_md", _call_md)
    monkeypatch.setattr(prd_runner, "company_id_for_slug", lambda _slug: "co-test")

    prd_runner._run_sync(prd_id, brief_id, 0)

    sup = isolated_settings["supabase"]
    rows = sup.table("agent_decision_log").select("*").execute().data
    gen = [r for r in rows if r["decision_type"] == "generate_prd"]
    assert len(gen) == 1
    pv = gen[0]["prompt_version"]
    assert "prd-author" in pv
    assert skill_hash in pv
    assert gen[0]["factors"]["has_llm_part"] is True
    assert gen[0]["factors"]["part_b_error"] is None
    # Two llm_call telemetry rows (one per part) are logged by the gateway.
    assert sum(1 for r in rows if r["decision_type"] == "llm_call") == 2


def test_decision_log_has_llm_part_false_on_empty_b(isolated_settings, monkeypatch):
    """has_llm_part is accurate: False when Part B was empty."""
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod)
    prd_id = _start_prd(db_mod, brief_id)

    call, _ = _two_call_mock(part_b="")
    monkeypatch.setattr(prd_runner, "llm_call", call)
    monkeypatch.setattr(prd_runner, "company_id_for_slug", lambda _slug: "co-test")
    prd_runner._run_sync(prd_id, brief_id, 0)

    sup = isolated_settings["supabase"]
    rows = sup.table("agent_decision_log").select("*").execute().data
    gen = [r for r in rows if r["decision_type"] == "generate_prd"]
    assert len(gen) == 1
    assert gen[0]["factors"]["has_llm_part"] is False


def test_decision_log_records_part_b_error(isolated_settings, monkeypatch):
    """When Part B's call failed, the decision row records the error (audit),
    has_llm_part=False, and the PRD still completed."""
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod)
    prd_id = _start_prd(db_mod, brief_id)

    def _call(**kwargs):
        if kwargs.get("purpose") == "generate_prd_part_b":
            raise RuntimeError("part B exploded")
        return _llm_result(_PART_A)

    monkeypatch.setattr(prd_runner, "llm_call", _call)
    monkeypatch.setattr(prd_runner, "company_id_for_slug", lambda _slug: "co-test")
    prd_runner._run_sync(prd_id, brief_id, 0)

    sup = isolated_settings["supabase"]
    rows = sup.table("agent_decision_log").select("*").execute().data
    gen = [r for r in rows if r["decision_type"] == "generate_prd"]
    assert len(gen) == 1
    assert "part B exploded" in (gen[0]["factors"]["part_b_error"] or "")
    assert gen[0]["factors"]["has_llm_part"] is False


def test_real_gateway_prepends_each_halfs_method(isolated_settings, monkeypatch):
    """End-to-end through the real gateway: Part A's prompt carries the
    prd-author METHOD; Part B's carries the implementation-spec METHOD."""
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod)
    prd_id = _start_prd(db_mod, brief_id)

    systems: dict[str, str] = {}

    def _call_md(**kwargs):
        # Part B's user prompt is fed the finished human PRD.
        if "HUMAN PRD (Part A" in kwargs["user"]:
            systems["b"] = kwargs["system"]
            return _PART_B
        systems["a"] = kwargs["system"]
        return _PART_A

    import app.graph.gateway as gw
    monkeypatch.setattr(gw, "call_md", _call_md)
    prd_runner._run_sync(prd_id, brief_id, 0)

    assert set(systems) == {"a", "b"}
    # Part A: prd-author METHOD + the PRD agent layer after it.
    assert "METHOD (skill: prd-author" in systems["a"]
    assert "Sprntly's PRD agent" in systems["a"]
    # Part B: the implementation-spec METHOD + the spec agent layer.
    assert "METHOD (skill: implementation-spec" in systems["b"]
    assert "Sprntly's Implementation Spec agent" in systems["b"]

    row = db_mod.get_prd(prd_id)
    assert "Part A" in row["payload_md"]
    assert "Part B" in row["llm_part"]


# ── back-compat: _split_2part retained ───────────────────────────────────

def test_split_2part_still_available_for_backcompat(isolated_settings):
    """_split_2part is kept for any caller still holding a combined document."""
    combined = _PART_A + "\n---\n" + _PART_B
    part_a, part_b = prd_runner._split_2part(combined)
    assert "Part A — Product Requirements Document" in part_a
    assert "Part B — Implementation Spec" in part_b


def test_split_2part_degenerate_single_part(isolated_settings):
    part_a, part_b = prd_runner._split_2part("# Just a PRD\nbody only")
    assert part_a == "# Just a PRD\nbody only"
    assert part_b == ""


# ── format/style exemplars (company gold-standard templates) ─────────────

def _wire_templates(isolated_settings, monkeypatch, company_id="co-tpl"):
    """Point company_template storage at the fake db, seed a company, and make
    the runner resolve the brief's slug to that company."""
    db = isolated_settings["supabase"]
    if not db.table("companies").select("id").eq("id", company_id).execute().data:
        db.table("companies").insert(
            {"id": company_id, "slug": "asurion", "display_name": "Co"}
        ).execute()
    import app.company_template as ct
    monkeypatch.setattr(ct, "require_client", lambda: db)
    monkeypatch.setattr(prd_runner, "company_id_for_slug", lambda _slug: company_id)
    return ct


def test_templates_reach_both_compose_calls(isolated_settings, monkeypatch):
    """A company's uploaded gold-standard templates are folded into BOTH
    part-calls as a FORMAT/STYLE EXEMPLARS block so the PRD matches house
    structure & voice."""
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod)
    prd_id = _start_prd(db_mod, brief_id)

    ct = _wire_templates(isolated_settings, monkeypatch)
    ct.save_company_template(
        "co-tpl", filename="gold.md", data=b"GOLD_TEMPLATE_MARK", label="House style"
    )

    call, captured = _two_call_mock()
    monkeypatch.setattr(prd_runner, "llm_call", call)
    prd_runner._run_sync(prd_id, brief_id, 0)

    assert len(captured) == 2
    for c in captured:
        assert "FORMAT/STYLE EXEMPLARS" in c["input"]
        assert "GOLD_TEMPLATE_MARK" in c["input"]
        assert "House style" in c["input"]
        # additive: the structural template + insight are still present
        assert "Part B" in c["input"]


def test_no_templates_is_clean_no_op(isolated_settings, monkeypatch):
    """No templates uploaded ⇒ no exemplars block in either compose call
    (additive context only; absence is a clean no-op)."""
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod)
    prd_id = _start_prd(db_mod, brief_id)

    _wire_templates(isolated_settings, monkeypatch)  # company exists, but no templates

    call, captured = _two_call_mock()
    monkeypatch.setattr(prd_runner, "llm_call", call)
    prd_runner._run_sync(prd_id, brief_id, 0)

    for c in captured:
        assert "FORMAT/STYLE EXEMPLARS" not in c["input"]
    # PRD still completes normally.
    assert db_mod.get_prd(prd_id)["status"] == "ready"


def test_template_lookup_failure_does_not_break_prd(isolated_settings, monkeypatch):
    """A templates read error degrades to no exemplars — never fails the PRD."""
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod)
    prd_id = _start_prd(db_mod, brief_id)

    monkeypatch.setattr(prd_runner, "company_id_for_slug", lambda _slug: "co-x")

    def _boom(*_a, **_k):
        raise RuntimeError("templates backend down")

    monkeypatch.setattr(prd_runner, "render_templates_for_prompt", _boom)

    call, captured = _two_call_mock()
    monkeypatch.setattr(prd_runner, "llm_call", call)
    prd_runner._run_sync(prd_id, brief_id, 0)

    assert db_mod.get_prd(prd_id)["status"] == "ready"
    for c in captured:
        assert "FORMAT/STYLE EXEMPLARS" not in c["input"]


# ── error paths (unchanged contract) ─────────────────────────────────────

def test_run_sync_missing_brief_raises(isolated_settings):
    with pytest.raises(RuntimeError):
        prd_runner._run_sync(1, brief_id=9999, insight_index=0)


def test_run_sync_out_of_range_insight_raises(isolated_settings):
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod, insights=[{"title": "only-one"}])
    with pytest.raises(RuntimeError):
        prd_runner._run_sync(1, brief_id=brief_id, insight_index=5)


def test_generate_prd_records_failure_in_db(isolated_settings, monkeypatch):
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod)
    prd_id = _start_prd(db_mod, brief_id)

    def _boom(**_kw):
        raise ValueError("LLM exploded")

    monkeypatch.setattr(prd_runner, "llm_call", _boom)
    asyncio.run(prd_runner.generate_prd(prd_id, brief_id, 0))

    row = db_mod.get_prd(prd_id)
    assert row["status"] == "failed"
    assert "ValueError" in (row["error"] or "")
