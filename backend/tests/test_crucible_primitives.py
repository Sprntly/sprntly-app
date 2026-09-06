"""`app.crucible.primitives` — the closed vocabulary a plan step may name.

THE ONE TEST THAT CARRIES THE FILE is `test_every_implemented_primitive_
actually_resolves`: `status="implemented"` is a promise that there is code
behind an operation, and a registry that can claim it without anything
checking is exactly the dishonesty the status field was added to prevent. The
rest of this file is the validator, which is the gate stopping a model from
writing down an operation the engine cannot run.

Pure: no DB, no LLM.
"""
from __future__ import annotations

import importlib

import pytest

from app.crucible import primitives as prim


def test_every_implemented_primitive_actually_resolves():
    """`implemented_by` is a dotted path and it must lead somewhere real.

    Not a style check. An `implemented` entry with a stale path is a plan step
    the reader is told will happen, promised on the strength of a function
    somebody renamed.
    """
    for p in prim.implemented():
        module_path, _, attr = p.implemented_by.rpartition(".")
        module = importlib.import_module(module_path)
        assert hasattr(module, attr), (
            f"{p.id} claims to be implemented by {p.implemented_by}, "
            f"which does not exist"
        )


def test_a_declared_primitive_names_no_implementation():
    for p in prim.declared_only():
        assert not p.implemented_by


def test_there_is_something_registered_in_every_group():
    for group in prim.GROUPS:
        assert prim.by_group(group, implemented_only=False), group


# ─── The validator ─────────────────────────────────────────────────────────


def _step(primitive: str, **params) -> dict:
    return {"primitive": primitive, "params": params}


def test_an_invented_operation_is_rejected():
    problems = prim.validate_steps([_step("compute_vibes")])
    assert len(problems) == 1
    assert "no such primitive" in problems[0].problem


def test_a_declared_but_unimplemented_primitive_may_never_reach_a_plan():
    """THE HONESTY INVARIANT. The vocabulary contains operations the engine
    cannot run — they are registered so the gap is recorded — and a plan that
    named one would be describing work that does not happen, which is the
    exact failure the plan gate exists to remove."""
    declared = prim.declared_only()
    assert declared, "this test is vacuous if nothing is declared-only"
    problems = prim.validate_steps(
        [_step(declared[0].id, source="s", field="f")])
    assert any("declared but not implemented" in p.problem for p in problems)


def test_the_same_declared_primitive_passes_when_that_gate_is_lifted():
    """The rejection above must be about STATUS, not about the entry being
    malformed — otherwise the honesty invariant is untested and the step was
    simply invalid."""
    problems = prim.validate_steps(
        [_step("partition_population", source="contracts", field="segment")],
        implemented_only=False,
    )
    assert problems == ()


def test_a_missing_required_parameter_is_rejected():
    problems = prim.validate_steps([_step("reconcile_value_columns",
                                          source="contracts")])
    named = " ".join(p.problem for p in problems)
    assert "'base_field' is missing" in named
    assert "'total_field' is missing" in named


def test_a_parameter_of_the_wrong_type_is_rejected():
    problems = prim.validate_steps([_step("select_top_n", top_n="five")])
    assert any("must be a whole number" in p.problem for p in problems)


def test_a_boolean_is_not_a_whole_number():
    """`bool` is an `int` in Python and is never what a step meant by one."""
    problems = prim.validate_steps([_step("select_top_n", top_n=True)])
    assert any("must be a whole number" in p.problem for p in problems)


def test_an_undeclared_parameter_is_rejected():
    problems = prim.validate_steps([_step("select_top_n", top_n=5, sneaky="x")])
    assert any("not declared by this primitive" in p.problem for p in problems)


def test_a_step_naming_a_source_this_run_does_not_have_is_rejected():
    """The most expensive kind of wrong plan, because it reads as a promise:
    a method described over evidence the customer never connected."""
    problems = prim.validate_steps(
        [_step("audit_field_coverage", source="salesforce", field="stage")],
        available_sources=["contracts", "tickets"],
    )
    assert any("not present in this run" in p.problem for p in problems)


def test_source_checking_is_skipped_when_the_caller_has_no_inventory():
    """An unset argument means "do not check", never "the world is empty" —
    otherwise a caller without the inventory fails every step."""
    assert prim.validate_steps(
        [_step("audit_field_coverage", source="salesforce", field="stage")]
    ) == ()


def test_a_steps_rendered_sources_are_checked_as_well_as_its_parameters():
    """A step whose rendered source list and actual parameters disagree is a
    plan that misdescribes itself to the reader."""
    problems = prim.validate_steps(
        [{"primitive": "score_impact", "params": {}, "sources": ["nowhere"]}],
        available_sources=["contracts"],
    )
    assert any("nowhere" in p.problem for p in problems)


# ─── The catalogue the model is shown ──────────────────────────────────────


def test_the_catalogue_never_offers_an_operation_a_plan_may_not_name():
    """The prompt is GENERATED from the registry for this reason. A
    hand-written list is a second registry, and it drifts in one direction:
    it keeps offering the operation that was downgraded to `declared`, and the
    model keeps emitting it.

    A GATED PRIMITIVE IS `implemented` AND STILL MAY NOT BE NAMED on a run
    whose verdict does not permit it — "the engine can do this" and "this run
    is doing this" are different facts, and only the second one may reach the
    model."""
    text = prim.catalogue(weighting_enabled=True)
    for p in prim.declared_only():
        assert p.id not in text
    for p in prim.implemented():
        assert p.id in text


def test_the_catalogue_withholds_a_gated_operation_by_default():
    """DEFAULT FALSE, AND IT IS THE DEFAULT THAT MATTERS. A caller that has
    not read the run's verdict must not be handed an operation the run will
    not perform — a model shown an operation writes a step for it, and the
    plan then promises weighting on a run that counts."""
    text = prim.catalogue()
    for gated in prim.WEIGHTING_GATED:
        assert gated not in text
    # ...and nothing else went missing with it.
    for p in prim.implemented():
        if p.id not in prim.WEIGHTING_GATED:
            assert p.id in text


def test_a_gated_step_is_rejected_on_a_counted_run_and_accepted_on_a_weighted_one():
    """`requires_sources` is declared on `Primitive` and read NOWHERE in the
    backend, so a condition expressed there would be a condition that
    describes and does not perform. This is the enforced version."""
    step = [{"primitive": "weight_by_account_value", "params": {}}]
    problems = prim.validate_steps(step)
    assert [p.problem for p in problems] == [
        "weighs by account value, and this run's approved unit is a count of "
        "accounts, so a plan must not name it"]
    assert prim.validate_steps(step, weighting_enabled=True) == ()


@pytest.mark.parametrize("bad, expected", [
    (prim.Primitive(id="x", group="scoping", description="d",
                    status="implemented"),
     "implemented"),
    (prim.Primitive(id="x", group="nonsense", description="d"),
     "unknown group"),
    (prim.Primitive(id="x", group="scoping", description="d",
                    params=(prim.Param("p", "dataframe"),)),
     "unknown"),
])
def test_the_registry_refuses_to_contradict_itself(monkeypatch, bad, expected):
    """The audit runs at IMPORT rather than in a test, so a contradictory
    entry cannot reach a customer's plan — catching it here would only mean it
    cannot reach main. This exercises the same function against entries the
    real registry is not allowed to contain."""
    monkeypatch.setattr(prim, "_PRIMITIVES", (bad,))
    monkeypatch.setattr(prim, "REGISTRY", {bad.id: bad})
    with pytest.raises(ValueError, match=expected):
        prim._audit()
