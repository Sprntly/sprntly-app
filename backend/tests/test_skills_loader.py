"""Tests for the vendored PM Agent Skills: loader, gateway binding, agent
bindings, and the ported prioritization scoring math."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.skills.loader import (
    SKILLS_ROOT,
    SkillSpec,
    UnknownSkillError,
    get_skill,
    list_skills,
)

# The nine skills a live pipeline binds by name — the whole vendored library.
# Documented in skills/README.md with the call site for each.
BOUND_SKILLS = [
    "prd-author",
    "implementation-spec",
    "evidence-brief",
    "user-stories",
    "top-insights",
    "jira-extraction",
    "hubspot-extraction",
    "clickup-extraction",
    "roadmap-extraction",
    # Vendored 2026-08-20 for the scheduled report engines. Adding them also
    # gave four RESEARCH call sites a real method for the first time — see
    # the two binding tests below, which used to assert the opposite.
    "competitive-intelligence-review",
    "public-feedback-report",
]


# ---------- loader ----------

def test_lists_all_vendored_skills():
    """A CLOSED set, not a floor.

    This asserted `expected.issubset(ids)` over a nine-name sample of a
    ~78-skill tree, because skills were dropped in as folders. The library is
    the keep-list now, and equality is what makes an accidental re-vendoring
    (or a deletion) fail loudly."""
    assert set(list_skills()) == set(BOUND_SKILLS)


@pytest.mark.parametrize("skill_id", BOUND_SKILLS)
def test_loads_each_vendored_skill(skill_id):
    spec = get_skill(skill_id)
    assert isinstance(spec, SkillSpec)
    assert spec.id == skill_id
    assert spec.method.strip(), "SKILL.md must be non-empty"
    # 12 hex chars, stable across calls (lru cache returns the same object).
    assert len(spec.content_hash) == 12
    int(spec.content_hash, 16)  # is hex
    assert get_skill(skill_id) is spec


def test_content_hash_is_stable_and_distinct():
    a = get_skill("user-stories").content_hash
    b = get_skill("user-stories").content_hash
    assert a == b
    assert a != get_skill("top-insights").content_hash


def test_content_hash_recomputes_from_disk(tmp_path, monkeypatch):
    """Editing any file under the skill dir changes the hash (cache-bypassing
    fresh load to prove the hash is content-derived, not a constant)."""
    import app.skills.loader as loader

    skill_dir = tmp_path / "demo"
    (skill_dir).mkdir()
    (skill_dir / "SKILL.md").write_text("method one", encoding="utf-8")
    monkeypatch.setattr(loader, "SKILLS_ROOT", tmp_path)
    loader.get_skill.cache_clear()
    h1 = loader.get_skill("demo").content_hash

    (skill_dir / "SKILL.md").write_text("method two — changed", encoding="utf-8")
    loader.get_skill.cache_clear()
    h2 = loader.get_skill("demo").content_hash
    assert h1 != h2


def test_templates_loaded():
    """`templates/` still loads. `modules/` no longer has a vendored example:
    the only skill that shipped one was competitive-intelligence-review, whose
    directory went with the built-in skill layer. The loader's `modules` support
    stays (a company's uploaded skill can carry them, and
    `test_gateway_unknown_module_raises` still covers the lookup) — there is
    simply nothing on disk to read it from."""
    spec = get_skill("implementation-spec")
    assert spec.templates
    assert spec.modules == {}


# ---------- CIR vendoring: REMOVED ----------
# Five tests here pinned `competitive-intelligence-review`'s vendored tree — its
# v2 module sequence (bound BY FILENAME from `research/competitor.py`), its v3
# frontmatter, its references, its example and its README. The skill is not
# vendored any more. `research/competitor.py` still names the modules and still
# passes `skill=CIR_SKILL`; both `gateway._build_method_prefix` and
# `llm.call_with_web_search` answer a missing directory with an empty method
# block, so the staged deep-dive keeps running WITHOUT its method text. That is
# the accepted degradation, and it is covered by
# `test_gateway_missing_skill_runs_method_less` below rather than by asserting
# on files that no longer exist.


def test_references_and_assets_loaded():
    """The top-insights skill's `references/*` (schema, rubric, examples) and
    `assets/*` (the render template) are read into the SkillSpec so the gateway
    can fold the references into the cacheable METHOD prefix."""
    wb = get_skill("top-insights")
    assert set(wb.references) == {
        "signal-schema.json", "rubric.md", "examples.md", "sources.md"
    }
    assert "top-insights schemas" in wb.references["signal-schema.json"]
    assert "Deterministic linters" in wb.references["rubric.md"]
    assert "golden reference" in wb.references["examples.md"]
    # assets are loaded (for fingerprinting/inspection) but stay OUT of the prompt.
    assert "brief-template.html" in wb.assets
    assert "<!DOCTYPE html>" in wb.assets["brief-template.html"]


def test_skill_without_references_has_empty_dicts():
    """A skill with no references/ or assets/ dir loads empty dicts (so the
    gateway's reference-injection is a no-op for every other skill)."""
    spec = get_skill("implementation-spec")
    assert spec.references == {}
    assert spec.assets == {}


# ---------- frontmatter block scalars ----------

def test_folded_block_scalar_description_parses_to_full_text():
    """`description: >` must yield the whole folded paragraph, not ">".

    The parser is a no-YAML-dep line splitter doing `partition(":")`, so a folded
    block scalar captured only the ">" marker. The router classifies against
    `description`, so prd-author reached the menu as `- prd-author: >` with zero
    semantic signal.
    """
    from app.skills.loader import _parse_frontmatter

    fm = _parse_frontmatter(
        "---\n"
        "name: demo\n"
        "description: >\n"
        "  Author the human-readable half of a PRD from a problem,\n"
        "  signals, and business context.\n"
        "\n"
        "  A second paragraph stays separate.\n"
        "kind: method\n"
        "---\n\nbody\n"
    )
    assert fm["name"] == "demo"
    # Folded: lines join with a single space, blank line = paragraph break.
    assert fm["description"] == (
        "Author the human-readable half of a PRD from a problem, signals, "
        "and business context.\nA second paragraph stays separate."
    )
    # The block must not swallow the key that follows it.
    assert fm["kind"] == "method"


def test_literal_block_scalar_keeps_its_newlines():
    """`description: |` is the literal form — every newline survives, and the
    block's own indentation is stripped while deeper nesting is kept."""
    from app.skills.loader import _parse_frontmatter

    fm = _parse_frontmatter(
        "---\n"
        "description: |\n"
        "  line one\n"
        "    indented two\n"
        "  line three\n"
        "name: demo\n"
        "---\n"
    )
    assert fm["description"] == "line one\n  indented two\nline three"
    assert fm["name"] == "demo"


def test_plain_single_line_frontmatter_is_unchanged():
    """The flat form every other vendored skill uses must parse exactly as
    before — this fix adds a case, it does not change the existing one."""
    from app.skills.loader import _parse_frontmatter

    fm = _parse_frontmatter("---\nname: demo\ndescription: One flat line.\n---\n")
    assert fm == {"name": "demo", "description": "One flat line."}


def test_block_scalar_indicators_are_tolerated():
    """Chomping/indent indicators (`>-`, `|+`) still open a block scalar rather
    than being captured as the literal value."""
    from app.skills.loader import _parse_frontmatter

    assert _parse_frontmatter(
        "---\ndescription: >-\n  folded and chomped\n---\n"
    )["description"] == "folded and chomped"
    assert _parse_frontmatter(
        "---\ndescription: |+\n  literal and kept\n---\n"
    )["description"] == "literal and kept"


def test_prd_author_description_is_the_real_summary():
    """The regression this fix exists for: prd-author's description was the
    single character ">". It is loaded from disk, so this also proves the fix
    works against the real vendored file rather than only a fixture."""
    desc = get_skill("prd-author").description
    assert desc != ">"
    assert len(desc) > 60
    assert "Product Requirements Document" in desc
    assert "Part A" in desc
    # Folded to one flowing line, not a ragged column of source fragments.
    assert "\n" not in desc


def test_unknown_skill_raises():
    with pytest.raises(UnknownSkillError):
        get_skill("does-not-exist")


def test_skills_root_exists():
    assert (SKILLS_ROOT / "prd-author" / "SKILL.md").is_file()


# ---------- gateway binding ----------

def _msg(text="ok"):
    """Mirror tests/test_gateway_config._msg — a fake Anthropic message."""
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=10, output_tokens=5,
                              cache_creation_input_tokens=0,
                              cache_read_input_tokens=2),
        stop_reason="end_turn",
    )


