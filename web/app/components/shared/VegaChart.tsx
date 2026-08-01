"use client"

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react"
// `import type` is fully erased by TypeScript — it adds nothing to the bundle
// and creates no module edge. The only runtime reference to vega is the
// `await import(...)` inside the effect below.
import type { Result as VegaEmbedResult } from "vega-embed"
import { clientConfig } from "../../lib/chart-theme"
import type { VegaLiteSpec } from "../../types/content"
import { ChartDataTable, type ChartTableRow } from "./ChartDataTable"

/* ------------------------------------------------------------------ *
 * Why a bare `await import("vega-embed")` in an effect, and not
 * `next/dynamic`:
 *
 * 1. `next/dynamic` lazy-loads a *component module*. `vega-embed` exports an
 *    imperative function that mounts into a DOM node we own — wrapping it in a
 *    synthetic component to satisfy `next/dynamic` buys nothing.
 * 2. We need the load and the render to share one state machine. A failed
 *    import, a failed compile and a failed render all have to land in the same
 *    place: degrade to the data table. `next/dynamic`'s loading/error hooks
 *    can express the first but not the other two without an extra error
 *    boundary per chart.
 * 3. SSR safety: `vega-embed` touches `window`/`document`/`canvas` at module
 *    scope and is not SSR-safe. Effects never run on the server, so this import
 *    is *structurally* unreachable during prerender — stronger than
 *    `next/dynamic({ ssr: false })`, which still has the module in the client
 *    manifest for the route.
 *
 * The promise is cached at module scope so N charts on a page trigger ONE
 * network fetch of the vega chunk, and every subsequent chart mounts against
 * the already-resolved module.
 * ------------------------------------------------------------------ */
type VegaRuntime = {
  embed: typeof import("vega-embed").default
  expressionInterpreter: unknown
}

let runtimePromise: Promise<VegaRuntime> | null = null
let runtimeLoadCount = 0

async function loadVegaRuntime(): Promise<VegaRuntime> {
  if (!runtimePromise) {
    runtimeLoadCount += 1
    runtimePromise = Promise.all([
      import("vega-embed"),
      import("vega-interpreter"),
    ])
      .then(([embedMod, interpreterMod]) => ({
        embed: embedMod.default,
        expressionInterpreter: interpreterMod.expressionInterpreter,
      }))
      .catch((err) => {
        // Don't cache a rejected promise — a transient chunk-load failure
        // should not permanently poison every chart on the page.
        runtimePromise = null
        throw err
      })
  }
  return runtimePromise
}

/** Reset the cached runtime. Test-only seam. */
export function __resetVegaRuntimeForTests() {
  runtimePromise = null
  runtimeLoadCount = 0
}

/** How many times the runtime was actually fetched. Test-only seam — this is
 *  what pins "N charts on a page trigger ONE import" to the module-scope cache
 *  rather than to vitest's own module registry. */
export function __vegaRuntimeLoadCount() {
  return runtimeLoadCount
}

/**
 * How deep we are willing to walk a spec.
 *
 * Both walkers below recurse over attacker-influenced JSON and BOTH run during
 * render, outside any try/catch — an unbounded recursion there is a RangeError
 * thrown out of React's render phase, which takes down the whole page rather
 * than degrading one chart.
 *
 * 64 on both sides, pinned by `limits.maxDepth` in the shared row-extraction
 * fixture rather than left as two constants that happen to match today. Both
 * count every JSON level. It has
 * to be generous: `facet -> spec -> vconcat -> layer -> encoding -> color ->
 * condition -> scale -> domain` is already ~12 levels before any transform
 * detail, and each nesting step costs two here (object, then array element). A
 * cap that trips on a deep-but-VALID spec would have the browser refuse a chart
 * the server renders fine — the exact client/server divergence this contract
 * exists to prevent.
 */
export const MAX_SPEC_DEPTH = 64

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === "object" && !Array.isArray(v)
}

