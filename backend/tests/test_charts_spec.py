"""`ChartSpec` validation — the security boundary, one test per rule.

Every rule in `app/charts/spec.py` gets a rejection case here, and the ones that
matter get their *nested* form too. A `url` at the top level is the case everyone
remembers; a `url` inside `layer[]`/`hconcat[]`/`facet.spec`/`repeat.spec` is the
case that ships. Those are parametrised so adding a container to the walk cannot
quietly lose coverage.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.charts.spec import (
    ALTAIR_SCHEMA_VERSION,
    count_rows,
    extract_rows,
    total_row_payload,
    VEGA_LITE_SCHEMA_URL,
    VL_VERSION,
    ChartProvenance,
    ChartSpec,
    ChartSpecError,
    _top_level_keys,
    validate_vega_lite_spec,
)

BASE = {
    "mark": "bar",
    "encoding": {
        "x": {"field": "a", "type": "nominal"},
        "y": {"field": "b", "type": "quantitative"},
    },
}
ROWS = [{"a": "A", "b": 1}, {"a": "B", "b": 2}]


def _with_data(spec: dict) -> dict:
    return {**spec, "data": {"values": ROWS}}


# ── the schema source ────────────────────────────────────────────────────────

def test_schema_comes_from_the_pinned_altair_and_matches_our_version():
    """The whole point of reading altair's schema is that it cannot drift."""
    import altair

    assert altair.SCHEMA_VERSION == ALTAIR_SCHEMA_VERSION
    # "v6.4.1" -> "6.4", which is what vl_convert is told.
    assert ALTAIR_SCHEMA_VERSION.lstrip("v").startswith(VL_VERSION)
    assert VEGA_LITE_SCHEMA_URL.endswith(f"{ALTAIR_SCHEMA_VERSION}.json")


def test_top_level_allowlist_is_derived_not_hand_written():
    keys = _top_level_keys()
    # A sample of what Vega-Lite actually allows up top; if the derivation broke,
    # this collapses to an empty/short set and every valid spec starts failing.
    for key in ("mark", "encoding", "layer", "hconcat", "facet", "repeat", "$schema"):
        assert key in keys
    assert len(keys) > 25


# ── happy path ───────────────────────────────────────────────────────────────

def test_valid_spec_passes():
    validate_vega_lite_spec(_with_data(BASE))


def test_calculate_and_string_filter_transforms_stay_allowed():
    """Deliberate: Phase 1 authors specs through altair, where these are ordinary."""
    validate_vega_lite_spec(
        {
            **_with_data(BASE),
            "transform": [
                {"calculate": "datum.b * 2", "as": "double"},
                {"filter": "datum.b > 0"},
            ],
        }
    )


def test_chart_spec_inlines_its_rows_and_stamps_the_schema():
    chart = ChartSpec(spec=dict(BASE), data=ROWS)
    assert chart.spec["data"] == {"values": ROWS}
    assert chart.spec["$schema"] == VEGA_LITE_SCHEMA_URL
    # The caller's dict is not mutated.
    assert "data" not in BASE


def test_envelope_rows_win_over_a_data_block_already_in_the_spec():
    """Injection is unconditional, on this side and on the client's.

    A *conditional* injection on either side means the same stored chart renders
    from the spec's rows on the server and the envelope's rows in the browser —
    one chart, two pictures, and no error anywhere to notice it by.
    """
    envelope_rows = [{"a": "Z", "b": 9}]
    chart = ChartSpec(spec=_with_data(BASE), data=envelope_rows)
    assert chart.spec["data"] == {"values": envelope_rows}


def test_an_empty_envelope_leaves_a_self_contained_spec_alone():
    """The model-authored path: a spec that already carries its own rows."""
    chart = ChartSpec(spec=_with_data(BASE))
    assert chart.spec["data"] == {"values": ROWS}
    assert chart.row_count() == len(ROWS)


def test_the_serialised_spec_carries_its_rows_so_a_client_cannot_render_blank():
    """A data-free spec handed to `vega-embed` renders an empty box at
    ready-state: no exception, so no degrade-to-table and no data either. The
    envelope closes the spec at construction precisely so that a client which
    passes `spec` through verbatim still gets a chart."""
    payload = ChartSpec(spec=dict(BASE), data=ROWS).to_payload()
    assert payload["spec"]["data"] == {"values": ROWS}
    assert payload["data"] == ROWS


