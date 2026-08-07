/**
 * Legacy `{kind, data}` chart blocks → Vega-Lite v6 specs.
 *
 * ---------------------------------------------------------------------------
 * FIDELITY, HONESTLY
 * ---------------------------------------------------------------------------
 * `InlineChart`'s six kinds are not generic charts. They are hand-designed
 * infographic components tuned to the PRD/evidence visual language — CSS custom
 * properties (`--surface`, `--accent`, `--ink`), a bar "track", a two-column
 * legend carrying value *and* percentage, a bespoke two-line axis-label wrapper
 * that deliberately splits before a parenthetical. Vega-Lite reproduces the
 * *data*, not the design:
 *
 *   bar    Today: `label | grey track | coloured fill | value` rows, no axis.
 *          Vega-Lite: real bars on a quantitative axis. Reads fine, looks
 *          different. NOT a faithful reproduction.
 *   line   Today: polyline + grid + the custom label wrapper added on purpose
 *          (see `wrapAxisLabel`, which splits "Searched 3P repair (Day 7)"
 *          onto two lines). Vega-Lite truncates long labels with an ellipsis
 *          instead. Migrating regresses a fix someone shipped deliberately.
 *   pie    Arc geometry IS reproducible (Vega-Lite's theta starts at 12
 *          o'clock, clockwise — same as the hand-rolled path). The DOM legend
 *          with "value (pct%)" is not. Zero visual upside for ~1 MB of runtime.
 *   donut  As pie, plus the hole is filled with `var(--surface)` so it picks up
 *          the surrounding card. Vega-Lite's `innerRadius` cuts a real hole.
 *          Different on any non-default background.
 *   stat   A number, not a chart. Stays DOM, permanently.
 *   gauge  180° arc with a gradient stroke, a target tick, centred value text
 *          and a two-row legend. Vega-Lite has no gauge mark; approximating it
 *          with `arc` loses the round stroke caps, the gradient and the tick.
 *          Stays DOM.
 *
 * So: **no legacy kind is migrated by default.** `COMPILE_LEGACY_KINDS` is
 * the switch, and it is off. A stored PRD that renders one way today renders
 * exactly the same way after this change — which is the whole point of Phase 2
 * keeping the old contract alive rather than replacing it.
 *
 * What this module is FOR: the compiled specs are the forward path. When the
 * backend starts emitting `spec` for these kinds (or when design signs off on
 * the Vega look for one of them), flipping it is one boolean
 * below, guarded by the snapshot tests in `__tests__/chart-adapter.test.ts`.
 */
import type { PrdChartDatum, PrdChartKind, VegaLiteSpec } from "../types/content"
import { CHART_THEME } from "./chart-theme"

const VL_SCHEMA = CHART_THEME.vegaLiteSchema

/**
 * Route legacy `{kind, data}` blocks through the Vega renderer instead of their
 * DOM implementation?
 *
 * OFF ON PURPOSE — see the fidelity table above. Flipping this changes the look
 * of every stored PRD and evidence document containing a chart, so it needs a
 * staging eyeball and design sign-off, not just a green test run. It is a
 * deliberate deviation from plan §3, which assumed the six kinds would compile
 * across cleanly; reading the actual implementations says otherwise.
 */
export const COMPILE_LEGACY_KINDS = false

/** Kinds `kindToVegaLite` knows how to compile at all. */
export const COMPILABLE_KINDS: readonly PrdChartKind[] = ["bar", "line", "pie", "donut"]

/**
 * Normalize a candidate `spec` field off a parsed chart block.
 *
 * A spec is a JSON object — never an array, never a string we would have to
 * re-parse, never a scalar. Anything else is treated as ABSENT so the legacy
 * `{kind, data}` path still runs: a malformed spec must degrade a block to its
 * old rendering, never drop it.
 */
export function extractSpec(value: unknown): VegaLiteSpec | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined
  return value as VegaLiteSpec
}

