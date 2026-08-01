"use client"

import { useEffect, useRef, useState, type ReactNode } from "react"
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

async function loadVegaRuntime(): Promise<VegaRuntime> {
  if (!runtimePromise) {
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
}

/**
 * Rows for the table disclosure. A ChartSpec is *data-closed* — it carries its
 * own rows inline — so the table is derivable from the spec itself and the
 * caller never has to pass the data twice.
 *
 * Walks into `layer`/`concat`/`hconcat`/`vconcat`/`spec` rather than reading
 * only the top level. The interesting Phase 4 charts (interrupted time series,
 * difference-in-differences, Kaplan-Meier) are ALL layered specs whose data
 * hangs off the first layer, not the root — a top-level-only read returns
 * nothing for exactly the charts whose numbers a reader most wants to check.
 */
export function specDataRows(spec: VegaLiteSpec | null | undefined): ChartTableRow[] {
  const asRows = (v: unknown): ChartTableRow[] | null => {
    if (!Array.isArray(v)) return null
    const rows = v.filter(
      (r): r is ChartTableRow => !!r && typeof r === "object" && !Array.isArray(r),
    )
    return rows.length > 0 ? rows : null
  }

  const seen = new WeakSet<object>()
  const walk = (node: unknown): ChartTableRow[] | null => {
    if (!node || typeof node !== "object") return null
    if (seen.has(node as object)) return null
    seen.add(node as object)
    const obj = node as Record<string, unknown>

    const data = obj.data
    if (data && typeof data === "object") {
      const rows = asRows((data as { values?: unknown }).values)
      if (rows) return rows
    }

    for (const key of ["layer", "concat", "hconcat", "vconcat", "spec"]) {
      const child = obj[key]
      if (Array.isArray(child)) {
        for (const c of child) {
          const rows = walk(c)
          if (rows) return rows
        }
      } else if (child) {
        const rows = walk(child)
        if (rows) return rows
      }
    }
    return null
  }

  return walk(spec) ?? []
}

/**
 * A spec must never fetch. The backend validator rejects remote data, but this
 * component also renders model-authored specs that arrive through other paths,
 * so refuse here too rather than trusting a single gate.
 */
export function specFetchesRemoteData(spec: VegaLiteSpec): boolean {
  const data = (spec as { data?: unknown }).data
  if (data && typeof data === "object" && "url" in (data as object)) return true
  const datasets = (spec as { datasets?: unknown }).datasets
  if (datasets && typeof datasets === "object") {
    for (const v of Object.values(datasets as Record<string, unknown>)) {
      if (v && typeof v === "object" && !Array.isArray(v) && "url" in (v as object)) {
        return true
      }
    }
  }
  return false
}

type Phase = "idle" | "loading" | "ready" | "failed"

export function VegaChart({
  spec,
  title,
  subtitle,
  caption,
  /** Overrides the rows pulled off `spec.data.values` (e.g. an old-format
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

  const rows = tableRows && tableRows.length > 0 ? tableRows : specDataRows(spec)

  useEffect(() => {
    let cancelled = false
    let result: VegaEmbedResult | null = null
    const host = hostRef.current
    if (!host) return

    if (!spec || typeof spec !== "object") {
      setPhase("failed")
      return
    }
    if (specFetchesRemoteData(spec)) {
      setPhase("failed")
      return
    }

    setPhase("loading")
    void (async () => {
      try {
        const { embed, expressionInterpreter } = await loadVegaRuntime()
        if (cancelled) return
        result = await embed(host, spec as never, {
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
  }, [spec])

  return (
    <figure className={`prd-chart prd-chart-vega${className ? ` ${className}` : ""}`}>
      {title ? <figcaption className="prd-chart-title">{title}</figcaption> : null}
      {subtitle ? <div className="prd-chart-sub">{subtitle}</div> : null}
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
      {caption ? <figcaption className="prd-chart-caption">{caption}</figcaption> : null}
      {/* The static fallback table already IS the data; don't print it twice. */}
      {phase === "failed" && !pending ? null : <ChartDataTable rows={rows} />}
    </figure>
  )
}
