"""`ChartSpec` — the one chart contract, and the validation that makes it safe.

Every chart in Sprntly (deterministic emitter output *and* model-authored
one-offs) is a `ChartSpec`: a Vega-Lite spec, the rows it was built from, and
provenance. One contract, two renderers — `app.charts.render` turns it into
static SVG/PNG for the no-scripts HTML surfaces, and (Phase 2) the web app hands
the same spec to `vega-embed` for the interactive React surfaces.

## Why v6.4 and not v5

The original plan said Vega-Lite v5. That was wrong, and the reason is worth
keeping: `altair==6.2.2` emits `v6.4.1` specs and has **no v5 mode**. Validating
against v5 would have rejected our own future emitter output the moment Phase 1
starts authoring specs through altair in the code-execution sandbox. So the
contract is v6.4 throughout: validated here against altair's own bundled schema,
rendered by `vl_convert` with `vl_version="6.4"` pinned at every call site, and
matched by Phase 2's `vega-lite@6.4.3`.

The schema is **read out of the installed altair package** rather than vendored
into the repo. That is deliberate on two counts: it cannot drift from the altair
pin (a version bump moves both at once, and `tests/test_charts_spec.py` asserts
the two agree), and it keeps a 1.8 MB machine-generated JSON blob out of the
diff. It is still an offline read — `importlib.resources`, no network.

## Validation is a security boundary, not a nicety

A spec can arrive from the model (the DS code-execution sandbox writes
`*.vl.json`; the LLM may author one inline). Vega-Lite is a *program*: it can
fetch data over the network and evaluate expressions. So construction of a
`ChartSpec` enforces, in this order (cheapest and most security-relevant first):

1. **Data-closed.** No `url` key anywhere in the spec — not at the top level, and
   not inside a `layer[]` / `hconcat[]` / `vconcat[]` / `concat[]` / `facet.spec` /
   `repeat.spec` child, which is where it actually hides. A `ChartSpec` carries
   its rows; it never fetches. `datasets` values must be inline arrays.
2. **No `image` marks and no `href`, anywhere.** Both are network reach that
   survives SSR: an `image` mark makes a fetch happen, and an `href` puts a
   spec-chosen link into a chart the user is invited to click, out of a sandboxed
   report. `href` is refused as a *key anywhere* rather than as an enumerated
   encoding channel — enumerating channels is precisely how such a gap appears.
3. **No expression bindings.** No `expr` key at any depth — value refs, `params`,
   transform entries alike. (See the note on `calculate` below.)
4. **No top-level `config`.** Vega-Lite's `config` REPLACES the one the renderer
   is handed rather than merging, so one spec could opt itself out of the theme.
5. **No top-level `facet`/`repeat`.** `{facet, spec}` carries a `spec` key, which
   is ambiguous with this envelope's own `spec`; the client discriminates on
   exactly that. Facet inside a child spec instead.
6. **No unknown top-level keys** — the allowlist is *derived from the schema
   itself* (`_top_level_keys`), so it cannot drift from the version we validate
   against.
7. **Schema-valid** against the Vega-Lite v6.4 JSON schema.

Rules 1-3 read the spec's STRUCTURE, not its rows: inline row payloads
(`data.values`, `datasets.*`) are not descended into, because a data column named
`url` is a value rather than a fetch instruction, and a rule that fires on the
data is one people learn to route around. Their shape is still checked.

Rejection raises `ChartSpecError`, a `ValueError` subclass. Note the consequence:
raised inside a pydantic validator it is converted into a `ValidationError`, so
`ChartSpec(...)` surfaces *that*, not `ChartSpecError`. Callers that want the
precise type use `ChartSpec.build(...)`, which unwraps it back. Validation lives
in a real validator rather than `__init__` on purpose — `model_validate()` (how
Phase 1/2 will deserialise stored specs) bypasses `__init__` entirely, and a
security check that a deserialiser can skip is not a security check.

### What is deliberately still allowed

`calculate` and string-form `filter` transforms stay legal. They are the same
Vega expression language as `expr`, and that language is sandboxed — no DOM, no
filesystem, no network; the network reach is `url`, which rule 1 closes. Banning
them would break Phase 1, where the model authors specs through `altair` and
`transform_calculate` is ordinary. `expr` is rejected because the emitter
contract gives us no legitimate use for reactive value bindings, so refusing them
costs nothing.

Rendering applies a second, independent lock: `render.py` passes
`allowed_base_urls=[]` to `vl_convert`, so even a spec that somehow slipped a URL
past this module cannot fetch. Belt and braces, on purpose.
"""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Iterator

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

