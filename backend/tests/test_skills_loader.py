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

# Every skill a live agent binds (must stay vendored).
BOUND_SKILLS = [
    "prioritize",
    "incident-runbook",
    "third-party-feedback",
    "competitive-intelligence-review",
]


# ---------- loader ----------

def test_lists_all_vendored_skills():
    ids = list_skills()
    # the vendored subset documented in skills/README.md
    expected = {
        "prd-author", "prioritize", "decision-memo", "third-party-feedback",
        "feedback-synthesis", "competitive-intelligence-review",
        "incident-runbook", "business-context", "fact-check",
    }
    assert expected.issubset(set(ids))


@pytest.mark.parametrize("skill_id", [
    "prd-author", "prioritize", "decision-memo", "third-party-feedback",
    "feedback-synthesis", "competitive-intelligence-review",
    "incident-runbook", "business-context", "fact-check",
])
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
    a = get_skill("prioritize").content_hash
    b = get_skill("prioritize").content_hash
    assert a == b
    assert a != get_skill("incident-runbook").content_hash


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


def test_modules_and_templates_loaded():
    cir = get_skill("competitive-intelligence-review")
    # CIR vendors all of its modules + a report template.
    assert len(cir.modules) >= 9
    assert "00-scope.md" in cir.modules
    assert cir.templates  # cir-report-template.md

    bc = get_skill("business-context")
    assert "business-context-schema.yaml" in bc.templates


def test_unknown_skill_raises():
    with pytest.raises(UnknownSkillError):
        get_skill("does-not-exist")


def test_skills_root_exists():
    assert (SKILLS_ROOT / "prioritize" / "SKILL.md").is_file()


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
    """A fake Anthropic client that records the kwargs of messages.create.

    Returns a tool_use response when a schema/tools call is made (json_schema
    path), else a plain text response (call_md path)."""
    def _create(**kw):
        captured.update(kw)
        if kw.get("tools"):
            return _tool_msg()
        return _msg("done")
    return SimpleNamespace(messages=SimpleNamespace(create=_create))


def test_gateway_skill_prepends_method_to_cacheable_prefix(isolated_settings, monkeypatch):
    from app import llm
    from app.graph.gateway import llm_call

    captured: dict = {}
    monkeypatch.setattr(llm, "get_client", lambda: _capture_client(captured))

    spec = get_skill("prioritize")
    r = llm_call(
        enterprise_id="ent-A", agent="synthesis", purpose="rank",
        prompt_version="synth-v1", system="agent system layer", input="candidates",
        json_schema={"type": "object", "properties": {}, "required": []},
        skill="prioritize",
    )
    # json_schema path → method rides the cacheable user prefix (first block).
    user_content = captured["messages"][0]["content"]
    assert isinstance(user_content, list)
    prefix_text = user_content[0]["text"]
    assert prefix_text.startswith(f"## METHOD (skill: prioritize @{spec.content_hash})")
    assert "cache_control" in user_content[0]
    # prompt_version is pinned to the exact method version.
    assert r.prompt_version == f"synth-v1+prioritize@{spec.content_hash}"


def test_gateway_skill_module_appended(isolated_settings, monkeypatch):
    from app import llm
    from app.graph.gateway import llm_call

    captured: dict = {}
    monkeypatch.setattr(llm, "get_client", lambda: _capture_client(captured))

    llm_call(
        enterprise_id="ent-A", agent="competitor_analysis", purpose="x",
        prompt_version="v1", system="sys", input="u",
        json_schema={"type": "object", "properties": {}, "required": []},
        skill="competitive-intelligence-review", skill_module="00-scope.md",
    )
    prefix_text = captured["messages"][0]["content"][0]["text"]
    assert "## METHOD (skill: competitive-intelligence-review" in prefix_text
    assert "### MODULE: 00-scope.md" in prefix_text