def test_to_payload_round_trips_through_json():
    chart = ChartSpec(
        spec=dict(BASE),
        data=ROWS,
        title="T",
        caption="C",
        provenance=ChartProvenance(source="unit test", rows=2, generated_by="test"),
    )
    payload = chart.to_payload()
    assert json.loads(json.dumps(payload)) == payload
    assert ChartSpec.model_validate(payload).spec == chart.spec


# ── where the rows live (the cross-language contract) ────────────────────────

SHARED_ROW_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "web"
    / "app"
    / "lib"
    / "__fixtures__"
    / "chart-row-extraction.json"
)
"""The SAME file #985's `specDataRows` tests read.

One fixture, two implementations, one definition — read at test time with
`open()`, the mirror of `web/app/lib/__tests__/pipeline-contract.test.ts`
reading `backend/data/`. No bundler is involved in either direction.

It lives under `web/` only because #985 could not touch `backend/`; it is not
web-owned. Relocating it to a neutral home is a follow-up for whoever can touch
both trees in one PR.
"""


def _shared_fixture() -> dict:
    if not SHARED_ROW_FIXTURE.exists():
        return {}
    return json.loads(SHARED_ROW_FIXTURE.read_text(encoding="utf-8"))


def _shared_cases():
    return _shared_fixture().get("cases", [])


def _shared_contract_cases():
    return _shared_fixture().get("contractCases", [])


@pytest.mark.parametrize("case", _shared_cases(), ids=lambda c: c["name"])
def test_row_extraction_contract(case):
    """Every case in the shared fixture, answered by the backend implementation.

    Both sides use this number for decisions a user sees — the server to decide
    "empty, degrade to a table", the client to decide whether to inject rows and
    offer expand-to-table. Two answers to one question is a chart that reads
    "No data." in a report and draws fine in the browser, from one stored object,
    with no error on either side. That is exactly what happened before this
    fixture existed, in three different directions at once.
    """
    assert count_rows(case["spec"]) == case["rowCount"]


@pytest.mark.parametrize(
    "case", _shared_contract_cases(), ids=lambda c: c["name"]
)
def test_accept_reject_contract(case):
    """The fixture pins WHAT IS IN CONTRACT, not just where rows live.

    This half used to exist only as two separate codebases' opinions, which is
    how a spec ends up stored by one side and refused by the other. `rejected`
    is the shared answer; this asserts the backend gives it.
    """
    rejected = True
    try:
        validate_vega_lite_spec(case["spec"])
        rejected = False
    except ChartSpecError:
        pass
    assert rejected == case["rejected"], case.get("$note", case["name"])


def test_the_depth_limit_is_the_contract_value_not_a_coincidence():
    """`limits.maxDepth` is the pin; the constant is asserted against it.

    It was unpinned drift — client 64, server 16, agreeing up to 16 and
    diverging from 17. Two constants that happen to match is not a contract.
    """
    from app.charts.spec import _MAX_DEPTH

    limits = _shared_fixture().get("limits", {})
    assert limits.get("maxDepth") == _MAX_DEPTH


def test_the_shared_fixture_is_actually_present_and_pins_all_three_things():
    """A silently-empty parametrize would make the contract vacuous.

    Skips rather than fails only if the fixture is relocated out from under us —
    in which case this repoints, it does not get deleted.
    """
    if not SHARED_ROW_FIXTURE.exists():  # pragma: no cover - relocation guard
        pytest.skip(f"shared row fixture not at {SHARED_ROW_FIXTURE}")
    fixture = _shared_fixture()
    assert len(fixture["cases"]) >= 17           # row extraction
    assert len(fixture["contractCases"]) >= 13   # accept/reject
    assert fixture["limits"]["maxDepth"] == 64   # the limit
    names = " ".join(case["name"] for case in fixture["cases"])
    assert "altair" in names and "layer" in names


# The three cases in that fixture that encode DECISIONS rather than examples.
# Asserted separately so that if the fixture is ever trimmed, the decisions do
# not silently stop being tested.

def test_decision_inline_values_beat_a_named_reference_on_the_same_node():
    spec = {
        "mark": "bar",
        "data": {"name": "d", "values": [{"a": 1}, {"a": 2}]},
        "datasets": {"d": [{"b": 9}]},
    }
    assert extract_rows(spec) == [{"a": 1}, {"a": 2}]