# The Vega-Lite version we validate against AND render with. `render.py` passes
# `VL_VERSION` to `vl_convert` explicitly rather than letting it infer from
# `$schema` — an unpinned version is how golden files start drifting on a
# dependency bump.
VL_VERSION = "6.4"
"""What `vl_convert` is told. Its supported set is coarse-grained (`"6.4"`)."""

ALTAIR_SCHEMA_VERSION = "v6.4.1"
"""What `altair==6.2.2` stamps. Asserted against `altair.SCHEMA_VERSION` in tests."""

VEGA_LITE_SCHEMA_URL = f"https://vega.github.io/schema/vega-lite/{ALTAIR_SCHEMA_VERSION}.json"

# Where altair keeps the schema it validates its own output against.
_SCHEMA_PACKAGE = "altair.vegalite.v6.schema"
_SCHEMA_RESOURCE = "vega-lite-schema.json"


class ChartSpecError(ValueError):
    """A chart spec was rejected. Carries the offending JSON path when known.

    A `ValueError` subclass so generic input handling catches it — and so
    pydantic recognises it as a validation failure. See the module docstring for
    what that means for the exception type callers actually see.
    """

    def __init__(self, message: str, *, path: str | None = None) -> None:
        self.path = path
        super().__init__(f"{message} (at {path})" if path else message)


# ── the schema, read from altair ─────────────────────────────────────────────

@lru_cache(maxsize=1)
def _schema() -> dict[str, Any]:
    """The Vega-Lite v6.4 schema, read out of the installed altair package.

    Offline: `importlib.resources` reads from site-packages, never the network.
    """
    from importlib.resources import files

    raw = files(_SCHEMA_PACKAGE).joinpath(_SCHEMA_RESOURCE).read_text(encoding="utf-8")
    return json.loads(raw)


@lru_cache(maxsize=1)
def _validator():  # -> jsonschema.protocols.Validator
    """A compiled Draft-07 validator for the schema.

    Compiling is the expensive part; it happens once per process, and only the
    first time a chart is validated — nothing imports this eagerly.
    """
    from jsonschema import Draft7Validator  # local: keeps import cost off startup

    return Draft7Validator(_schema())


@lru_cache(maxsize=1)
def _top_level_keys() -> frozenset[str]:
    """Every property Vega-Lite allows at the top level, read off the schema.

    Derived rather than hand-listed so the allowlist tracks the schema version
    automatically. Walks `#/definitions/TopLevelSpec` through `$ref` and
    `anyOf`/`allOf`/`oneOf` branches, unioning each branch's `properties`.
    """
    defs = _schema().get("definitions", {})

    def walk(node: Any, depth: int, seen: frozenset[str]) -> set[str]:
        out: set[str] = set()
        if depth > 8 or not isinstance(node, dict):
            return out
        ref = node.get("$ref")
        if isinstance(ref, str):
            name = ref.split("/")[-1]
            if name in seen:
                return out
            return walk(defs.get(name, {}), depth + 1, seen | {name})
        for combinator in ("anyOf", "allOf", "oneOf"):
            for branch in node.get(combinator, []) or []:
                out |= walk(branch, depth + 1, seen)
        props = node.get("properties")
        if isinstance(props, dict):
            out |= set(props)
        return out

    keys = walk({"$ref": "#/definitions/TopLevelSpec"}, 0, frozenset())
    if not keys:  # pragma: no cover - only if altair ships a shape we don't expect
        raise ChartSpecError("Vega-Lite schema exposes no TopLevelSpec properties")
    return frozenset(keys | {"$schema"})


