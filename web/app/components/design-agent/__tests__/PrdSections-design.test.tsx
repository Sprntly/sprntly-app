import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"
import { PrdSections } from "../../shared/PrdSections"
import type { PrdSection } from "../../../types/content"

// PrdSections.tsx — like every Sprntly component — has no `import React`; it
// relies on the React 17+ automatic JSX runtime that Next.js's SWC supplies
// in production. This repo's vitest/esbuild transform defaults to the classic
// runtime (`React.createElement`), so the imported component needs a global
// `React`. Expose it here rather than modify Sprntly's shared vitest config
// (outside this engagement's isolation map; DBD keeps its footprint minimal).
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

/**
 * Pure-function render assertion (no @testing-library / jsdom — environment
 * is node per vitest.config.ts). Renders the prd-design block to static
 * markup and asserts the Design header + empty-state entry point + the
 * P1-09 mount slot are present.
 */
describe("PrdSections — prd-design block", () => {
  it("renders the Design header and the empty-state entry point", () => {
    const sections: PrdSection[] = [{ type: "prd-design" }]
    const html = renderToStaticMarkup(
      React.createElement(PrdSections, { sections }),
    )
    expect(html).toContain("Design")
    expect(html).toContain(
      "No prototype yet — use the Design Agent to generate one",
    )
    // The mount target P1-09's DesignAgentDrawer trigger plugs into.
    expect(html).toContain("data-design-agent-slot")
  })
})