def test_decision_first_container_that_yields_rows_wins():
    """Not the largest, not the sum — the first. Document order decides."""
    spec = {
        "layer": [
            {"mark": "line", "data": {"values": [{"a": 1}]}},
            {"mark": "point", "data": {"values": [{"b": 1}, {"b": 2}]}},
        ]
    }
    assert count_rows(spec) == 1


def test_decision_non_object_array_elements_are_not_rows():
    assert count_rows({"mark": "bar", "data": {"values": [1, 2, 3]}}) == 0
    assert count_rows({"mark": "bar", "data": {"values": [1, {"a": 1}, "x"]}}) == 1


def test_decision_an_EMPTY_inline_values_falls_through_to_the_named_reference():
    """Presence of `values` is not the test; having rows is.

    Compiled against vega-lite 6.4.3, `{"name": "d", "values": []}` with a
    populated `datasets.d` yields a vega `data[0]` of the NAMED dataset. Reading
    the mere presence of `values` counted 0 rows and printed "No data." over a
    chart `vl_convert` draws in 7,071 bytes and the client returns 2 rows for.
    """
    spec = {
        "mark": "bar",
        "data": {"name": "d", "values": []},
        "datasets": {"d": [{"a": 1}, {"a": 2}]},
    }
    assert count_rows(spec) == 2


def test_an_empty_inline_values_with_no_reference_is_still_empty():
    """The fall-through must not invent rows where there are none."""
    assert count_rows({"mark": "bar", "data": {"values": []}}) == 0
    assert count_rows({"mark": "bar", "data": {"name": "d"}, "datasets": {"d": []}}) == 0


def test_row_extraction_and_the_security_walk_share_one_depth_limit():
    """64 on both sides, and the same number the client's walker uses.

    An undocumented 16 here meant the two implementations agreed up to 16 and
    diverged from 17: a 17-deep `vconcat` counted 0 rows and printed "No data."
    while `vl_convert` rendered it in 13,310 bytes.
    """
    from app.charts.spec import _MAX_DEPTH

    assert _MAX_DEPTH == 64

    def nest(depth):
        node = {"mark": "bar", "data": {"values": [{"a": 1}, {"a": 2}]}}
        for _ in range(depth):
            node = {"vconcat": [node]}
        return node

    assert count_rows(nest(16)) == 2
    assert count_rows(nest(17)) == 2  # the case that used to disagree
    assert count_rows(nest(_MAX_DEPTH)) == 2
    assert count_rows(nest(_MAX_DEPTH + 1)) == 0  # past the agreed limit, both stop


def test_a_csv_string_in_values_is_out_of_contract():
    """Vega-Lite accepts it; we refuse it, on purpose.

    Every chart promises the rows behind it can be shown as a table — that
    provenance is the trust story the contract exists for. A CSV string we do
    not parse cannot be tabulated, so accepting it would draw a chart whose data
    we could not display, and break the promise silently rather than fail.
    """
    for payload in ("a,b\nA,1\nB,2", "a\tb\nA\t1"):
        with pytest.raises(ChartSpecError, match="array of rows"):
            validate_vega_lite_spec(
                {**BASE, "data": {"values": payload, "format": {"type": "csv"}}}
            )


# ── the cap asks a DIFFERENT question, on purpose ────────────────────────────

def test_total_row_payload_sums_what_the_renderer_is_handed():
    """The contract stops at the first container; the cap cannot afford to.

    A two-layer spec of 100k rows each counts 100k under the shared definition
    and hands the renderer 200k. `MAX_ROWS` is the only real defence (the
    timeout bounds the wait, not the work), so it measures the payload.
    """
    spec = {
        "layer": [
            {"mark": "line", "data": {"values": [{"a": 1}]}},
            {"mark": "point", "data": {"values": [{"b": 1}, {"b": 2}]}},
        ]
    }
    assert count_rows(spec) == 1        # the contract
    assert total_row_payload(spec) == 3  # what actually gets rendered


def test_total_row_payload_is_breadth_first_so_ordering_cannot_hide_the_payload():
    """It was LIFO: 10,050 empty sibling marks popped first, the budget ran out
    before reaching the 200k-row layer, and the function returned 0 — the cap
    failing OPEN on the largest payload in the spec."""
    from app.charts.spec import total_row_payload as payload

    spec = {
        "layer": [{"data": {"values": [{"a": i} for i in range(500)]}, "mark": "bar"}]
        + [{"mark": "rule"} for _ in range(200)]
    }
    assert payload(spec) == 500