/* ------------------------------------------------------------------ *
 * The ChartSpec envelope
 *
 * Phase 0's backend contract is an ENVELOPE — `{spec, data, title, subtitle,
 * caption, provenance}` — where `spec` is a data-FREE Vega-Lite spec and `data`
 * carries the rows. Its server renderer injects the rows into the spec right
 * before handing it to vl-convert. This client has to do the identical
 * injection, or a spec that renders correctly server-side comes out BLANK here:
 * vega-embed resolves happily on a spec with no data, so it reaches `ready`,
 * never throws, and never degrades — a blank box with no table under it.
 *
 * Same input, same chart, both renderers. That is the whole point of having one
 * spec contract, and it is the one thing a type can't enforce across the wire.
 *
 * The block-level `data` field is NOT this. That field is `PrdChartDatum[]` —
 * the legacy `{label, value}` contract — and it stays legacy-only, forever. It
 * never feeds the Vega path.
 * ------------------------------------------------------------------ */

/**
 * A spec object that is really an envelope wrapping the actual spec.
 *
 * `facet` and `repeat` specs ALSO have a `spec` key — they are operators with a
 * single-view child — so their presence is taken as proof this is a real
 * Vega-Lite spec rather than an envelope.
 *
 * THAT DISCRIMINATION DEPENDS ON A GUARANTEE FROM THE EMITTING END: Phase 0's
 * envelope must never carry `facet` or `repeat` at top level. It is pinned on
 * the backend side as well, so the ambiguity cannot arise from either
 * direction. If that ever changes, this function is where it breaks.
 */
function isChartSpecEnvelope(v: VegaLiteSpec): boolean {
  if (!isPlainObject(v.spec)) return false
  if ("facet" in v || "repeat" in v) return false
  return true
}

function rowsOf(v: unknown): ChartTableRow[] {
  if (!Array.isArray(v)) return []
  return v.filter((r): r is ChartTableRow => isPlainObject(r))
}

/** The container keys a Vega-Lite spec can nest child specs under. */
const CONTAINER_KEYS = ["layer", "concat", "hconcat", "vconcat", "spec"] as const

/**
 * Top-level keys removed from every spec before it reaches `embed()`.
 *
 * THIS IS A SECURITY BOUNDARY, not tidying.
 *
 * `usermeta` — `vega-embed` treats `spec.usermeta.embedOptions` as CALLER
 * OPTIONS and merges them over the options we pass, with the SPEC winning. A
 * model-authored spec could set `actions: true` (restoring the actions menu,
 * including "Open in Vega Editor"), `ast: false` (dropping the interpreter and
 * putting expressions back through `Function`-constructor codegen, voiding the
 * whole CSP argument above), or a STRING `patch`/`config`, which vega-embed
 * loads via `loader.load()` — an unconditional HTTP GET of an attacker-chosen
 * URL whose JSON is then applied as a JSON-Patch to the compiled Vega spec.
 * That last one is arbitrary Vega control: further fetches, signals,
 * expressions.
 *
 * `config` — a spec's own `config` wins over the `config` we pass, so
 * `{"config": {"background": "#ff0000"}}` repaints a chart straight out of the
 * Sprntly theme. The theme is the contract that keeps the server SVG renderer
 * and this one looking like the same product; a spec does not get to opt out.
 *
 * `specViolatesContract` cannot catch either. Its key-based rejection is the
 * right shape for encodings and marks, but a JSON-Patch hides its URL in a
 * VALUE — `{"op":"add","path":"/data/url","value":"https://…"}` — where no key
 * check will ever see it.
 *
 * STRIPPED rather than rejected, on purpose: altair writes a benign `usermeta`
 * and a benign `config`, so refusing on their presence would degrade
 * legitimate charts. Nothing in our render path reads either, so dropping them
 * costs nothing and the chart still draws. Top level only — that is where
 * `vega-embed` looks for caller options and where Vega-Lite reads `config`.
 */
