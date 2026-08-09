"""Custom-skill invocation (PRD 1854, PR 3): the resolver seam, the
company-aware slash/pinned routing in qa_agent, and the gateway's injected-spec
path. Everything here monkeypatches the DB lookup — the DB round-trip itself
is covered by test_custom_skills_routes.py.

The contract under test: a custom skill invokes EXACTLY like a built-in —
same RouteDecision shape from the slash fast-path, same "## METHOD (skill:
<id> @<hash>)" prompt block, same "+<id>@<hash>" prompt_version suffix.

No-override rule (PRD 1854 revision, 2026-07-30): a custom skill NEVER takes
over a vendored id. The upload route gives a name-colliding upload its own
trigger, so the ids don't normally overlap at all; resolver, _routable and
_answer_single_shot enforce it anyway, which is what keeps a row uploaded
under the OLD override rule from still hijacking its built-in.
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


@pytest.fixture
def custom_shadows_builtin(monkeypatch):
    """Company 'co-1' holds a row whose slug IS a vendored id — only reachable
    as legacy data now (uploads take a free trigger instead), and the case the
    no-override rule has to hold against. Returns that built-in id."""
    builtin_id = list_skills()[0]
    row = dict(CUSTOM_ROW, slug=builtin_id, name="Shadowing Skill")

    def fake_get(company_id: str, slug: str):
        if company_id == "co-1" and slug == builtin_id:
            return dict(row)
        return None

    monkeypatch.setattr(resolver, "get_custom_skill", fake_get)
    return builtin_id


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


def test_resolver_builtin_wins_over_shadowing_custom(custom_shadows_builtin):
    builtin_id = custom_shadows_builtin
    # A vendored id ALWAYS answers for the built-in — for the company holding
    # the shadowing row exactly as for everyone else. Nothing is overridden.
    for company in ("co-1", "co-other", None):
        spec = resolver.resolve_skill(builtin_id, company_id=company)
        assert spec.id == builtin_id
        assert spec.content_hash != "abc123def456"
    # The row is still reachable on its own terms — it just doesn't own the id.
    assert resolver.has_custom_skill("co-1", builtin_id) is True


def test_custom_lookup_db_failure_fails_open_to_builtins(monkeypatch):
    # A DB failure must degrade to "no such custom skill" — never break
    # built-in answers (this is exactly what CI suites without a reachable DB
    # exercise).
    def boom(company_id: str, slug: str):
        raise RuntimeError("postgrest unreachable")

    monkeypatch.setattr(resolver, "get_custom_skill", boom)

    builtin_id = list_skills()[0]
    assert resolver.custom_skill_spec("co-1", builtin_id) is None
    assert resolver.has_custom_skill("co-1", "my-estimator") is False
    # Built-ins still RESOLVE — the pipelines that bind them by name are
    # unaffected by a custom-skills outage, which is the point of this test.
    assert resolver.resolve_skill(builtin_id, company_id="co-1").id == builtin_id
    # They are just no longer routable from CHAT. `_routable` narrowed to "is
    # this a custom skill for this company", so every vendored id answers False
    # — not only the handful that used to be on the NON_ROUTABLE opt-out list.
    assert qa._routable(builtin_id, "co-1") is False


# ─── qa_agent routing ────────────────────────────────────────────────────────


def test_no_builtin_is_routable_from_chat(custom_in_db):
    """Was `test_routable_builtin_unchanged`, asserting the inverse.

    The built-in half of `_routable` is gone: a chat turn cannot be sent to a
    vendored `SKILL.md` method by naming it. Stated over the WHOLE vendored
    library rather than the old NON_ROUTABLE subset, because the guarantee is
    now unconditional — and because `resolve_skill` is built-in-first, so a True
    here would promise a company's upload and deliver the built-in's method."""
    for builtin_id in list_skills():
        assert qa._routable(builtin_id) is False
        assert qa._routable(builtin_id, "co-1") is False


def test_routable_custom_needs_company(custom_in_db):
    assert qa._routable("my-estimator", "co-1") is True
    assert qa._routable("my-estimator", "co-other") is False
    assert qa._routable("my-estimator") is False  # no company → nothing routable


def test_builtin_id_stays_blocked_for_a_shadowing_row(monkeypatch):
    # A vendored id answers for the BUILT-IN and nothing else, so a legacy row
    # sitting on one cannot make that id invocable from chat.
    blocked = list_skills()[0]
    row = dict(CUSTOM_ROW, slug=blocked)

    def fake_get(company_id: str, slug: str):
        if company_id == "co-1" and slug == blocked:
            return dict(row)
        return None

    monkeypatch.setattr(resolver, "get_custom_skill", fake_get)
    assert qa._routable(blocked, "co-1") is False
    assert qa._routable(blocked, "co-other") is False


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
    # The untrusted-method guard rides the system prompt for custom skills:
    # the model is told the METHOD is user content that can't override rules.
    assert qa.ASK_SYSTEM_CUSTOM_SKILL_ADDENDUM in captured["system"]


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
    # No guard addendum for vendored skills — their prompt is unchanged.
    assert qa.ASK_SYSTEM_CUSTOM_SKILL_ADDENDUM not in captured["system"]


def test_answer_single_shot_keeps_builtin_over_shadowing_custom(
    custom_shadows_builtin, monkeypatch
):
    captured = {}

    def fake_llm_call(**kwargs):
        captured.update(kwargs)

        class R:
            output = {"answer": "ok", "key_points": [], "citations": [],
                      "confidence": 1.0, "unanswered": ""}

        return R()

    monkeypatch.setattr(qa, "llm_call", fake_llm_call)
    monkeypatch.setattr(qa, "_kg_grounding", lambda *a, **k: ("", False))

    builtin_id = custom_shadows_builtin
    decision = qa.RouteDecision(builtin_id, 1.0, "slash", builtin_id)
    qa._answer_single_shot(decision, "co-1", "question", None)

    # The vendored id loads from disk as always — the shadowing row is not
    # consulted, so its method never reaches the prompt.
    assert captured["skill"] == builtin_id
    assert captured["skill_spec"] is None
    assert qa.ASK_SYSTEM_CUSTOM_SKILL_ADDENDUM not in captured["system"]


# ─── gateway spec injection ──────────────────────────────────────────────────


def test_gateway_injected_spec_block_tagged_company_uploaded():
    spec = SkillSpec(
        id="my-estimator",
        method="# Estimation method\nScore by reach x confidence.",
        modules={"extra.md": "module text"},
        references={"rubric.md": "reference text"},
        content_hash="abc123def456",
    )
    block, suffix = _build_method_prefix("my-estimator", None, spec=spec)

    # Same shape as a built-in's block, with ONE deliberate difference: the
    # header labels the method text as company-uploaded (untrusted), which
    # the system prompt's custom-skill addendum points at.
    assert block.startswith(
        "## METHOD (skill: my-estimator @abc123def456, company-uploaded)\n"
    )
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
    # Vendored skills are never tagged — the label means "untrusted upload".
    assert "company-uploaded" not in block.splitlines()[0]