# ── structural walk ──────────────────────────────────────────────────────────

# Depth guard: real specs nest ~8 levels (layer -> spec -> encoding -> field ->
# scale -> domain). 64 is far past anything legitimate and stops a pathological
# spec from exhausting the recursion limit during validation, which would turn a
# bad chart into a 500.
_MAX_DEPTH = 64


# Row payloads, not spec structure. The structural rules below (`url`, `href`,
# `expr`, `image`) are about what the SPEC instructs the renderer to do; a data
# row that happens to have a column called `url` is a value, not an instruction.
# Descending into rows would reject a perfectly good "top referrer URLs" chart —
# and a rule that fires on the data is a rule people learn to route around.
_DATA_KEYS = frozenset({"values", "datasets"})


def _walk(node: Any, path: str = "$", depth: int = 0) -> Iterator[tuple[str, Any]]:
    """Yield `(json_path, node)` for every structural dict/list node in the tree.

    A full recursive walk is what makes the nested cases (`layer[]`, `hconcat[]`,
    `vconcat[]`, `concat[]`, `facet`+`spec`, `repeat`+`spec`) fall out for free
    instead of needing a hand-maintained list of container keys — the container
    list is exactly what a future Vega-Lite version would silently extend.

    Inline row payloads (`data.values`, `datasets.*`) are NOT descended into —
    see `_DATA_KEYS`. Their *shape* is still checked, just not their contents.
    """
    if depth > _MAX_DEPTH:
        raise ChartSpecError(
            f"spec nests deeper than {_MAX_DEPTH} levels; rejected as malformed",
            path=path,
        )
    yield path, node
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _DATA_KEYS:
                continue
            yield from _walk(value, f"{path}.{key}", depth + 1)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _walk(value, f"{path}[{i}]", depth + 1)


def _mark_type(mark: Any) -> str | None:
    if isinstance(mark, str):
        return mark
    if isinstance(mark, dict):
        kind = mark.get("type")
        return kind if isinstance(kind, str) else None
    return None