def _tool_msg(payload=None):
    """A fake Anthropic message carrying a submit_response tool_use block
    (what call_json's schema path expects)."""
    return SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", name="submit_response",
                                 input=payload or {})],
        usage=SimpleNamespace(input_tokens=10, output_tokens=5,
                              cache_creation_input_tokens=0,
                              cache_read_input_tokens=2),
        stop_reason="tool_use",
    )


def _capture_client(captured: dict):
    """A fake Anthropic client that records the kwargs of messages.create AND
    messages.stream (call_with_web_search streams on the long timeout).

    Returns a tool_use response when a schema/tools call is made (json_schema
    path), else a plain text response (call_md path)."""
    def _reply(kw):
        return _tool_msg() if kw.get("tools") else _msg("done")

    def _create(**kw):
        captured.update(kw)
        return _reply(kw)

    class _FakeStream:
        def __init__(self, kw):
            self._kw = kw

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        @property
        def text_stream(self):
            return iter(())

        def get_final_message(self):
            return _reply(self._kw)

    def _stream(**kw):
        captured.update(kw)
        return _FakeStream(kw)

    return SimpleNamespace(messages=SimpleNamespace(create=_create, stream=_stream))


def test_gateway_skill_prepends_method_to_cacheable_prefix(isolated_settings, monkeypatch):
    from app import llm
    from app.graph.gateway import llm_call

    captured: dict = {}
    monkeypatch.setattr(llm, "get_client", lambda: _capture_client(captured))

    spec = get_skill("user-stories")
    r = llm_call(
        enterprise_id="ent-A", agent="synthesis", purpose="rank",
        prompt_version="synth-v1", system="agent system layer", input="candidates",
        json_schema={"type": "object", "properties": {}, "required": []},
        skill="user-stories",
    )
    # json_schema path → method rides the cacheable user prefix (first block).
    user_content = captured["messages"][0]["content"]
    assert isinstance(user_content, list)
    prefix_text = user_content[0]["text"]
    assert prefix_text.startswith(f"## METHOD (skill: user-stories @{spec.content_hash})")
    assert "cache_control" in user_content[0]
    # prompt_version is pinned to the exact method version.
    assert r.prompt_version == f"synth-v1+user-stories@{spec.content_hash}"


