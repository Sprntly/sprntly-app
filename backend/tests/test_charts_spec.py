"""`ChartSpec` validation — the security boundary, one test per rule.

Every rule in `app/charts/spec.py` gets a rejection case here, and the ones that
matter get their *nested* form too. A `url` at the top level is the case everyone
remembers; a `url` inside `layer[]`/`hconcat[]`/`facet.spec`/`repeat.spec` is the
case that ships. Those are parametrised so adding a container to the walk cannot
quietly lose coverage.
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.charts.spec import (
    ALTAIR_SCHEMA_VERSION,
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