def test_total_row_payload_raises_rather_than_under_counting():
    """A partial count fails open on a cap — the same bug, quieter."""
    from app.charts.spec import _MAX_NODES
    from app.charts.spec import total_row_payload as payload

    spec = {"layer": [{"mark": "rule"} for _ in range(_MAX_NODES + 10)]}
    with pytest.raises(ChartSpecError, match="refusing to size it"):
        payload(spec)


def test_pathological_breadth_is_refused_before_the_validator_sees_it():
    """The schema validator has no breadth budget and is the expensive step:
    ~10k sibling views took `ChartSpec.build` 60.3s, all inside jsonschema,
    holding a worker. The structural walk runs first, so it is the place to
    stop that. vega's V8 blows its stack on that shape anyway."""
    from app.charts.spec import _MAX_NODES

    spec = {"layer": [{"mark": "rule"} for _ in range(_MAX_NODES)]}
    with pytest.raises(ChartSpecError, match="structural nodes"):
        validate_vega_lite_spec(spec)


def test_an_elaborate_but_legitimate_dashboard_is_not_caught_by_the_breadth_cap():
    """The cap is measured against real shapes: our worst emitter is 98 nodes and
    a 20-view dashboard is 412, against a limit of 5,000."""
    inner = {
        "layer": [
            {"mark": "line", "encoding": dict(BASE["encoding"])},
            {"mark": "point", "encoding": dict(BASE["encoding"])},
        ]
    }
    dashboard = {
        "data": {"values": ROWS},
        "vconcat": [{"hconcat": [inner for _ in range(4)]} for _ in range(5)],
    }
    validate_vega_lite_spec(dashboard)  # must not raise


def test_total_row_payload_counts_a_named_dataset_once():
    spec = {
        "datasets": {"d": [{"a": 1}, {"a": 2}, {"a": 3}]},
        "layer": [
            {"data": {"name": "d"}, "mark": "line"},
            {"data": {"name": "d"}, "mark": "point"},
        ],
    }
    assert total_row_payload(spec) == 3


def test_total_row_payload_counts_an_unreferenced_dataset():
    """The renderer is handed it either way, so the cap must see it."""
    spec = {
        "mark": "bar",
        "data": {"values": [{"a": 1}]},
        "datasets": {"orphan": [{"b": 1}, {"b": 2}]},
    }
    assert count_rows(spec) == 1
    assert total_row_payload(spec) == 3


# ── rule 1: data-closed ──────────────────────────────────────────────────────

NESTED_URL_CASES = {
    "top_level": {**BASE, "data": {"url": "https://example.com/rows.json"}},
    "layer": {"layer": [{**BASE, "data": {"url": "https://example.com/rows.json"}}]},
    "hconcat": {"hconcat": [{**BASE, "data": {"url": "https://example.com/r.json"}}]},
    "vconcat": {"vconcat": [{**BASE, "data": {"url": "https://example.com/r.json"}}]},
    "concat": {"concat": [{**BASE, "data": {"url": "https://example.com/r.json"}}]},
    "facet_spec": {
        "facet": {"field": "a", "type": "nominal"},
        "spec": {**BASE, "data": {"url": "https://example.com/r.json"}},
    },
    "repeat_spec": {
        "repeat": ["a"],
        "spec": {
            "mark": "bar",
            "data": {"url": "https://example.com/r.json"},
            "encoding": {"x": {"field": {"repeat": "repeat"}, "type": "nominal"}},
        },
    },
}


@pytest.mark.parametrize("where", sorted(NESTED_URL_CASES))
def test_url_is_rejected_at_every_nesting_depth(where):
    with pytest.raises(ChartSpecError) as excinfo:
        validate_vega_lite_spec(NESTED_URL_CASES[where])
    assert "data-closed" in str(excinfo.value)
    assert excinfo.value.path and excinfo.value.path.endswith(".url")


def test_a_url_column_in_the_rows_is_data_not_an_instruction():
    """The rules are about what the SPEC instructs, not what the rows contain.

    A "top referrer URLs" chart is a legitimate chart. Rejecting it would be a
    rule people learn to route around rather than a boundary.
    """
    validate_vega_lite_spec(
        {
            "data": {"values": [{"url": "https://example.com/a", "hits": 3}]},
            "mark": "bar",
            "encoding": {
                "x": {"field": "url", "type": "nominal"},
                "y": {"field": "hits", "type": "quantitative"},
            },
        }
    )