def validate_vega_lite_spec(spec: Any) -> None:
    """Raise `ChartSpecError` unless `spec` is a safe, schema-valid VL spec.

    Pure function, no side effects — safe to call on anything, including a spec
    that came straight out of the model sandbox.
    """
    if not isinstance(spec, dict):
        raise ChartSpecError(f"spec must be a JSON object, got {type(spec).__name__}")

    # 1-4. one walk, four structural rules.
    for path, node in _walk(spec):
        if not isinstance(node, dict):
            continue

        # Row payloads are not descended into (see `_DATA_KEYS`), so their SHAPE
        # is checked here instead: an inline row set must be an array of rows (or
        # a raw string, which is how Vega-Lite carries inline CSV/TSV). That is
        # what makes "not descended into" safe — a dict hiding under `values`
        # would be spec structure smuggled past the rules below.
        values = node.get("values")
        if values is not None and not isinstance(values, (list, str)):
            raise ChartSpecError(
                "inline 'values' must be an array of rows",
                path=f"{path}.values",
            )

        if "url" in node:
            raise ChartSpecError(
                "chart specs are data-closed: a 'url' key is not allowed "
                "(the spec must carry its own rows and never fetch)",
                path=f"{path}.url",
            )

        # `expr` anywhere, not just in transforms: it reaches specs as value
        # refs (`{"expr": …}`) and inside `params` too, and an emitter has no
        # reason to defer a computation to render time.
        if "expr" in node:
            raise ChartSpecError(
                "expression bindings are not allowed in chart specs "
                "(compute the value in Python and inline the result)",
                path=f"{path}.expr",
            )

        if "mark" in node and _mark_type(node["mark"]) == "image":
            raise ChartSpecError(
                "'image' marks are not allowed: the renderer would fetch the "
                "image, which breaks the data-closed guarantee",
                path=f"{path}.mark",
            )

        # Blanket, not an enumeration of channels. Enumerating is how the gap
        # appears: `href` is an encoding channel today, and the next version of
        # Vega-Lite is free to accept it somewhere else.
        if "href" in node:
            raise ChartSpecError(
                "'href' is not allowed anywhere in a chart spec: it puts a "
                "spec-chosen outbound link into a chart the user is invited to "
                "click, out of a sandboxed report",
                path=f"{path}.href",
            )

    datasets = spec.get("datasets")
    if datasets is not None:
        if not isinstance(datasets, dict):
            raise ChartSpecError("'datasets' must be an object", path="$.datasets")
        for name, rows in datasets.items():
            if not isinstance(rows, list):
                raise ChartSpecError(
                    "'datasets' entries must be inline row arrays",
                    path=f"$.datasets.{name}",
                )

    # 5. the theme is ours; a spec does not get to replace it.
    #    Vega-Lite's `config` REPLACES the config handed to the renderer rather
    #    than merging with it, so one model-authored chart carrying a `config`
    #    would silently opt itself — and only itself — out of the Sprntly theme,
    #    which reads as a rendering bug rather than as a spec problem.
    if "config" in spec:
        raise ChartSpecError(
            "top-level 'config' is not allowed: the theme is applied at render "
            "time (app.charts.theme) and a spec-level config replaces it",
            path="$.config",
        )

    # 6. no faceting at the top level — a cross-phase structural pin, not taste.
    #    Vega-Lite's `{facet, spec}` and `{repeat, spec}` forms carry a `spec`
    #    key, which is ambiguous with the `ChartSpec` ENVELOPE's own `spec` key.
    #    The client discriminates the two by treating `facet`/`repeat` as proof
    #    that it is looking at a raw Vega-Lite spec rather than an envelope, and
    #    that only holds while nothing we emit is faceted at this level. Faceting
    #    belongs one level down, inside a layer/concat child.
    if "facet" in spec or "repeat" in spec:
        raise ChartSpecError(
            "top-level 'facet'/'repeat' is not allowed: the {facet, spec} form is "
            "structurally ambiguous with the ChartSpec envelope. Facet inside a "
            "child spec instead",
            path="$.facet" if "facet" in spec else "$.repeat",
        )

    # 7. no unknown top-level keys.
    unknown = sorted(set(spec) - _top_level_keys())
    if unknown:
        raise ChartSpecError(
            f"unknown top-level key(s) for Vega-Lite v{VL_VERSION}: "
            + ", ".join(unknown)
        )

    # 8. the schema itself (last: by far the most expensive check).
    errors = sorted(_validator().iter_errors(spec), key=lambda e: list(e.absolute_path))
    if errors:
        first = errors[0]
        where = "$" + "".join(
            f"[{p}]" if isinstance(p, int) else f".{p}" for p in first.absolute_path
        )
        raise ChartSpecError(
            f"spec is not valid Vega-Lite v{VL_VERSION}: {first.message}",
            path=where,
        )


# ── the model ────────────────────────────────────────────────────────────────

class ChartProvenance(BaseModel):
    """Where a chart's numbers came from.

    Provenance is the product's whole trust story — a chart a PM cannot trace
    back to rows is a chart they cannot defend in a room. Every field is
    optional so an exploratory one-off is not blocked, but emitters fill it in.
    """

    model_config = ConfigDict(extra="forbid")

    source: str | None = None
    """Human-readable origin, e.g. "Amplitude export, 30d" — never a customer name."""

    rows: int | None = None
    """How many underlying rows the chart summarises."""

    generated_by: str | None = None
    """Emitter name (e.g. `"emitters.timeseries"`) or `"llm"` for a free-form spec."""

    evidence_ids: list[str] = Field(default_factory=list)
    """Evidence/insight ids the chart can be traced back to."""

    notes: str | None = None
    """Method notes — the stats caveat that belongs next to the picture."""


