"""Custom-skill invocation (PRD 1854, PR 3): the resolver seam, the
company-aware slash/pinned routing in qa_agent, and the gateway's injected-spec
path. Everything here monkeypatches the DB lookup — the DB round-trip itself
is covered by test_custom_skills_routes.py.

The contract under test: a custom skill invokes EXACTLY like a built-in —
same RouteDecision shape from the slash fast-path, same "## METHOD (skill:
<id> @<hash>)" prompt block, same "+<id>@<hash>" prompt_version suffix.
"""
from __future__ import annotations

import pytest

import app.qa_agent as qa
import app.skills.resolver as resolver
from app.graph.gateway import _build_method_prefix
from app.skills.loader import SkillSpec, UnknownSkillError, list_skills

CUSTOM_ROW = {
    "slug": "my-estimator",
    "name": "My Estimator",
    "description": "Scores features by reach × confidence.",
    "method": "# Estimation method\nScore by reach x confidence.",
    "modules": {"extra.md": "module text"},
    "references": {"rubric.md": "reference text"},
    "content_hash": "abc123def456",
}


@pytest.fixture
def custom_in_db(monkeypatch):
    """Company 'co-1' has the my-estimator custom skill; everyone else none."""
    def fake_get(company_id: str, slug: str):
        if company_id == "co-1" and slug == "my-estimator":
            return dict(CUSTOM_ROW)
        return None

    monkeypatch.setattr(resolver, "get_custom_skill", fake_get)


# ─── resolver ────────────────────────────────────────────────────────────────


def test_resolver_builtin_passthrough(custom_in_db):
    builtin_id = list_skills()[0]
    spec = resolver.resolve_skill(builtin_id, company_id="co-1")
    assert spec.id == builtin_id  # vendored path untouched by company context


def test_resolver_custom_lookup(custom_in_db):
    spec = resolver.resolve_skill("my-estimator", company_id="co-1")
    assert isinstance(spec, SkillSpec)
    assert spec.id == "my-estimator"
    assert spec.method.startswith("# Estimation")
    assert spec.content_hash == "abc123def456"


def test_resolver_unknown_raises(custom_in_db):
    with pytest.raises(UnknownSkillError):
        resolver.resolve_skill("my-estimator", company_id="co-other")
    with pytest.raises(UnknownSkillError):
        resolver.resolve_skill("nope-nope", company_id="co-1")


def test_has_custom_skill(custom_in_db):
    assert resolver.has_custom_skill("co-1", "my-estimator") is True
    assert resolver.has_custom_skill("co-other", "my-estimator") is False
    assert resolver.has_custom_skill("", "my-estimator") is False


# ─── qa_agent routing ────────────────────────────────────────────────────────


def test_routable_builtin_unchanged(custom_in_db):
    builtin_id = list_skills()[0]
    assert qa._routable(builtin_id) is True
    # NON_ROUTABLE built-ins stay non-routable even with company context.
    for blocked in qa.NON_ROUTABLE:
        assert qa._routable(blocked, "co-1") is False


def test_routable_custom_needs_company(custom_in_db):
    assert qa._routable("my-estimator", "co-1") is True
    assert qa._routable("my-estimator", "co-other") is False
    assert qa._routable("my-estimator") is False  # no company → built-ins only


def test_slash_routes_custom_skill(custom_in_db, monkeypatch):
    # The LLM router must never be reached on the slash fast-path.
    monkeypatch.setattr(qa, "llm_call", lambda **kw: (_ for _ in ()).throw(AssertionError("router called")))
    decision = qa.route("/my-estimator score the login epic", enterprise_id="co-1")
    assert decision.skill_id == "my-estimator"
    assert decision.confidence == 1.0
    assert decision.source == "slash"


def test_slash_unknown_custom_falls_through(custom_in_db, monkeypatch):
    # Unknown slug → not slash-pinned; the LLM router (stubbed to fail) is
    # reached and routing degrades to the direct path, as before this change.
    monkeypatch.setattr(qa, "llm_call", lambda **kw: (_ for _ in ()).throw(RuntimeError("no llm in tests")))
    decision = qa.route("/not-a-skill do something", enterprise_id="co-1")
    assert decision.source == "none"
    assert decision.skill_id is None


def test_answer_single_shot_passes_custom_spec(custom_in_db, monkeypatch):
    captured = {}

    def fake_llm_call(**kwargs):
        captured.update(kwargs)

        class R:
            output = {"answer": "ok", "key_points": [], "citations": [],
                      "confidence": 1.0, "unanswered": ""}

        return R()

    monkeypatch.setattr(qa, "llm_call", fake_llm_call)
    monkeypatch.setattr(qa, "_kg_grounding", lambda *a, **k: ("", False))

    decision = qa.RouteDecision("my-estimator", 1.0, "slash", "my-estimator")
    payload = qa._answer_single_shot(decision, "co-1", "score the epic", None)

    assert captured["skill"] == "my-estimator"
    assert isinstance(captured["skill_spec"], SkillSpec)
    assert captured["skill_spec"].method.startswith("# Estimation")
    assert payload["answer"] == "ok"


def test_answer_single_shot_builtin_spec_is_none(custom_in_db, monkeypatch):
    captured = {}

    def fake_llm_call(**kwargs):
        captured.update(kwargs)

        class R:
            output = {"answer": "ok", "key_points": [], "citations": [],
                      "confidence": 1.0, "unanswered": ""}

        return R()

    monkeypatch.setattr(qa, "llm_call", fake_llm_call)
    monkeypatch.setattr(qa, "_kg_grounding", lambda *a, **k: ("", False))

    builtin_id = list_skills()[0]
    decision = qa.RouteDecision(builtin_id, 1.0, "slash", builtin_id)
    qa._answer_single_shot(decision, "co-1", "question", None)

    assert captured["skill"] == builtin_id
    assert captured["skill_spec"] is None  # built-ins load from disk as before


# ─── gateway spec injection ──────────────────────────────────────────────────


def test_gateway_builds_identical_block_from_injected_spec():
    spec = SkillSpec(
        id="my-estimator",
        method="# Estimation method\nScore by reach x confidence.",
        modules={"extra.md": "module text"},
        references={"rubric.md": "reference text"},
        content_hash="abc123def456",
    )
    block, suffix = _build_method_prefix("my-estimator", None, spec=spec)

    assert block.startswith("## METHOD (skill: my-estimator @abc123def456)\n")
    assert "# Estimation method" in block
    assert "### REFERENCE: rubric.md\nreference text" in block
    assert suffix == "+my-estimator@abc123def456"


def test_gateway_injected_spec_module_selection():
    spec = SkillSpec(
        id="my-estimator", method="m", modules={"extra.md": "module text"},
        content_hash="abc123def456",
    )
    block, _ = _build_method_prefix("my-estimator", "extra.md", spec=spec)
    assert "### MODULE: extra.md\nmodule text" in block

    with pytest.raises(KeyError):
        _build_method_prefix("my-estimator", "missing.md", spec=spec)


def test_gateway_builtin_path_unchanged():
    builtin_id = list_skills()[0]
    block, suffix = _build_method_prefix(builtin_id, None)
    assert block.startswith(f"## METHOD (skill: {builtin_id} @")
    assert suffix.startswith(f"+{builtin_id}@")
