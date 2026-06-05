import { readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"
import { PrdSections } from "../PrdSections"
import {
  DesignAgentLauncher,
  type LauncherDrawerProps,
} from "../../design-agent/DesignAgentLauncher"
import type { PrdSection } from "../../../types/content"

// PrdSections.tsx — like every Sprntly component — has no `import React`; it
// relies on the React 17+ automatic JSX runtime that Next.js's SWC supplies
// in production. This repo's vitest/esbuild transform defaults to the classic
// runtime (`React.createElement`), so the imported component needs a global
// `React`. Expose it here rather than modify Sprntly's shared vitest config
// (outside this engagement's isolation map; DBD keeps its footprint minimal).
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const HERE = dirname(fileURLToPath(import.meta.url))
// design-agent.css + PrdSections.tsx read from disk for WORKING-TREE content
// invariants (node env, no DOM). NEVER `git show <rev>` / `git diff <sha>` —
// CI shallow clone (fetch-depth=1) lacks historical objects; that CI-failed
// P6-11. The "intent" is the on-disk content; the method is fs reads.
// shared/__tests__ → shared → components → design-agent/design-agent.css
const CSS_PATH = join(HERE, "..", "..", "design-agent", "design-agent.css")
const CSS = readFileSync(CSS_PATH, "utf8")
const PRD_SECTIONS_SRC = readFileSync(join(HERE, "..", "PrdSections.tsx"), "utf8")

const OLD_EMPTY_COPY = "No prototype yet — use the Design Agent to generate one"

// The prdId === undefined branch mounts NO launcher/drawer, so it SSR-renders
// without a NavigationProvider (which itself calls useRouter at render time and
// is not SSR-renderable in this node env).
function renderEmptyState(): string {
  const sections: PrdSection[] = [{ type: "prd-design" }]
  return renderToStaticMarkup(React.createElement(PrdSections, { sections }))
}

describe("PrdSections — prd-design block (UX-9: dead slot + empty-state)", () => {
  // ── Regression (fails on unfixed code) ──────────────────────────────────
  it("test_design_section_has_no_dead_slot — no prd-design-slot / data-design-agent-slot (AC1)", () => {
    // The dead slot sat OUTSIDE the prdId ternary, so it rendered in both
    // branches; the empty-state render alone proves its removal.
    const html = renderEmptyState()
    expect(html).not.toContain("prd-design-slot")
    expect(html).not.toContain("data-design-agent-slot")
    // The stale doc-comment line that described the slot is also gone.
    expect(PRD_SECTIONS_SRC).not.toContain("forward-compat mount")
  })

  it("test_empty_state_copy_updated — new explanatory copy + next-step, old actionless string gone (AC2)", () => {
    const html = renderEmptyState()
    expect(html).toContain("No prototype yet.")
    expect(html).toContain("Open this PRD to generate an interactive prototype")
    expect(html).not.toContain(OLD_EMPTY_COPY)
  })

  // ── Branch behaviour ────────────────────────────────────────────────────
  it("test_empty_state_no_inert_cta — empty-state renders no inert generate button (AC3)", () => {
    const html = renderEmptyState()
    // No <button> in the prdId === undefined branch — an inert CTA would repeat
    // the dead-slot mistake. The real Generate affordance lives in the launcher.
    expect(html).not.toMatch(/<button[^>]*>/)
  })

  it("test_launcher_branch_unchanged — defined prdId routes to DesignAgentLauncher, which renders the Generate affordance (AC4)", () => {
    // Wiring invariant: DesignSection's prdId-defined branch still hands off to
    // <DesignAgentLauncher prdId figmaFileKey/> (source content invariant — the
    // component is internal and the integration render mounts the real drawer,
    // which needs an app-router-backed NavigationProvider unavailable in node).
    expect(PRD_SECTIONS_SRC).toMatch(
      /<DesignAgentLauncher\s+prdId=\{prdId\}\s+figmaFileKey=\{figmaFileKey\}\s*\/>/,
    )
    // Affordance invariant: the launcher itself still renders the prd-design
    // launcher root + "Generate Prototype" button (stub drawer, per P6-11's
    // own test convention, so useNavigation is not reached).
    const stubDrawer = (_props: LauncherDrawerProps) => null
    const html = renderToStaticMarkup(
      React.createElement(DesignAgentLauncher, {
        prdId: 42,
        figmaFileKey: null,
        renderDrawer: stubDrawer,
      }),
    )
    expect(html).toContain("prd-design-launcher")
    expect(html).toContain("Generate Prototype")
  })

  // ── CSS / scope ─────────────────────────────────────────────────────────
  it("test_empty_state_rule_exists_and_scoped — scoped rule exists, wrapper scope applied, token-only (AC5/AC7)", () => {
    // Rule exists in design-agent.css (shipped by P6-11; this ticket does not
    // duplicate it) and is scoped with the .design-agent-surface prefix.
    expect(CSS).toContain(".design-agent-surface .prd-design-empty")

    // The empty-state renders under a design-agent-surface scope so the rule
    // applies (AC5 second clause — the gap this ticket actually fills in JSX).
    const html = renderEmptyState()
    expect(html).toMatch(
      /<div[^>]*class="design-agent-surface"[^>]*>\s*<p[^>]*class="prd-design-empty"/,
    )

    // The rule's declaration body uses tokens only — no literal colour.
    const block = extractRuleBlock(CSS, ".design-agent-surface .prd-design-empty")
    expect(block).not.toBeNull()
    expect(block).not.toMatch(/#[0-9a-fA-F]{3,8}\b/)
    expect(block).not.toMatch(/\b(rgb|rgba|hsl|hsla)\(/)
  })

  // ── Non-breakage ────────────────────────────────────────────────────────
  it("test_only_design_section_modified — sibling renderer intact + prd-design still dispatches to DesignSection (AC6)", () => {
    // A sibling prd-* renderer still produces its expected markup (DodChecklist).
    const dod = renderToStaticMarkup(
      React.createElement(PrdSections, {
        sections: [{ type: "prd-dod", items: ["ship it"] }] as PrdSection[],
      }),
    )
    expect(dod).toContain("prdv2-dod")
    expect(dod).toContain("ship it")
    // The prd-design dispatch case still resolves to DesignSection (Design header).
    expect(renderEmptyState()).toContain("Design")
    // Source invariant: the dispatch case still returns <DesignSection .../>.
    expect(PRD_SECTIONS_SRC).toMatch(/return <DesignSection prdId=\{prdId\}/)
  })

  it("test_p6_11_css_blocks_unchanged — exactly one prd-design-empty rule, dashed card, no duplicate appended (AC8)", () => {
    // This ticket does NOT append to design-agent.css (P6-11 already shipped the
    // .design-agent-surface .prd-design-empty rule). Guard against a duplicate
    // selector creeping in and against the dashed-card treatment regressing.
    const occurrences =
      CSS.match(/\.design-agent-surface \.prd-design-empty\b/g) ?? []
    expect(occurrences.length).toBe(1)
    const block = extractRuleBlock(CSS, ".design-agent-surface .prd-design-empty")
    expect(block).toContain("dashed")
    expect(block).toContain("var(--line-strong)")
  })
})

/** Return the `{ ... }` declaration body for the first rule whose selector
 *  exactly matches `selector`, or null. Plain brace scan — sufficient for the
 *  flat, un-nested design-agent.css. */
function extractRuleBlock(css: string, selector: string): string | null {
  const at = css.indexOf(selector)
  if (at === -1) return null
  const open = css.indexOf("{", at)
  const close = css.indexOf("}", open)
  if (open === -1 || close === -1) return null
  return css.slice(open + 1, close)
}