const STRIPPED_TOP_LEVEL_KEYS = ["usermeta", "config"] as const

function sanitizeSpec(spec: VegaLiteSpec): VegaLiteSpec {
  const copy = { ...(spec as Record<string, unknown>) }
  for (const key of STRIPPED_TOP_LEVEL_KEYS) delete copy[key]
  return copy as VegaLiteSpec
}

/** The root `datasets` map, which named `data: {name}` references resolve against. */
function datasetsOf(root: VegaLiteSpec): Record<string, unknown> {
  const ds = isPlainObject(root) ? root.datasets : undefined
  return isPlainObject(ds) ? ds : {}
}

/**
 * The rows one node's `data` points at — inline, or through a named reference.
 *
 * Inline `values` wins over a named reference on the same node ONLY when it is
 * non-empty: `vega-lite@6.4.3` resolves `{"name": "d", "values": []}` to the
 * named dataset, verified by compiling it.
 *
 * `data: {"name": "data-<hash>"}` + a top-level `datasets` map is what
 * `altair.to_dict()` emits by DEFAULT (its default data transformer names and
 * hoists the rows rather than inlining them). Phase 1's DS sandbox writes
 * altair specs, so this is the ordinary shape, not an exotic one — and a
 * reader that only understands inline `values` reports those charts as having
 * no data at all.
 */
function resolveNodeRows(
  data: unknown,
  datasets: Record<string, unknown>,
): ChartTableRow[] {
  if (!isPlainObject(data)) return []
  const inline = rowsOf(data.values)
  if (inline.length > 0) return inline
  if (typeof data.name === "string") return rowsOf(datasets[data.name])
  return []
}

/**
 * Rows for the table disclosure.
 *
 * Reads the ENVELOPE's `data` first — that is the row carrier in the Phase 0
 * contract — and only then walks the spec.
 *
 * ONE SHARED DEFINITION OF WHERE ROWS LIVE, matching `ChartSpec.row_count()`
 * on the backend: the root, any nested container (`layer`/`concat`/`hconcat`/
 * `vconcat`/`spec`), AND named `datasets` references. Pinned across both
 * implementations by the fixture at
 * `app/lib/__fixtures__/chart-row-extraction.json`.
 *
 * The container walk matters because the interesting Phase 4 charts
 * (interrupted time series, difference-in-differences, Kaplan-Meier) are ALL
 * layered specs whose data hangs off a layer, not the root — a root-only read
 * returns nothing for exactly the charts whose numbers a reader most wants to
 * check.
 */
export function specDataRows(spec: VegaLiteSpec | null | undefined): ChartTableRow[] {
  if (!isPlainObject(spec)) return []

  if (isChartSpecEnvelope(spec)) {
    const envelopeRows = rowsOf(spec.data)
    if (envelopeRows.length > 0) return envelopeRows
    return specDataRows(spec.spec as VegaLiteSpec)
  }

  const datasets = datasetsOf(spec)

  const walk = (node: unknown, depth: number): ChartTableRow[] | null => {
    if (depth > MAX_SPEC_DEPTH || !isPlainObject(node)) return null

    const rows = resolveNodeRows(node.data, datasets)
    if (rows.length > 0) return rows

    for (const key of CONTAINER_KEYS) {
      const child = node[key]
      if (Array.isArray(child)) {
        for (const c of child) {
          const found = walk(c, depth + 1)
          if (found) return found
        }
      } else if (child) {
        const found = walk(child, depth + 1)
        if (found) return found
      }
    }
    return null
  }

  return walk(spec, 0) ?? []
}

/**
 * Does this spec state where its rows are — even if the answer is "none"?
 *
 * The difference between "no rows" and "rows we cannot see from here" is the
 * difference between a chart we KNOW will be blank and one whose data arrives
 * through a `sequence` generator or some other path this reader does not model.
 * A `values` array that is present and empty is decidably rowless, as is a
 * named reference resolving to an empty dataset. The absence of any statement
 * at all is not decidable and gets the benefit of the doubt.
 */
