/**
 * Theme drift guard.
 *
 * `app/lib/chart-theme.json` is the WEB-SIDE MIRROR of the backend chart theme
 * (`backend/app/charts/theme.py`), and `app/lib/chart-theme.ts` imports THAT —
 * not the backend file. See the comment in `chart-theme.ts` for why the mirror
 * lives under `web/`; Phase 0 adds the test that regenerates and diffs it.
 *
 * So the palette lives in two places this test can see: the mirror, and
 * `InlineChart.tsx`'s `CHART_COLORS`, which the DOM renderers still use and
 * which the theme was lifted from. Drift is silent — a chart just quietly comes
 * out the wrong colour in a stored PRD. This test binds them.
 *
 * If this fails, do NOT "fix" it by editing one side. Decide which palette is
 * correct, change all three (theme.py, chart-theme.json, InlineChart.tsx), and
 * say so in the PR — it is a visual change to every existing artifact.
 */
import { describe, expect, it } from "vitest"
import { CHART_COLORS } from "../../components/shared/InlineChart"
import { CHART_THEME, THEME_CHART_COLORS, clientConfig } from "../chart-theme"

/** The palette as agreed with Phase 0, spelled out so a change to BOTH
 *  sources still trips this test. Order is load-bearing: index = series. */
const AGREED_PALETTE = [
  "#5B7FFF",
  "#6FCF97",
  "#F2994A",
  "#BB6BD9",
  "#56CCF2",
  "#EB5757",
  "#F2C94C",
  "#27AE60",
]

describe("chart theme", () => {
  it("matches InlineChart's CHART_COLORS exactly, in order", () => {
    expect(THEME_CHART_COLORS).toEqual(CHART_COLORS)
  })

  it("matches the palette agreed with backend/app/charts/theme.py", () => {
    expect(THEME_CHART_COLORS).toEqual(AGREED_PALETTE)
    expect(CHART_COLORS).toEqual(AGREED_PALETTE)
  })

  it("uses the same palette for the Vega-Lite categorical range, light and dark", () => {
    for (const scheme of ["light", "dark"] as const) {
      const range = (CHART_THEME.vega[scheme].range as { category?: string[] }).category
      expect(range).toEqual(AGREED_PALETTE)
    }
  })

  it("declares a Vega-Lite v6 schema", () => {
    // altair 6.2.2 emits v6.4 and has no v5 mode, so both ends are on v6.
    expect(CHART_THEME.vegaLiteSchema).toContain("vega-lite/v6")
  })

  it("carries concrete font stacks, never a CSS custom property", () => {
    // vl-convert has no DOM and cannot resolve var(--…); a theme value that
    // works on the client and blanks server-side is exactly the drift the
    // single-file theme exists to prevent.
    for (const stack of [CHART_THEME.font, CHART_THEME.fontClient]) {
      expect(stack).not.toContain("var(")
      expect(stack.length).toBeGreaterThan(0)
    }
    for (const scheme of ["light", "dark"] as const) {
      expect(JSON.stringify(CHART_THEME.vega[scheme])).not.toContain("var(")
    }
  })

  it("never sets a background that would punch a hole in a card", () => {
    for (const scheme of ["light", "dark"] as const) {
      expect(CHART_THEME.vega[scheme].background).toBe("transparent")
    }
  })

  describe("clientConfig", () => {
    it("swaps the browser font stack in over the SSR one", () => {
      // The stored config carries the SSR stack (Liberation, metric-compatible
      // with Arial) because vl-convert must measure with what it draws with.
      // In a browser we want the app's real UI font.
      expect(CHART_THEME.vega.light.font).toBe(CHART_THEME.font)
      expect(clientConfig("light").font).toBe(CHART_THEME.fontClient)
    })

    it("leaves no SSR font anywhere in the client config", () => {
      // A block setting its own font/labelFont/titleFont beats config.font, so
      // a partial swap leaves half the chart in the wrong stack.
      expect(JSON.stringify(clientConfig("light"))).not.toContain(CHART_THEME.font)
      expect(JSON.stringify(clientConfig("dark"))).not.toContain(CHART_THEME.font)
    })

    it("passes every non-font value through untouched", () => {
      const config = clientConfig("light")
      expect(config.background).toBe("transparent")
      expect((config.range as { category: string[] }).category).toEqual(AGREED_PALETTE)
      expect((config.bar as { cornerRadiusEnd: number }).cornerRadiusEnd).toBe(3)
    })

    it("defaults to light and tolerates an unknown scheme", () => {
      expect(clientConfig()).toEqual(clientConfig("light"))
    })
  })
})