def test_a_dict_hiding_under_values_is_still_rejected():
    """Not descending into rows is only safe while rows are actually rows."""
    with pytest.raises(ChartSpecError, match="array of rows"):
        validate_vega_lite_spec({**BASE, "data": {"values": {"url": "https://x"}}})


def test_href_is_rejected_outside_an_encoding_block_too():
    """Blanket, not a channel enumeration — enumerating is how the gap appears."""
    with pytest.raises(ChartSpecError, match="href"):
        validate_vega_lite_spec(
            {"data": {"values": ROWS}, "layer": [{**BASE, "href": "https://example.com"}]}
        )


def test_datasets_pointing_at_a_url_is_rejected():
    with pytest.raises(ChartSpecError):
        validate_vega_lite_spec(
            {**BASE, "datasets": {"d": {"url": "https://example.com/r.json"}}}
        )


def test_datasets_are_rejected_on_SHAPE_not_on_the_url_key():
    """Agreed with #985, which rejects the same shape.

    The shape rule is the stronger one and it is what earns the right to skip
    descending into row payloads during the security walk: if a `datasets` entry
    must be an array of rows, then nothing that is not rows can hide there. The
    `url`-key check would have caught only the case someone thought of.

    A divergence here would be the bad kind — a spec that stores fine on one
    side and refuses to render on the other.
    """
    for bad in (
        {"url": "https://example.com/rows.json"},  # the fetch shape
        "https://example.com/rows.json",           # a bare string
        {"foo": 1},                                # an object that is not rows
        42,                                        # not even close
    ):
        with pytest.raises(ChartSpecError, match="inline row arrays"):
            validate_vega_lite_spec({**_with_data(BASE), "datasets": {"d": bad}})


def test_datasets_must_be_inline_row_arrays():
    with pytest.raises(ChartSpecError, match="inline row arrays"):
        validate_vega_lite_spec({**_with_data(BASE), "datasets": {"d": "https://x"}})


# ── rule 2: nothing the reader's browser would fetch ─────────────────────────

def test_image_marks_are_rejected():
    with pytest.raises(ChartSpecError, match="image"):
        validate_vega_lite_spec(
            {
                "data": {"values": [{"u": "https://example.com/pixel.png"}]},
                "mark": {"type": "image"},
                "encoding": {"x": {"field": "u", "type": "nominal"}},
            }
        )


def test_image_marks_are_rejected_inside_a_layer():
    with pytest.raises(ChartSpecError, match="image"):
        validate_vega_lite_spec(
            {
                "data": {"values": ROWS},
                "layer": [BASE, {"mark": "image", "encoding": {}}],
            }
        )


def test_href_encodings_are_rejected():
    with pytest.raises(ChartSpecError, match="href"):
        validate_vega_lite_spec(
            {
                "data": {"values": ROWS},
                "mark": "bar",
                "encoding": {
                    "x": {"field": "a", "type": "nominal"},
                    "href": {"field": "a", "type": "nominal"},
                },
            }
        )


# ── rule 3: no expression bindings ───────────────────────────────────────────

def test_transform_with_expr_is_rejected():
    with pytest.raises(ChartSpecError, match="expression"):
        validate_vega_lite_spec(
            {**_with_data(BASE), "transform": [{"filter": {"expr": "datum.b > 1"}}]}
        )


def test_expr_nested_inside_a_layer_transform_is_rejected():
    with pytest.raises(ChartSpecError, match="expression"):
        validate_vega_lite_spec(
            {
                "data": {"values": ROWS},
                "layer": [
                    {
                        **BASE,
                        "transform": [
                            {"calculate": "1", "as": "z"},
                            {"filter": {"param": {"expr": "true"}}},
                        ],
                    }
                ],
            }
        )


def test_expr_as_a_value_ref_is_rejected():
    """`{"expr": …}` is legal Vega-Lite anywhere a value goes — not here."""
    with pytest.raises(ChartSpecError, match="expression"):
        validate_vega_lite_spec(
            {
                "data": {"values": ROWS},
                "mark": {"type": "bar", "opacity": {"expr": "0.5"}},
                "encoding": BASE["encoding"],
            }
        )


def test_params_are_rejected():
    """Inert server-side; on the client they are input widgets in a PRD panel."""
    with pytest.raises(ChartSpecError, match="params"):
        validate_vega_lite_spec(
            {
                **_with_data(BASE),
                "params": [{"name": "cutoff", "value": 5, "bind": {"input": "range"}}],
            }
        )