/** Same numeric coercion the DOM renderer uses, so both paths agree. */
export function toNumber(v: number | string): number {
  if (typeof v === "number") return v
  const m = String(v).replace(/[^\d.\-]/g, "")
  const n = parseFloat(m)
  return Number.isFinite(n) ? n : 0
}

/** `{label, value}` rows, as table rows for the data disclosure. */
export function chartDatumRows(data: PrdChartDatum[]): Array<Record<string, unknown>> {
  return (data || []).map((d) => ({ label: d.label, value: d.value }))
}

type Row = { label: string; value: number; display: string }

function rows(data: PrdChartDatum[]): Row[] {
  return (data || []).map((d) => ({
    label: String(d.label ?? ""),
    value: toNumber(d.value),
    display: typeof d.value === "string" ? d.value : String(d.value),
  }))
}

const CATEGORY_SCALE = { range: CHART_THEME.categorical }

/**
 * Compile a legacy chart block to a Vega-Lite v6 spec, or `null` for kinds that
 * have no chart form (`stat`) or no faithful one (`gauge`).
 */
export function kindToVegaLite(
  kind: PrdChartKind,
  data: PrdChartDatum[],
  opts: { title?: string } = {},
): VegaLiteSpec | null {
  const values = rows(data)
  if (values.length === 0) return null
  const title = opts.title
  const base: Record<string, unknown> = {
    $schema: VL_SCHEMA,
    ...(title ? { title } : {}),
    data: { values },
  }

  switch (kind) {
    case "bar":
      return {
        ...base,
        // Horizontal, category on Y — matches the row-per-label reading order
        // of the DOM implementation even though the chrome differs.
        mark: { type: "bar", cornerRadiusEnd: 3, height: { band: 0.7 } },
        encoding: {
          y: {
            field: "label",
            type: "nominal",
            sort: null,
            axis: { title: null, labelLimit: 160 },
          },
          x: {
            field: "value",
            type: "quantitative",
            axis: { title: null, grid: true, tickCount: 4 },
          },
          color: {
            field: "label",
            type: "nominal",
            sort: null,
            scale: CATEGORY_SCALE,
            legend: null,
          },
          tooltip: [
            { field: "label", type: "nominal", title: "Label" },
            { field: "display", type: "nominal", title: "Value" },
          ],
        },
      }

    case "line":
      return {
        ...base,
        mark: {
          type: "line",
          strokeWidth: 2.5,
          color: CHART_THEME.categorical[0],
          point: { filled: true, size: 40, color: CHART_THEME.categorical[0] },
        },
        encoding: {
          x: {
            field: "label",
            type: "ordinal",
            sort: null,
            axis: { title: null, labelAngle: 0, labelLimit: 120 },
          },
          y: {
            field: "value",
            type: "quantitative",
            axis: { title: null, grid: true, tickCount: 4 },
          },
          tooltip: [
            { field: "label", type: "nominal", title: "Label" },
            { field: "display", type: "nominal", title: "Value" },
          ],
        },
      }

    case "pie":
    case "donut":
      return {
        ...base,
        mark: {
          type: "arc",
          // The DOM donut cuts a hole at 55% of the radius; match that ratio.
          ...(kind === "donut" ? { innerRadius: 44, outerRadius: 80 } : { outerRadius: 80 }),
        },
        encoding: {
          theta: { field: "value", type: "quantitative", stack: true },
          color: {
            field: "label",
            type: "nominal",
            sort: null,
            scale: CATEGORY_SCALE,
            legend: { title: null },
          },
          tooltip: [
            { field: "label", type: "nominal", title: "Label" },
            { field: "display", type: "nominal", title: "Value" },
          ],
        },
      }

    // `stat` is a number, not a chart. `gauge` has no faithful Vega-Lite form.
    case "stat":
    case "gauge":
    default:
      return null
  }
}
