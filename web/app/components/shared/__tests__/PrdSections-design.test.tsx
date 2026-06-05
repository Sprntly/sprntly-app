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
// shared/__tests__ → shared → components → screens/app/PrdScreen.tsx
const PRD_SCREEN_SRC = readFileSync(
  join(HERE, "..", "..", "screens", "app", "PrdScreen.tsx"),
  "utf8",
)

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

  it("defined prdId routes to DesignAgentLauncher with the threaded prdTitle, and the generate trigger now lives in the Approve modal (not a bare inline button)", () => {
    // Wiring invariant: DesignSection's prdId-defined branch hands off to
    // <DesignAgentLauncher prdId figmaFileKey prdTitle/> (source content
    // invariant — the component is internal and the integration render mounts the
    // real drawer, which needs an app-router-backed NavigationProvider
    // unavailable in node).
    expect(PRD_SECTIONS_SRC).toMatch(
      /<DesignAgentLauncher\s+prdId=\{prdId\}\s+figmaFileKey=\{figmaFileKey\}\s+prdTitle=\{prdTitle\}\s*\/>/,
    )
    // Affordance invariant: the launcher renders the prd-design launcher root,
    // but the bare "Generate Prototype" button has moved into the Approve modal
    // flow — the launcher surface no longer carries an inline generate button
    // (stub drawer so useNavigation is not reached).
    const stubDrawer = (_props: LauncherDrawerProps) => null
    const html = renderToStaticMarkup(
      React.createElement(DesignAgentLauncher, {
        prdId: 42,
        figmaFileKey: null,
        renderDrawer: stubDrawer,
      }),
    )
    expect(html).toContain("prd-design-launcher")
    expect(html).not.toContain("Generate Prototype")
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
    // The prd-design dispatch case still resolves to DesignSection, which now
    // renders the section wrapper + relocated launcher/empty-state (the old
    // "Design" header was removed in the redesign).
    expect(renderEmptyState()).toContain("prd-design")
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

describe("PrdSections — prd-design generate-trigger relocation + hot-file exception", () => {
  const stubDrawer = (_props: LauncherDrawerProps) => null

  it("test_prd_design_renders_relocated_trigger — the prd-design launcher routes through the Approve-modal flow, not a bare inline generate button", () => {
    // The launcher surface mounts (contentEditable={false} so it never disturbs
    // the PRD body). The generate trigger itself now lives in the Approve modal
    // (GenerateModal is opened from ApproveModal), so the launcher surface no
    // longer renders a bare "Generate Prototype" button. Stub drawer keeps the
    // render off useNavigation (no NavigationProvider in node env).
    const html = renderToStaticMarkup(
      React.createElement(DesignAgentLauncher, {
        prdId: 7,
        figmaFileKey: null,
        prdTitle: "Checkout flow",
        renderDrawer: stubDrawer,
      }),
    )
    expect(html).toContain("prd-design-launcher")
    expect(html).toContain('contentEditable="false"')
    expect(html).not.toContain("Generate Prototype")
    // Source invariant: the prd-design case dispatches to DesignSection, which
    // owns the launcher hand-off (the relocation extends the single existing
    // case rather than adding a parallel renderer).
    expect(PRD_SECTIONS_SRC).toMatch(/return <DesignSection prdId=\{prdId\}/)
  })

  it("test_prd_title_threads_to_design_section — prdTitle is forwarded PrdScreen → PrdSections → DesignSection → launcher", () => {
    // PrdScreen passes prd.title; PrdSections forwards prdTitle through every
    // RenderBlock; the prd-design case threads it onto DesignSection, which hands
    // it to the launcher. Verified as a source-wiring invariant — the title only
    // surfaces on the preview card / canvas breadcrumb, which need client state
    // (useEffect) that does not run under SSR.
    expect(PRD_SCREEN_SRC).toContain("prdTitle={prd.title}")
    expect(PRD_SECTIONS_SRC).toMatch(
      /return <DesignSection prdId=\{prdId\} figmaFileKey=\{figmaFileKey\} prdTitle=\{prdTitle\} \/>/,
    )
    // PrdSections accepts prdTitle and forwards it down to each RenderBlock.
    expect(PRD_SECTIONS_SRC).toContain("prdTitle?: string | null")
    expect(PRD_SECTIONS_SRC).toMatch(/<RenderBlock[\s\S]*?prdTitle=\{prdTitle\}[\s\S]*?\/>/)
  })

  it("test_prd_design_no_trigger_without_prd_id — with no prdId the section renders the empty state and no generate trigger", () => {
    // The prdId === undefined branch (demo / empty PRD) mounts NO launcher, so it
    // SSR-renders without a NavigationProvider and surfaces no button.
    const html = renderEmptyState()
    expect(html).toContain("prd-design")
    expect(html).toContain("No prototype yet.")
    expect(html).not.toMatch(/<button[^>]*>/)
  })

  it("test_content_editable_region_untouched — the PRD-body contentEditable element keeps its exact attributes", () => {
    // Acceptance criterion: the editable PRD body is byte-identical to its prior
    // form. PrdScreen needs an app-router-backed NavigationProvider to render in
    // this node env,
    // so the invariant is asserted on the on-disk source (NEVER `git show <rev>`
    // — CI shallow clones lack historical objects).
    expect(PRD_SCREEN_SRC).toMatch(
      /<div\s+className="prd-body"\s+contentEditable\s+spellCheck=\{false\}\s+suppressContentEditableWarning\s*>/,
    )
    // The PrdSections mount still lives INSIDE that editable region, unchanged.
    expect(PRD_SCREEN_SRC).toMatch(
      /<PrdSections sections=\{prd\.sections\} prdId=\{prd\.prd_id\} figmaFileKey=\{prd\.figma_file_key \?\? null\} prdTitle=\{prd\.title\} \/>/,
    )
  })

  it("test_no_ux_explore_marker_in_prd_design — both edited files carry the durable hot-file exception note, no throwaway scratch markers", () => {
    // Acceptance criterion: zero throwaway "UX-EXPLORE (throwaway — REVERT)"
    // markers remain on the prd-design path, and both edited files carry the
    // durable, plain-English sanctioned-exception note.
    expect(PRD_SECTIONS_SRC).not.toContain("UX-EXPLORE")
    expect(PRD_SECTIONS_SRC).toContain("Hot-file exception")
    expect(PRD_SECTIONS_SRC).toContain(
      "contentEditable region is deliberately untouched",
    )
    expect(PRD_SCREEN_SRC).toContain("Hot-file exception")
    expect(PRD_SCREEN_SRC).toContain(
      "editable PRD-body region is deliberately left untouched",
    )
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