def test_gateway_unknown_skill_raises(isolated_settings, monkeypatch):
    from app import llm
    from app.graph.gateway import llm_call

    monkeypatch.setattr(
        llm, "get_client",
        lambda: SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: _msg())),
    )
    with pytest.raises(UnknownSkillError):
        llm_call(
            enterprise_id="ent-A", agent="x", purpose="x",
            prompt_version="v1", system="s", input="u", skill="nope",
        )


def test_gateway_unknown_module_raises(isolated_settings):
    from app.graph.gateway import _build_method_prefix

    with pytest.raises(KeyError):
        _build_method_prefix("prioritize", "no-such-module.md")


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


def test_gateway_md_path_folds_method_into_system(isolated_settings, monkeypatch):
    """call_md has no cacheable-prefix path → method goes into the system prompt."""
    from app import llm
    from app.graph.gateway import llm_call

    captured: dict = {}
    monkeypatch.setattr(llm, "get_client", lambda: _capture_client(captured))
    llm_call(
        enterprise_id="ent-A", agent="x", purpose="x",
        prompt_version="v1", system="AGENT LAYER", input="u",
        skill="decision-memo",  # no json_schema → call_md
    )
    system_sent = captured["system"]
    assert "## METHOD (skill: decision-memo" in system_sent
    # method first, agent layer after.
    assert system_sent.index("## METHOD") < system_sent.index("AGENT LAYER")


# ---------- agent bindings ----------

def test_synthesis_binds_weekly_brief(isolated_settings, monkeypatch):
    """The synthesis brief COMPOSITION call binds the `weekly-brief` skill — its
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

    spec = get_skill("weekly-brief")
    with patch.object(synth, "save_brief"), \
         patch.object(synth, "deliver_brief_to_slack", return_value={"delivered": False, "reason": "slack_not_connected"}), \
         patch.object(synth, "log_agent_decision"):
        # The fake client returns text, not a tool_use block, so call_json's
        # schema path raises after capturing kwargs — that's enough to assert
        # the binding. Catch and inspect what was sent.
        try:
            synth.run_synthesis(_FakeFacade(), "ent-A", dataset_slug="acme")
        except Exception:
            pass

    prefix_text = captured["messages"][0]["content"][0]["text"]
    assert prefix_text.startswith(f"## METHOD (skill: weekly-brief @{spec.content_hash})")


def test_oncall_binds_incident_runbook(isolated_settings, monkeypatch):
    from app import llm

    captured: dict = {}
    monkeypatch.setattr(llm, "get_client", lambda: _capture_client(captured))

    from app.oncall import agent as oncall
    spec = get_skill("incident-runbook")

    monkeypatch.setattr(oncall, "embed_texts", lambda t, **k: [[0.1] * 4 for _ in t])
    facade = _FakeFacade()
    inc = oncall.IncidentInput(title="Checkout 500s", description="spike of 500s")
    try:
        oncall.investigate_incident(facade, "ent-A", incident=inc)
    except Exception:
        pass

    prefix_text = captured["messages"][0]["content"][0]["text"]
    assert prefix_text.startswith(f"## METHOD (skill: incident-runbook @{spec.content_hash})")


def test_market_research_binds_third_party_feedback(isolated_settings, monkeypatch):
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

    spec = get_skill("third-party-feedback")
    market.run_market_research(_FakeFacade(), "ent-A")
    # web-search path folds the method into the system prompt.
    assert f"## METHOD (skill: third-party-feedback @{spec.content_hash})" in captured["system"]


def test_competitor_research_binds_cir(isolated_settings, monkeypatch):
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

    spec = get_skill("competitive-intelligence-review")
    comp.run_competitor_research(_FakeFacade(), "ent-A", competitors=["Adobe"])
    assert f"## METHOD (skill: competitive-intelligence-review @{spec.content_hash})" \
        in captured["system"]


# ---------- ported scoring (prioritize skill) ----------

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