function specHasExplicitValues(spec: VegaLiteSpec): boolean {
  const datasets = datasetsOf(spec)

  const walk = (node: unknown, depth: number): boolean => {
    if (depth > MAX_SPEC_DEPTH || !isPlainObject(node)) return false
    const data = node.data
    if (isPlainObject(data)) {
      if (Array.isArray(data.values)) return true
      if (typeof data.name === "string" && Array.isArray(datasets[data.name])) return true
    }
    for (const key of CONTAINER_KEYS) {
      const child = node[key]
      if (Array.isArray(child)) {
        if (child.some((c) => walk(c, depth + 1))) return true
      } else if (child && walk(child, depth + 1)) {
        return true
      }
    }
    return false
  }
  return walk(spec, 0)
}

/**
 * Unwrap an envelope (if that is what we were handed) into the Vega-Lite spec
 * to embed plus the rows behind it. A bare Vega-Lite spec passes through.
 */
export function resolveChartSpec(input: VegaLiteSpec): {
  vlSpec: VegaLiteSpec
  rows: ChartTableRow[]
  /** False when we can PROVE the spec has nothing to draw — embed would
   *  succeed and paint an empty box. */
  drawable: boolean
  title?: string
  subtitle?: string
  caption?: string
} {
  if (!isPlainObject(input)) return { vlSpec: input, rows: [], drawable: true }

  if (!isChartSpecEnvelope(input)) {
    const rows = specDataRows(input)
    return {
      vlSpec: sanitizeSpec(input),
      rows,
      // Rows we found, or rows we cannot see from here (a `sequence`
      // generator, a named `datasets` reference). Only a `values` array that
      // is PRESENT and empty proves there is nothing to draw.
      drawable: rows.length > 0 || !specHasExplicitValues(input),
    }
  }

  const original = input.spec as VegaLiteSpec
  const inner = sanitizeSpec(original)
  const rows = rowsOf(input.data)
  const innerRows = specDataRows(original)

  // CONDITIONAL, mirroring `backend/app/charts/spec.py` exactly:
  //
  //     if self.data:
  //         spec["data"] = {"values": [dict(row) for row in self.data]}
  //
  // The carve-out is load-bearing on both ends, not an oversight. `ChartSpec.data`
  // defaults to `[]`, and Phase 1's DS sandbox writes altair `*.vl.json` files
  // that carry their own rows — inline as `data.values`, or (altair's DEFAULT)
  // as `data: {name}` plus a top-level `datasets` map. Either way
  // `{spec: <carries its own rows>, data: []}` is a routine shape, not an
  // exotic one, and assigning unconditionally would blank every one of those
  // charts in the browser while the server drew them correctly.
  if (rows.length > 0) {
    inner.data = { values: rows }
  }

  return {
    vlSpec: inner,
    // The table reaches for the inner spec's rows when the envelope has none,
    // mirroring `ChartSpec.row_count()`'s fallback to `spec["data"]["values"]`.
    rows: rows.length > 0 ? rows : innerRows,
    drawable: rows.length > 0 || innerRows.length > 0,
    title: typeof input.title === "string" ? input.title : undefined,
    subtitle: typeof input.subtitle === "string" ? input.subtitle : undefined,
    caption: typeof input.caption === "string" ? input.caption : undefined,
  }
}

