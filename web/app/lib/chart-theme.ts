/**
 * Typed access to the Sprntly chart theme.
 *
 * `chart-theme.json` is the WEB-SIDE MIRROR of `backend/app/charts/theme.py`,
 * which is the source of truth for the values. Phase 0 adds a test that
 * regenerates this file from `theme.py` and diffs it, so the server SVG
 * renderer (vl-convert) and the client renderer (vega-embed) cannot drift.
 *
 * WHY A MIRROR AND NOT A DIRECT IMPORT OF `backend/app/charts/theme.json`:
 *  - Phase 0 and Phase 2 are separate PRs. A web module importing a
 *    backend-owned file makes the WEB BUILD fail until Phase 0 lands, and puts
 *    the same path in two PRs' diffs.
 *  - A client-component `import` of a path above the project root goes through
 *    webpack, which refuses it without `experimental.externalDir` in
 *    `next.config.ts` — a file that also carries the Sentry wrapper and the
 *    static-export switch and is not worth touching for a theme.
 *  (The `pipeline-contract.test.ts` precedent for reading `backend/` from
 *   `web/` is a test-time `readFileSync`, a different mechanism entirely.)
 *
 * Drift between `categorical` here and `CHART_COLORS` in
 * `app/components/shared/InlineChart.tsx` is a test failure —
 * see `app/lib/__tests__/chart-theme.test.ts`.
 */
import themeJson from "./chart-theme.json"

export interface ChartTheme {
  version: number
  /** Vega-Lite schema URL the specs are authored against. */
  vegaLiteSchema: string
  /** SSR font stack — what vl-convert measures AND draws with. */
  font: string
  /** Browser font stack — the app's real UI font. */
  fontClient: string
  /** The categorical palette, in order. The index IS the series colour. */
  categorical: string[]
  /** Literal Vega-Lite `config` objects, one per colour scheme. */
  vega: {
    light: Record<string, unknown>
    dark: Record<string, unknown>
  }
}

export const CHART_THEME: ChartTheme = themeJson as unknown as ChartTheme

/** The categorical palette, in order. Mirror of backend `CHART_COLORS`. */
export const THEME_CHART_COLORS: readonly string[] = CHART_THEME.categorical

/**
 * The Vega config for the CLIENT renderer.
 *
 * The stored config carries the SSR font stack, because vl-convert has to
 * measure text with the same font it draws with. In a browser the font that
 * measures is the font that draws, so the client swaps in `fontClient` (the
 * app's real UI font) over `font` — top level and on every config block that
 * names a font of its own. Everything else passes through untouched.
 *
 * The app ships no dark mode yet; `scheme` is here so the call site does not
 * have to change when it does.
 */
export function clientConfig(scheme: "light" | "dark" = "light"): Record<string, unknown> {
  const base = CHART_THEME.vega[scheme] ?? CHART_THEME.vega.light
  const font = CHART_THEME.fontClient
  const out: Record<string, unknown> = { ...base, font }
  // Vega resolves `config.font` for most text, but a block that sets its own
  // `font`/`labelFont`/`titleFont` wins over it — so override those too rather
  // than leaving half the chart in the SSR stack.
  for (const [key, value] of Object.entries(out)) {
    if (!value || typeof value !== "object" || Array.isArray(value)) continue
    const block = value as Record<string, unknown>
    const patched: Record<string, unknown> = { ...block }
    let touched = false
    for (const fontKey of ["font", "labelFont", "titleFont"]) {
      if (fontKey in patched) {
        patched[fontKey] = font
        touched = true
      }
    }
    if (touched) out[key] = patched
  }
  return out
}