def test_gateway_skill_module_appended(isolated_settings, monkeypatch):
    """Module injection still works — exercised through an INJECTED spec.

    It used to bind `competitive-intelligence-review` + `00-scope.md` off disk;
    that skill is no longer vendored and no keeper ships `modules/`. The
    mechanism is unchanged and still reachable (a company's uploaded skill can
    carry modules), so the test moved to the path where modules now live rather
    than being deleted with the directory."""
    from app import llm
    from app.graph.gateway import llm_call

    captured: dict = {}
    monkeypatch.setattr(llm, "get_client", lambda: _capture_client(captured))

    spec = SkillSpec(id="uploaded", method="# uploaded method",
                     modules={"00-scope.md": "scope stage text"},
                     content_hash="abc123abc123")
    llm_call(
        enterprise_id="ent-A", agent="competitor_analysis", purpose="x",
        prompt_version="v1", system="sys", input="u",
        json_schema={"type": "object", "properties": {}, "required": []},
        skill="uploaded", skill_module="00-scope.md", skill_spec=spec,
    )
    prefix_text = captured["messages"][0]["content"][0]["text"]
    assert "## METHOD (skill: uploaded" in prefix_text
    assert "### MODULE: 00-scope.md" in prefix_text
    assert "scope stage text" in prefix_text