/* ------------------------------------------------------------------ *
 * Contract validator
 *
 * A spec is DATA-CLOSED and INERT: it carries its rows, never reaches the
 * network, and renders a picture rather than a user interface.
 *
 * THIS IS THE ONLY GATE ON THE MODEL-AUTHORED CHANNEL. A spec arriving in a
 * ```chart fence — ultimately steerable by whatever text the KG ingested from
 * Jira/Slack/Drive/Confluence — reaches `embed()` having passed through no
 * backend validation at all. Everything `backend/app/charts/spec.py` rejects
 * for server-rendered specs has to be rejected here too, or the fence is
 * simply the way around it.
 *
 * Rejected:
 *  - `data.url` / any `url` key — a fetch.
 *  - `mark: "image"` — Vega-Lite emits a real `<image>`, so the reader's
 *    browser fetches a third-party URL FROM app.sprntly.ai, carrying their IP
 *    and Referer. A tracking pixel inside a document they trust.
 *  - `href` — as an encoding channel or a mark property, Vega-Lite wraps the
 *    mark in a real `<a>` in OUR OWN DOM. A clickable outbound link the reader
 *    has every reason to believe we authored.
 *  - `params` — inert on the server, NOT here. `vega-embed` renders
 *    `{"bind": {"input": "range"}}` as a real `<input type="range">` in the PRD
 *    panel, and `{"select": "interval"}` wires live brush/pan/zoom onto the
 *    chart. A model-authored spec does not get to put controls nobody designed
 *    into a document. This is the one the backend rejects specifically so this
 *    renderer can collect on it.
 *  - `expr` value refs — sandboxed by `ast: true` + the interpreter, so this is
 *    a client/server DIVERGENCE rather than a hole: the server would refuse the
 *    spec while the browser drew it. Same contract on both ends or neither.
 *  - a malformed inline payload — `values` that is not an array of row objects,
 *    or a `datasets` entry that is not. The walk SKIPS the contents of those
 *    two keys (a "top referrer URLs" chart legitimately has a `url` column), so
 *    the shape check is what earns the right to skip them. Without it,
 *    `{"data": {"values": {"url": "…"}}}` walks straight past the guard.
 *
 * Not handled here: `usermeta` and top-level `config`, which are STRIPPED
 * instead — see `STRIPPED_TOP_LEVEL_KEYS`. Stripping degrades a legitimate
 * altair spec instead of refusing it, and a JSON-Patch hides its URL in a
 * VALUE where no key check could see it anyway.
 * ------------------------------------------------------------------ */

/**
 * Inline data payloads must be ARRAYS. Nothing more.
 *
 * Not "arrays of objects": Vega-Lite's `InlineDataset` legitimately admits
 * `string[]` / `number[]` / `boolean[]`, which is the ordinary way to carry a
 * sort order or a tick set alongside the real rows — and the server accepts
 * them (`isinstance(rows, list)`). Demanding row objects refused specs that
 * `vl_convert` renders perfectly well.
 *
 * The array test is all the safety argument needs. The reason to shape-check at
 * all is that a DICT sitting under `values`/`datasets` is spec structure
 * smuggled past a walk that deliberately skips those keys; `[1, 2, 3]` contains
 * no dict and smuggles nothing.
 */
function isInlineDataArray(v: unknown): boolean {
  return Array.isArray(v)
}

const REJECTED_KEYS = new Set(["url", "href", "params", "expr"])

export function specViolatesContract(spec: VegaLiteSpec): boolean {
  const isImageMark = (mark: unknown): boolean =>
    mark === "image" || (isPlainObject(mark) && mark.type === "image")

  const walk = (node: unknown, depth: number): boolean => {
    if (depth > MAX_SPEC_DEPTH) return true // too deep to vet — refuse it
    if (Array.isArray(node)) return node.some((c) => walk(c, depth + 1))
    if (!isPlainObject(node)) return false

    // Shape checks BEFORE the skip below, because the skip depends on them.
    const data = node.data
    if (isPlainObject(data) && "values" in data && !isInlineDataArray(data.values)) return true
    const datasets = node.datasets
    if (isPlainObject(datasets)) {
      for (const payload of Object.values(datasets)) {
        if (!isInlineDataArray(payload)) return true
      }
    }

    for (const [key, value] of Object.entries(node)) {
      if (REJECTED_KEYS.has(key)) return true
      if (key === "mark" && isImageMark(value)) return true
      // Inline row payloads, now shape-checked above. Phase 0 skips the same
      // two keys (`_DATA_KEYS`).
      if (key === "values" || key === "datasets") continue
      if (walk(value, depth + 1)) return true
    }
    return false
  }

  return walk(spec, 0)
}