def test_params_are_rejected_inside_a_layer_too():
    with pytest.raises(ChartSpecError, match="params"):
        validate_vega_lite_spec(
            {
                "data": {"values": ROWS},
                "layer": [{**BASE, "params": [{"name": "grid", "bind": "scales"}]}],
            }
        )


def test_usermeta_is_stripped_rather_than_stored():
    """`to_payload()` persists the spec; an arbitrary blob must not ride along.

    Stored, `usermeta` becomes every future consumer's problem — the report path,
    docx/pdf/email, the API, MCP — each with its own chance to forget to strip it.
    Stripped rather than rejected because altair writes a benign one of its own.
    """
    chart = ChartSpec(
        spec={**BASE, "usermeta": {"embedOptions": {"actions": True}}}, data=ROWS
    )
    assert "usermeta" not in chart.spec
    assert "usermeta" not in json.dumps(chart.to_payload())


# ── rule 4: the theme is ours ────────────────────────────────────────────────

def test_top_level_facet_is_rejected():
    """`{facet, spec}` is structurally ambiguous with the ChartSpec envelope."""
    with pytest.raises(ChartSpecError, match="ambiguous"):
        validate_vega_lite_spec(
            {
                "data": {"values": ROWS},
                "facet": {"field": "a", "type": "nominal"},
                "spec": BASE,
            }
        )


def test_top_level_repeat_is_rejected():
    with pytest.raises(ChartSpecError, match="ambiguous"):
        validate_vega_lite_spec(
            {
                "data": {"values": ROWS},
                "repeat": ["b"],
                "spec": {
                    "mark": "bar",
                    "encoding": {"x": {"field": {"repeat": "repeat"}, "type": "nominal"}},
                },
            }
        )


def test_the_envelope_itself_cannot_carry_facet_or_repeat():
    """Belt and braces on the same pin: the model forbids extra fields."""
    with pytest.raises(ValidationError):
        ChartSpec(spec=dict(BASE), data=ROWS, facet={"field": "a"})


def test_top_level_config_is_rejected():
    with pytest.raises(ChartSpecError, match="config"):
        validate_vega_lite_spec({**_with_data(BASE), "config": {"background": "red"}})


# ── rule 5: unknown top-level keys ───────────────────────────────────────────

def test_unknown_top_level_key_is_rejected():
    with pytest.raises(ChartSpecError, match="unknown top-level key"):
        validate_vega_lite_spec({**_with_data(BASE), "loader": {"http": {}}})


# ── rule 6: the schema, plus shape guards ────────────────────────────────────

def test_schema_invalid_spec_is_rejected_with_a_path():
    with pytest.raises(ChartSpecError) as excinfo:
        validate_vega_lite_spec({"data": {"values": ROWS}, "mark": "notamark"})
    assert f"Vega-Lite v{VL_VERSION}" in str(excinfo.value)


def test_non_object_spec_is_rejected():
    with pytest.raises(ChartSpecError, match="JSON object"):
        validate_vega_lite_spec(["not", "a", "spec"])


def test_pathologically_deep_spec_is_rejected_rather_than_recursing():
    deep: dict = {"mark": "bar"}
    node = deep
    for _ in range(200):
        node["encoding"] = {"x": {}}
        node = node["encoding"]["x"]
    with pytest.raises(ChartSpecError, match="nests deeper"):
        validate_vega_lite_spec(deep)


# ── how the error reaches a caller ───────────────────────────────────────────

def test_constructor_raises_validation_error_and_build_unwraps_it():
    bad = {**BASE, "data": {"url": "https://example.com/r.json"}}
    with pytest.raises(ValidationError):
        ChartSpec(spec=bad)
    with pytest.raises(ChartSpecError, match="data-closed"):
        ChartSpec.build(spec=bad)


def test_model_validate_cannot_skip_validation():
    """The deserialisation path is the one a stored spec takes — it must validate."""
    with pytest.raises(ValidationError):
        ChartSpec.model_validate(
            {"spec": {**BASE, "data": {"url": "https://example.com/r.json"}}}
        )


def test_extra_fields_on_the_envelope_are_rejected():
    with pytest.raises(ValidationError):
        ChartSpec(spec=dict(BASE), data=ROWS, sneaky="x")