def test_gateway_method_prefix_includes_skill_references(isolated_settings):
    """A bound skill's `references/*` ride the cacheable METHOD block under
    `### REFERENCE:` headers, so the model has the schema + rubric + examples
    in-prompt and can run SKILL.md's full documented workflow (incl. the step-6
    self-critique against the rubric). The `assets/*` render template stays OUT."""
    from app.graph.gateway import _build_method_prefix

    block, suffix = _build_method_prefix("top-insights", None)
    spec = get_skill("top-insights")
    assert block.startswith(f"## METHOD (skill: top-insights @{spec.content_hash})")
    # all three reference docs are folded in, each under its own header.
    assert "### REFERENCE: signal-schema.json" in block
    assert "### REFERENCE: rubric.md" in block
    assert "### REFERENCE: examples.md" in block
    # ...and their actual content (not just the header) is present.
    assert "top-insights schemas" in block      # schema
    assert "Deterministic linters" in block               # rubric hard gates
    assert "golden reference" in block                    # examples
    # the 247-line HTML render template is NOT injected (app renders from the
    # structured payload; the template is a downstream view, not a prompt input).
    assert "<!DOCTYPE html>" not in block
    assert "### REFERENCE: brief-template.html" not in block
    assert suffix == f"+top-insights@{spec.content_hash}"


def test_gateway_method_prefix_no_references_unchanged(isolated_settings):
    """A skill with no references/ dir produces a method block with no REFERENCE
    section — every other bound skill's prompt is byte-identical to before."""
    from app.graph.gateway import _build_method_prefix

    block, _ = _build_method_prefix("implementation-spec", None)
    assert "### REFERENCE:" not in block


def test_gateway_missing_skill_runs_method_less(isolated_settings, monkeypatch):
    """The gateway DEGRADES on an unvendored id — it used to raise.

    This is the load-bearing change behind the whole trim, and it is a
    deliberate inversion of `test_gateway_unknown_skill_raises`. A dozen-odd
    pipelines still pass `skill=<id>` at their call site — that binding is how
    the decision log attributes the call — and several of those ids no longer
    name a vendored skill. Raising here turned "this pipeline has no method
    doc" into a 500 for a pipeline perfectly able to run on its own prompt:
    trimming the library WITHOUT this produced 172 test failures, 28 of them a
    single UnknownSkillError inside company_research.

    `+bare` in prompt_version is what keeps the audit spine honest — a
    method-less run stays distinguishable from a method-backed one.
    """
    from app import llm
    from app.graph.gateway import _build_method_prefix, llm_call

    block, suffix = _build_method_prefix("nope", None)
    assert block == ""
    assert suffix == "+bare"

    captured: dict = {}
    monkeypatch.setattr(llm, "get_client", lambda: _capture_client(captured))
    r = llm_call(
        enterprise_id="ent-A", agent="x", purpose="x",
        prompt_version="v1", system="s", input="u", skill="nope",
    )
    assert r.prompt_version == "v1+bare"
    # No method block means NO cacheable prefix at all — an empty one would be
    # emitted as an empty cache-controlled block.
    assert captured["messages"][0]["content"] == "u"


def test_gateway_missing_skill_still_raises_for_the_loader(isolated_settings):
    """The tolerance is the GATEWAY's, not the loader's.

    `get_skill` must keep raising: the nine remaining skills are bound by name
    from pipelines that read `templates`/`assets` off the spec directly
    (prd_runner, evidence_kg), and a silently-empty method there would be worse
    than a stack trace."""
    with pytest.raises(UnknownSkillError):
        get_skill("nope")