type Phase = "idle" | "loading" | "ready" | "failed"

export function VegaChart({
  spec,
  title,
  subtitle,
  caption,
  /** Overrides the rows derived from the spec (e.g. an old-format
   *  `{label, value}` block whose spec was compiled by the adapter). */
  tableRows,
  className,
  pending,
}: {
  spec: VegaLiteSpec
  title?: string
  subtitle?: string
  caption?: string
  tableRows?: ChartTableRow[]
  className?: string
  /**
   * What to show while the vega chunk is in flight.
   *
   * Callers that HAVE a non-Vega rendering of the same chart pass it here, and
   * the reader sees the real chart on first paint instead of a spinner. That
   * matters for old-format `{kind, data}` blocks: a stored PRD that renders
   * instantly today must not start showing "Loading chart…" in its place, and
   * if the vega chunk 404s after a deploy the reader still gets their chart
   * rather than a stalled placeholder. Falls back to a loading line when the
   * caller has nothing better (a spec-only block has no DOM equivalent).
   */
  pending?: ReactNode
}) {
  const hostRef = useRef<HTMLDivElement | null>(null)
  const [phase, setPhase] = useState<Phase>("idle")

  // Unwrap the Phase 0 envelope (if that is what arrived) into the spec vega
  // actually embeds. A bare Vega-Lite spec passes straight through.
  const resolved = useMemo(() => resolveChartSpec(spec), [spec])

  const rows =
    tableRows && tableRows.length > 0 ? tableRows : resolved.rows
  const shownTitle = title ?? resolved.title
  const shownSubtitle = subtitle ?? resolved.subtitle
  const shownCaption = caption ?? resolved.caption

  /*
   * The effect is keyed on a stable SERIALISATION, not on the spec's identity.
   *
   * Several callers build the spec fresh on every render — `AskReplyBody`
   * re-parses the ```chart fence out of the markdown on every simulated-stream
   * tick, so the object identity changes ~30 times while an answer types out.
   * Keyed by reference, that tore down and re-embedded every chart in the
   * thread on every tick: a visible flicker, a dropped vega view each time, and
   * work proportional to (charts x ticks). Keyed by value, the spec has to
   * actually CHANGE before we re-embed.
   */
  const specKey = useMemo(() => {
    try {
      return JSON.stringify(resolved.vlSpec)
    } catch {
      // Unserialisable (cycle, BigInt) — fall back to identity so we still
      // render, we just lose the de-duplication for this one chart.
      return null
    }
  }, [resolved.vlSpec])

  // The effect reads the CURRENT spec through a ref, so a re-render that leaves
  // `specKey` unchanged doesn't need to re-run it just to see the same value.
  const specRef = useRef(resolved.vlSpec)
  specRef.current = resolved.vlSpec
  const drawableRef = useRef(resolved.drawable)
  drawableRef.current = resolved.drawable

  useEffect(() => {
    let cancelled = false
    let result: VegaEmbedResult | null = null
    const host = hostRef.current
    if (!host) return
    const vlSpec = specRef.current

    if (!vlSpec || typeof vlSpec !== "object") {
      setPhase("failed")
      return
    }
    if (specViolatesContract(vlSpec)) {
      setPhase("failed")
      return
    }
    if (!drawableRef.current) {
      // Envelope with no rows: vega would embed cleanly, resolve, and paint an
      // empty box with nothing to explain it. Degrade at the one point where
      // the blankness is actually detectable.
      setPhase("failed")
      return
    }

    setPhase("loading")
    void (async () => {
      try {
        const { embed, expressionInterpreter } = await loadVegaRuntime()
        if (cancelled) return
        result = await embed(host, vlSpec as never, {
          actions: false,
          renderer: "svg",
          // ---- CSP safety, concretely ----------------------------------
          // Vega's DEFAULT expression handling compiles `signal`/`expr`
          // strings to JavaScript with the `Function` constructor, which a
          // strict CSP (`script-src` without `'unsafe-eval'`) blocks outright.
          // `ast: true` makes vega's parser emit an expression **AST** instead
          // of generated code, and `expr: expressionInterpreter` supplies
          // vega-interpreter's tree-walking **interpreter** to evaluate that
          // AST. Interpreter, not codegen — that pair is the CSP-safe path.
          // Both options are required: `ast` alone produces an AST nothing
          // can evaluate, `expr` alone is never reached.
          ast: true,
          expr: expressionInterpreter as never,
          // Browser font stack swapped in over the SSR one; see clientConfig().
          config: clientConfig() as never,
        })
        if (cancelled) {
          result.finalize()
          result = null
          return
        }
        setPhase("ready")
      } catch (err) {
        if (cancelled) return
        // Malformed spec, unsupported schema version, chunk-load failure —
        // all the same outcome for the reader: they get the numbers.
        if (typeof console !== "undefined") {
          console.warn("[VegaChart] render failed, degrading to table", err)
        }
        setPhase("failed")
      }
    })()

    return () => {
      cancelled = true
      try {
        result?.finalize()
      } catch {
        /* view already torn down */
      }
      // vega-embed appends its own DOM into the host; clear it so a re-render
      // with a new spec does not stack two charts.
      if (host) host.innerHTML = ""
    }
    // `specKey === null` means unserialisable; fall back to identity so such a
    // spec still re-embeds when it changes.
  }, [specKey, specKey === null ? resolved.vlSpec : null])

  return (
    <figure className={`prd-chart prd-chart-vega${className ? ` ${className}` : ""}`}>
      {shownTitle ? (
        <figcaption className="prd-chart-title">{shownTitle}</figcaption>
      ) : null}
      {shownSubtitle ? <div className="prd-chart-sub">{shownSubtitle}</div> : null}
      <div className="prd-chart-body">
        {/* Always mounted: the effect needs a node to embed into, and keeping
            it in the tree across phase changes stops React from swapping the
            host out from under a live view. */}
        <div
          ref={hostRef}
          className="vega-chart-host"
          data-testid="vega-chart-host"
          data-phase={phase}
          hidden={phase === "failed"}
        />
        {/* Anything that is not a drawn Vega view falls back the same way:
            the caller's non-Vega rendering if it has one (identical first
            paint, and it simply STAYS if vega never loads), otherwise a
            loading line while in flight and the data table once we know the
            chart is not coming. */}
        {phase !== "ready" && pending ? (
          <div className="vega-chart-pending" data-testid="vega-chart-pending">
            {pending}
          </div>
        ) : null}
        {phase !== "ready" && !pending ? (
          phase === "failed" ? (
            rows.length > 0 ? (
              <ChartDataTable
                rows={rows}
                variant="static"
                note="This chart couldn’t be drawn — here is the data behind it."
              />
            ) : (
              // Nothing to draw AND nothing to tabulate. Say so rather than
              // leaving an unexplained empty box on the page.
              <div className="chart-data-fallback-note" data-testid="vega-chart-empty">
                This chart couldn’t be drawn — no data was attached to it.
              </div>
            )
          ) : (
            <div className="vega-chart-loading" data-testid="vega-chart-loading" aria-live="polite">
              Loading chart…
            </div>
          )
        ) : null}
      </div>
      {shownCaption ? (
        <figcaption className="prd-chart-caption">{shownCaption}</figcaption>
      ) : null}
      {/* The static fallback table already IS the data; don't print it twice. */}
      {phase === "failed" && !pending ? null : <ChartDataTable rows={rows} />}
    </figure>
  )
}