class ChartSpec(BaseModel):
    """A Vega-Lite chart plus the data and provenance that travel with it.

    Constructing one **validates** (see module docstring); an unsafe or invalid
    spec is rejected. Use `ChartSpec.build(...)` if you want to catch
    `ChartSpecError` rather than pydantic's `ValidationError`.

    `data` is the authoritative row set and is what the table fallback renders.
    If `spec` has no `data` block, the rows are inlined into it on construction,
    so a `ChartSpec` is always self-contained by the time anything renders it.
    """

    model_config = ConfigDict(extra="forbid")

    spec: dict[str, Any]
    data: list[dict[str, Any]] = Field(default_factory=list)
    title: str | None = None
    subtitle: str | None = None
    caption: str | None = None
    provenance: ChartProvenance | None = None

    @model_validator(mode="after")
    def _close_and_validate(self) -> "ChartSpec":
        """Close the spec over the envelope's rows, then validate it.

        Injection is **unconditional**: when `data` is non-empty it becomes the
        spec's top-level data, overwriting anything already there. That is a
        cross-renderer invariant, not a preference — the client injects the same
        way, and a *conditional* injection on either side means the same stored
        chart renders from the spec's rows on one and the envelope's rows on the
        other. One chart, two pictures, no error anywhere. So: the envelope's
        `data` is the row carrier, always, on both sides.

        An empty `data` leaves the spec alone, which is what lets a model-authored
        spec that already carries its own `values` through unharmed.
        """
        spec = dict(self.spec)
        if self.data:
            spec["data"] = {"values": [dict(row) for row in self.data]}
        spec.setdefault("$schema", VEGA_LITE_SCHEMA_URL)
        validate_vega_lite_spec(spec)
        object.__setattr__(self, "spec", spec)
        return self

    def row_count(self) -> int:
        """Rows this chart would actually draw, from wherever they live.

        The envelope's rows if it has any, else the spec's own inline `values`.
        Zero means there is nothing to draw — see `render.render_svg`, which
        degrades to the table rather than emitting an empty plot frame.
        """
        if self.data:
            return len(self.data)
        values = (self.spec.get("data") or {}).get("values")
        return len(values) if isinstance(values, list) else 0

    @classmethod
    def build(cls, **kwargs: Any) -> "ChartSpec":
        """`ChartSpec(...)` but re-raising the underlying `ChartSpecError`.

        pydantic converts a `ValueError` raised in a validator into a
        `ValidationError`, which buries both the type and the JSON path. It
        stashes the original under `ctx["error"]`, so we dig it back out — giving
        emitters and the render fallback a precise `except ChartSpecError`.
        """
        try:
            return cls(**kwargs)
        except ValidationError as exc:
            for error in exc.errors():
                original = error.get("ctx", {}).get("error")
                if isinstance(original, ChartSpecError):
                    raise original from None
            raise

    def to_payload(self) -> dict[str, Any]:
        """JSON-serialisable form for API/DB storage and the Phase 2 frontend.

        **The emitted `spec` is self-contained: it carries its rows.** Emitters
        build data-free specs and `_close_and_validate` inlines the envelope's
        `data` at construction, so by the time anything serialises a `ChartSpec`
        the spec renders on its own. A client that passes it to `vega-embed`
        verbatim gets a chart, not a titled empty box — the failure mode where a
        data-free spec renders blank with no exception (so no degrade-to-table,
        and no data shown either) cannot happen here.

        The cost is that rows appear twice on the wire: once in `spec.data.values`
        for the renderer, once in `data` for the table view and provenance. If
        that duplication ever matters more than the guarantee, strip
        `spec["data"]` here when it equals `{"values": self.data}` — the model
        validator re-inlines it on the way back in, so the round trip is lossless
        — but only once every client injects from the envelope.

        Note the *block* wire format is a different thing: `{type: "chart"}`
        blocks keep `data` meaning legacy `PrdChartDatum[]` (`{label, value}`)
        forever, and a `ChartSpec` travels in an additive `spec` field beside it.
        Emitter rows must never be written into that block-level `data`.
        """
        return self.model_dump(mode="json", exclude_none=True)