def test_gateway_unknown_module_raises(isolated_settings):
    """A module missing from a spec that DOES exist is a caller bug, not a
    vendoring decision, and still raises.

    Exercised through an injected spec because no vendored skill ships
    `modules/` any more — the injection path is also where modules actually
    occur now (a company's uploaded skill)."""
    from app.graph.gateway import _build_method_prefix

    spec = SkillSpec(id="uploaded", method="# m", modules={"01-a.md": "x"},
                     content_hash="abc123abc123")
    with pytest.raises(KeyError):
        _build_method_prefix("uploaded", "no-such-module.md", spec=spec)


def test_gateway_no_skill_is_unchanged(isolated_settings, monkeypatch):
    """Without skill=, prompt_version + content shape are untouched."""
    from app import llm
    from app.graph.gateway import llm_call

    captured: dict = {}
    monkeypatch.setattr(llm, "get_client", lambda: _capture_client(captured))
    r = llm_call(
        enterprise_id="ent-A", agent="x", purpose="x",
        prompt_version="v1", system="s", input="u",
    )
    assert r.prompt_version == "v1"
    # plain str content (no cacheable prefix injected).
    assert captured["messages"][0]["content"] == "u"


def test_gateway_md_path_routes_method_to_cacheable_prefix(isolated_settings, monkeypatch):
    """call_md now shares the cacheable-prefix path → the skill METHOD rides the
    user prefix block (a cache read on repeat calls), NOT the system prompt."""
    from app import llm
    from app.graph.gateway import llm_call

    spec = get_skill("implementation-spec")
    captured: dict = {}
    monkeypatch.setattr(llm, "get_client", lambda: _capture_client(captured))
    llm_call(
        enterprise_id="ent-A", agent="x", purpose="x",
        prompt_version="v1", system="AGENT LAYER", input="u",
        skill="implementation-spec",  # no json_schema → call_md
    )
    # Method rides the cacheable user prefix (first content block), cache-marked.
    user_content = captured["messages"][0]["content"]
    assert isinstance(user_content, list)
    prefix_text = user_content[0]["text"]
    assert prefix_text.startswith(f"## METHOD (skill: implementation-spec @{spec.content_hash})")
    assert "cache_control" in user_content[0]
    # The agent layer is the system prompt and no longer carries the METHOD.
    system_sent = captured["system"]
    system_text = system_sent if isinstance(system_sent, str) else system_sent[0]["text"]
    assert "AGENT LAYER" in system_text
    assert "## METHOD" not in system_text


# ---------- agent bindings ----------

def test_synthesis_binds_top_insights(isolated_settings, monkeypatch):
    """The synthesis brief COMPOSITION call binds the `top-insights` skill — its
    METHOD is prepended to the cacheable prefix (re-platformed off `prioritize`,
    which only ever scored the candidates upstream)."""
    from app import llm

    captured: dict = {}
    monkeypatch.setattr(llm, "get_client", lambda: _capture_client(captured))

    # Drive run_synthesis far enough to make the bound llm_call, then stop.
    from app.synthesis import agent as synth
    from app.synthesis.convergence import ThemeConvergence

    # Multi-source so it clears the brief evidence gate and reaches the LLM call
    # (this test asserts skill binding on the prompt, not the gate).
    cand = ThemeConvergence(theme_id="t1", theme_label="Slow checkout")
    cand.signal_count = 2
    cand.source_types = {"customer_voice", "revenue"}
    cand.connected_signal_count = 2
    cand.effective_weight = 0.9
    monkeypatch.setattr(synth, "compute_convergence", lambda f, e: [cand])
    monkeypatch.setattr(synth, "load_kpi_tree", lambda e: None)

    spec = get_skill("top-insights")
    with patch.object(synth, "save_brief"), \
         patch.object(synth, "deliver_brief", return_value={
             "slack": {"delivered": False, "reason": "slack_not_connected"},
             "email": {"delivered": False, "reason": "email_disabled"}}), \
         patch.object(synth, "log_agent_decision"):
        # The fake client returns text, not a tool_use block, so call_json's
        # schema path raises after capturing kwargs — that's enough to assert
        # the binding. Catch and inspect what was sent.
        try:
            synth.run_synthesis(_FakeFacade(), "ent-A", dataset_slug="acme")
        except Exception:
            pass

    prefix_text = captured["messages"][0]["content"][0]["text"]
    assert prefix_text.startswith(f"## METHOD (skill: top-insights @{spec.content_hash})")
    # The skill's reference doc set is now in the compose prompt (cacheable
    # prefix), so the skill can run its full documented workflow: the input/
    # output schema, the rubric's hard gates (step-6 self-critique), and the
    # golden/counter examples are all grounding the single compose generation.
    assert "### REFERENCE: signal-schema.json" in prefix_text
    assert "top-insights schemas" in prefix_text
    assert "### REFERENCE: rubric.md" in prefix_text
    assert "Deterministic linters" in prefix_text
    assert "### REFERENCE: examples.md" in prefix_text
    assert "golden reference" in prefix_text
    # the HTML render template is left out of the prompt on purpose.
    assert "<!DOCTYPE html>" not in prefix_text
    # references ride the SAME cacheable block as the method (one cache_control).
    assert "cache_control" in captured["messages"][0]["content"][0]


