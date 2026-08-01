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
 * than degrading one chart. A legitimate Vega-Lite spec nests a handful of
 * levels (layer inside concat inside facet); 24 is far past anything real.
 */
const MAX_SPEC_DEPTH = 24

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

/** A spec object that is really an envelope wrapping the actual spec. */
function isChartSpecEnvelope(v: VegaLiteSpec): boolean {
  if (!isPlainObject(v.spec)) return false
  // `facet` and `repeat` specs ALSO have a `spec` key — they are operators with
  // a single-view child. The envelope never carries one, so their presence
  // means this is a real Vega-Lite spec, not an envelope.
  if ("facet" in v || "repeat" in v) return false
  return true
}

function rowsOf(v: unknown): ChartTableRow[] {
  if (!Array.isArray(v)) return []
  return v.filter((r): r is ChartTableRow => isPlainObject(r))
}

/**
 * Unwrap an envelope (if that is what we were handed) into the Vega-Lite spec
 * to embed plus the rows behind it. A bare Vega-Lite spec passes through.
 */
export function resolveChartSpec(input: VegaLiteSpec): {
  vlSpec: VegaLiteSpec
  rows: ChartTableRow[]
  title?: string
  subtitle?: string
  caption?: string
} {
  if (!isPlainObject(input)) return { vlSpec: input, rows: [] }
  if (!isChartSpecEnvelope(input)) {
    return { vlSpec: input, rows: specDataRows(input) }
  }
  const inner = { ...(input.spec as Record<string, unknown>) } as VegaLiteSpec
  const rows = rowsOf(input.data)
  // Inject only when the inner spec has none of its own. Phase 0's renderer
  // assigns unconditionally, but the envelope contract says the spec is
  // data-free, so an inner spec that DOES carry data is non-conforming and
  // clobbering it would silently discard the rows an author put there.
  if (!("data" in inner) && rows.length > 0) {
    inner.data = { values: rows }
  }
  return {
    vlSpec: inner,
    rows: rows.length > 0 ? rows : specDataRows(inner),
    title: typeof input.title === "string" ? input.title : undefined,
    subtitle: typeof input.subtitle === "string" ? input.subtitle : undefined,
    caption: typeof input.caption === "string" ? input.caption : undefined,
  }
}

/**
 * Rows for the table disclosure.
 *
 * Reads the ENVELOPE's `data` first — that is the row carrier in the Phase 0
 * contract — and only then walks the spec, for a bare data-closed spec that
 * inlines its own values.
 *
 * The walk descends into `layer`/`concat`/`hconcat`/`vconcat`/`spec` rather
 * than reading only the top level. The interesting Phase 4 charts (interrupted
 * time series, difference-in-differences, Kaplan-Meier) are ALL layered specs
 * whose data hangs off a layer, not the root — a top-level-only read returns
 * nothing for exactly the charts whose numbers a reader most wants to check.
 */
export function specDataRows(spec: VegaLiteSpec | null | undefined): ChartTableRow[] {
  if (!isPlainObject(spec)) return []

  if (isChartSpecEnvelope(spec)) {
    const envelopeRows = rowsOf(spec.data)
    if (envelopeRows.length > 0) return envelopeRows
    return specDataRows(spec.spec as VegaLiteSpec)
  }

  const walk = (node: unknown, depth: number): ChartTableRow[] | null => {
    if (depth > MAX_SPEC_DEPTH || !isPlainObject(node)) return null

    const data = node.data
    if (isPlainObject(data)) {
      const rows = rowsOf(data.values)
      if (rows.length > 0) return rows
    }

    for (const key of ["layer", "concat", "hconcat", "vconcat", "spec"]) {
      const child = node[key]
      if (Array.isArray(child)) {
        for (const c of child) {
          const rows = walk(c, depth + 1)
          if (rows) return rows
        }
      } else if (child) {
        const rows = walk(child, depth + 1)
        if (rows) return rows
      }
    }
    return null
  }

  return walk(spec, 0) ?? []
}

/* ------------------------------------------------------------------ *
 * Outbound-reference guard
 *
 * A spec is DATA-CLOSED: it carries its rows and never reaches the network.
 * The backend validator enforces that too, but this component also renders
 * model-authored specs — a ```chart fence in a generated PRD, ultimately
 * steerable by whatever text the KG ingested from Jira/Slack/Drive — so refuse
 * here as well rather than trusting a single gate.
 *
 * Three ways a spec reaches outward, all rejected:
 *  - `data.url` / `datasets[*].url` — a fetch, the obvious one.
 *  - `mark: "image"` (or `{type: "image"}`) — Vega-Lite renders a real
 *    `<image>` element, so the reader's browser fetches a third-party URL from
 *    app.sprntly.ai, carrying their IP and Referer. A tracking pixel inside a
 *    document they trust.
 *  - `href` — as an encoding channel or a mark property, Vega-Lite wraps the
 *    mark in a real `<a>` in OUR OWN DOM. A clickable outbound link the reader
 *    has every reason to believe we authored.
 *
 * The last two are worse here than in the sandboxed-iframe case the plan
 * worried about, precisely because this path is trusted first-party DOM.
 *
 * Implemented as a blanket rejection of the `url` and `href` KEYS anywhere in
 * the spec structure, which is broader than enumerating channels and cannot be
 * out-manoeuvred by a channel we forgot. Row data is skipped: a data COLUMN
 * legitimately named "url" is a string in a table, not a fetch.
 * ------------------------------------------------------------------ */
export function specReachesOutward(spec: VegaLiteSpec): boolean {
  const isImageMark = (mark: unknown): boolean =>
    mark === "image" || (isPlainObject(mark) && mark.type === "image")

  const walk = (node: unknown, depth: number): boolean => {
    if (depth > MAX_SPEC_DEPTH) return true // too deep to vet — refuse it
    if (Array.isArray(node)) return node.some((c) => walk(c, depth + 1))
    if (!isPlainObject(node)) return false

    for (const [key, value] of Object.entries(node)) {
      if (key === "url" || key === "href") return true
      if (key === "mark" && isImageMark(value)) return true
      // `values` is inline row data, and `datasets` values are too.
      if (key === "values") continue
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
    if (specReachesOutward(vlSpec)) {
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
            <ChartDataTable
              rows={rows}
              variant="static"
              note="This chart couldn’t be drawn — here is the data behind it."
            />
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