def test_oncall_binding_degrades_method_less(isolated_settings, monkeypatch):
    from app import llm

    captured: dict = {}
    monkeypatch.setattr(llm, "get_client", lambda: _capture_client(captured))

    from app.oncall import agent as oncall
    monkeypatch.setattr(oncall, "embed_texts", lambda t, **k: [[0.1] * 4 for _ in t])
    facade = _FakeFacade()
    inc = oncall.IncidentInput(title="Checkout 500s", description="spike of 500s")
    try:
        oncall.investigate_incident(facade, "ent-A", incident=inc)
    except Exception:
        pass

    # The binding SURVIVES; the method does not. `incident-runbook` is not
    # vendored any more, so the gateway runs the call method-less: no METHOD
    # header on the prefix, and `+bare` recorded in prompt_version. The oncall
    # agent's own system prompt is what carries the call.
    assert "## METHOD (skill: incident-runbook" not in str(captured)
    assert captured.get("system")


def test_market_research_binds_the_public_feedback_method(isolated_settings, monkeypatch):
    from app import llm
    from app.research import market

    captured: dict = {}
    monkeypatch.setattr(llm, "get_client", lambda: _capture_client(captured))
    monkeypatch.setattr(
        market, "company_profile",
        lambda eid: {"display_name": "Acme", "product": {"name": "Acme"}},
    )
    monkeypatch.setattr(market, "resolve_config", lambda eid: {"research": {}})
    monkeypatch.setattr(market, "extract_document",
                        lambda *a, **k: {"signals": 0, "themes": 0, "skipped": 0})
    monkeypatch.setattr(market, "log_agent_decision", lambda **k: None)

    market.run_market_research(_FakeFacade(), "ent-A")
    # `run_market_research` binds `skill="public-feedback-report"`, and since
    # that skill was vendored the web-search path folds its method into the
    # system prompt. This test asserted the OPPOSITE while the skill was
    # missing — the binding was then a decision-log label with nothing behind
    # it. Pinned explicitly because the change is silent: vendoring a skill
    # re-methods every call site that names it, including ones that are not
    # the report engine it was written for.
    assert "## METHOD (skill: public-feedback-report" in captured["system"]
    assert captured["system"]


def test_competitor_research_binds_the_cir_method(isolated_settings, monkeypatch):
    from app import llm
    from app.research import competitor as comp

    captured: dict = {}
    monkeypatch.setattr(llm, "get_client", lambda: _capture_client(captured))
    monkeypatch.setattr(comp, "resolve_config",
                        lambda eid: {"resolution": {"tau_high": 0.9}})
    monkeypatch.setattr(comp, "embed_texts", lambda t, **k: [[0.1] * 4 for _ in t])
    monkeypatch.setattr(comp, "extract_document",
                        lambda *a, **k: {"signals": 0, "themes": 0, "skipped": 0})
    monkeypatch.setattr(comp, "log_agent_decision", lambda **k: None)

    comp.run_competitor_research(_FakeFacade(), "ent-A", competitors=["Adobe"])
    # Same as the market-research case: vendoring the skill re-methoded a call
    # site that is NOT the report engine the skill was written for.
    #
    # `research/competitor.py` also passes `skill_module=` per stage
    # ("08-synthesis-decisions.md" and friends), and the vendored skill ships
    # NO modules. That is safe only because this is the web-search path, which
    # tolerates an unknown module; `graph.gateway._build_method_prefix` raises
    # KeyError for one. Pinned here so a future move of these calls onto the
    # gateway fails loudly in a test rather than at runtime.
    assert "## METHOD (skill: competitive-intelligence-review" in captured["system"]
    assert captured["system"]


# ---------- ported scoring ----------
# `app/synthesis/scoring.py` is a PORT of the prioritize skill's scripts into
# first-class app code. The skill directory is gone; this module is not, and it
# is bound from the synthesis pipeline rather than from a chat turn. (Distinct
# from `app/skills/scripts.py`, which ran skill scripts through a tool loop and
# WAS deleted.)

def test_voc_score_known_values():
    from app.synthesis.scoring import voc_score

    # impact*severity*strategic_fit*confidence*trend
    assert voc_score(impact=0.5, severity=0.5) == pytest.approx(0.25)
    assert voc_score(impact=1.0, severity=0.8, strategic_fit=0.5,
                     confidence=0.5, trend=1.0) == pytest.approx(0.2)
    assert voc_score(impact=0.4, severity=0.5, trend=1.2) == pytest.approx(0.24)


def test_norm_conf_percent_and_fraction():
    from app.synthesis.scoring import norm_conf

    assert norm_conf(80) == pytest.approx(0.8)
    assert norm_conf(0.8) == pytest.approx(0.8)
    assert norm_conf(None) == 1.0


def test_fit_value_mapping():
    from app.synthesis.scoring import fit_value

    assert fit_value("high") == 1.0
    assert fit_value("med") == 0.6
    assert fit_value("low") == 0.25
    assert fit_value(0.42) == pytest.approx(0.42)
    assert fit_value(2) == 1.0       # clamp >1
    assert fit_value(-1) == 0.0      # clamp <0
    assert fit_value("garbage") is None


def test_goal_factor_blends_with_weight():
    from app.synthesis.scoring import goal_factor

    assert goal_factor("high") == pytest.approx(1.0)
    assert goal_factor("low") == pytest.approx(0.25)
    # goal_weight=0 → goal ignored.
    assert goal_factor("low", goal_weight=0.0) == 1.0
    # half weight blends toward 1.0: 0.25*0.5 + 0.5 = 0.625.
    assert goal_factor("low", goal_weight=0.5) == pytest.approx(0.625)
    # unknown fit → neutral.
    assert goal_factor(None) == 1.0


def test_convergence_sets_voc_base_score():
    """The convergence base-score path calls the ported voc_score."""
    from app.synthesis.scoring import voc_score
    from app.synthesis.convergence import ThemeConvergence

    tc = ThemeConvergence(theme_id="t", theme_label="x")
    tc.signal_count = 2
    tc.source_types = {"a", "b", "c"}
    tc.effective_weight = 1.0
    tc.competitor_pressure = 1
    # recompute via the same formula the convergence path uses.
    expected = voc_score(
        impact=min(1.0, tc.breadth / 5.0),
        severity=min(1.0, tc.effective_weight / max(tc.signal_count, 1)),
        trend=1.0 + 0.1 * tc.competitor_pressure,
    )
    assert expected == pytest.approx(0.6 * 0.5 * 1.1)


# ---------- minimal fakes ----------

class _FakeFacade:
    """Just enough GraphFacade surface for the agent-binding tests to reach the
    bound llm_call/call_with_web_search without a DB."""

    def find_candidates(self, *a, **k):
        return []

    def load_session_context(self, *a, **k):
        return {}

    def query_entities(self, *a, **k):
        return []

    def create_entity(self, *a, **k):
        return None

    def write_relationship(self, *a, **k):
        return None
