// P6-11 (UX-1) — structural tests for the scoped design-agent.css foundation.
//
// Node-env vitest (no DOM, no router, no @testing-library), so — following the
// CompletionBar / PostGenerationResult / PrdPatchBanner convention — we
// SSR-render the pure views via renderToStaticMarkup to assert the wrapper
// class, and read design-agent.css / layout.tsx / PublicTokenViewer.tsx from
// disk for the structural / invariant assertions. The globals.css "untouched"
// checks assert WORKING-TREE content invariants read via fs — NEVER `git show
// <historical-rev>` / `git diff <sha>`, which fails under CI's shallow clone
// (fetch-depth=1: historical objects like 2d6a416 are not in CI's object
// store). "globals untouched" is the intent; the method is free (per AC4/AC5).
import { readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"

// Sprntly components carry no `import React`; vitest's esbuild transform uses
// the classic runtime, so expose React globally (repo test convention).
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import {
  DesignAgentLauncher,
  type LauncherDrawerProps,
} from "../DesignAgentLauncher"
import {
  PostGenerationResultView,
  type PostGenerationResultViewProps,
} from "../PostGenerationResult"

const HERE = dirname(fileURLToPath(import.meta.url))
// __tests__ → design-agent → components → app
const APP_DIR = join(HERE, "..", "..", "..")
const CSS_PATH = join(HERE, "..", "design-agent.css")
const LAYOUT_PATH = join(APP_DIR, "layout.tsx")
const PUBLIC_VIEWER_PATH = join(APP_DIR, "p", "PublicTokenViewer.tsx")

const GLOBALS_PATH = join(APP_DIR, "globals.css")

const CSS = readFileSync(CSS_PATH, "utf8")
const LAYOUT = readFileSync(LAYOUT_PATH, "utf8")
const PUBLIC_VIEWER = readFileSync(PUBLIC_VIEWER_PATH, "utf8")
const GLOBALS = readFileSync(GLOBALS_PATH, "utf8")

const DOT_HEXES = ["#E5806B", "#E8C24A", "#6FBF8F"]
// DA-emitted classnames that MUST NOT appear as rules in globals.css (the
// scoped sheet owns them). If any leaked into globals, the ticket touched a
// hot file it must never open.
const DA_RULE_MARKERS = [
  "design-agent-surface",
  ".da-prototype",
  ".comments-panel",
  ".comment-composer",
  ".completion-bar",
  ".iterate-composer",
  ".generation-error-banner",
  ".da-public-",
]

/** Strip CSS comments (block, possibly multi-line) so content assertions never
 *  trip over prose inside comments. */
function stripCssComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, "")
}

/** The non-comment lines that open or continue a selector (end with `{` or
 *  `,`), excluding at-rules. The scoping invariant: each must begin with
 *  `.design-agent-surface`. */
function selectorLines(css: string): string[] {
  return stripCssComments(css)
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l.length > 0 && !l.startsWith("@"))
    .filter((l) => l.endsWith("{") || l.endsWith(","))
}

function makeDrawerSpy() {
  const renderDrawer = (_props: LauncherDrawerProps) => null
  return { renderDrawer }
}

function renderResultView(
  over: Partial<PostGenerationResultViewProps> = {},
): string {
  return renderToStaticMarkup(
    React.createElement(PostGenerationResultView, {
      prototypeId: 42,
      isComplete: false,
      shareMode: "private",
      shareToken: null,
      bundleUrl: null,
      ...over,
    }),
  )
}

// ── AC1: stylesheet imported once, immediately after globals.css ──────────
describe("layout import (AC1)", () => {
  it("test_layout_imports_design_agent_css — imports the scoped sheet right after globals.css", () => {
    const lines = LAYOUT.split("\n").map((l) => l.trim())
    const globalsIdx = lines.indexOf('import "./globals.css"')
    const cssIdx = lines.indexOf(
      'import "./components/design-agent/design-agent.css"',
    )
    expect(globalsIdx).toBeGreaterThanOrEqual(0)
    expect(cssIdx).toBe(globalsIdx + 1)
    // exactly one import of the scoped sheet
    const matches = LAYOUT.match(
      /import "\.\/components\/design-agent\/design-agent\.css"/g,
    )
    expect(matches).toHaveLength(1)
  })
})

// ── AC5: no :root redefinition in the new file ────────────────────────────
describe("no :root redefinition (AC5)", () => {
  it("test_css_file_has_no_root_redefinition — the file contains no `:root {` block", () => {
    expect(stripCssComments(CSS)).not.toMatch(/:root\s*\{/)
  })
})

// ── Scoping invariant: every selector line begins with .design-agent-surface ─
describe("scoping invariant", () => {
  it("test_css_scopes_every_rule_under_surface — every selector line starts with .design-agent-surface", () => {
    const offenders = selectorLines(CSS).filter(
      (l) => !l.startsWith(".design-agent-surface"),
    )
    expect(offenders).toEqual([])
  })
})

// ── AC2: wrapper class present on all three roots ─────────────────────────
describe("wrapper class on the three DA roots (AC2)", () => {
  it("test_launcher_root_has_surface_class — prd-design-launcher root carries design-agent-surface", () => {
    const { renderDrawer } = makeDrawerSpy()
    const html = renderToStaticMarkup(
      React.createElement(DesignAgentLauncher, {
        prdId: 1,
        figmaFileKey: null,
        renderDrawer,
      }),
    )
    expect(html).toMatch(
      /<div[^>]*class="design-agent-surface prd-design-launcher"/,
    )
  })

  it("test_result_root_has_surface_class — design-agent-result root carries design-agent-surface", () => {
    const html = renderResultView()
    expect(html).toMatch(
      /<div[^>]*class="design-agent-surface design-agent-result"/,
    )
  })

  it("test_public_viewer_root_has_surface_class — PublicTokenViewer roots carry design-agent-surface", () => {
    // Hook-driven component (useParams/useEffect) → not SSR-renderable in the
    // node-env run, so assert the class placements structurally from source.
    expect(PUBLIC_VIEWER).toContain(
      'className="design-agent-surface da-public-loading"',
    )
    expect(PUBLIC_VIEWER).toContain(
      'className="design-agent-surface da-public-error"',
    )
    // main <PrototypeViewer> return wrapped in a design-agent-surface div
    expect(PUBLIC_VIEWER).toMatch(
      /<div className="design-agent-surface">\s*<PrototypeViewer/,
    )
  })
})

// ── AC3: representative scoped rules exist for the emitted surface ─────────
describe("scoped rules cover the emitted surface (AC3)", () => {
  const SAMPLE = [
    "comments-panel",
    "comment-thread",
    "comment-composer",
    "completion-bar",
    "iterate-composer",
    "generation-error-banner",
    "da-public-loading",
    "prd-design-empty",
    "da-prototype-viewer",
    "share-link",
  ]
  for (const cls of SAMPLE) {
    it(`defines a scoped rule for .${cls}`, () => {
      const re = new RegExp(
        `\\.design-agent-surface[^{]*\\.${cls.replace(/-/g, "\\-")}[\\s.,{:]`,
      )
      expect(CSS).toMatch(re)
    })
  }
})

// ── AC4: .share-menu override present, scoped, non-leaking ─────────────────
describe("share-menu collision override (AC4)", () => {
  it("test_share_menu_scoped_override_present — scoped .share-menu sets position: static", () => {
    // find the `.design-agent-surface .share-menu { ... }` block
    const block = CSS.match(
      /\.design-agent-surface\s+\.share-menu\s*\{([^}]*)\}/,
    )
    expect(block).not.toBeNull()
    const body = block![1]
    expect(body).toMatch(/position:\s*static/)
    expect(body).toMatch(/opacity:\s*1/)
    expect(body).toMatch(/pointer-events:\s*auto/)
    expect(body).toMatch(/transform:\s*none/)
  })

  it("every .share-menu selector in design-agent.css is scoped under .design-agent-surface", () => {
    // no bare `.share-menu` selector may appear (would leak to PrdScreen)
    const bare = stripCssComments(CSS)
      .split("\n")
      .map((l) => l.trim())
      .filter((l) => /(^|[\s,]):?\.share-menu\b/.test(l) || l.startsWith(".share-menu"))
      .filter((l) => !l.startsWith(".design-agent-surface"))
    expect(bare).toEqual([])
  })

  it("test_globals_share_menu_unchanged — working-tree globals.css .share-menu popover rule is present + intact", () => {
    // WORKING-TREE content invariant (no `git show <rev>` — CI shallow-clone
    // lacks historical objects). The DA scoped override must NOT have leaked
    // into globals: globals' `.share-menu` is still the absolutely-positioned,
    // hidden-by-default popover (PrdScreen's footer dropdown, ~lines 1412-1428).
    const block = GLOBALS.match(/\n\s*\.share-menu\s*\{([^}]*)\}/)
    expect(block).not.toBeNull()
    const body = block![1]
    expect(body).toMatch(/position:\s*absolute/)
    expect(body).toMatch(/opacity:\s*0\b/)
    expect(body).toMatch(/pointer-events:\s*none/)
    // the scoped DA override (`.design-agent-surface .share-menu`) lives in
    // design-agent.css, NOT globals — globals carries no `design-agent-surface`.
    expect(GLOBALS).not.toContain("design-agent-surface")
  })
})

// ── AC5: globals.css untouched by this ticket ─────────────────────────────
describe("globals.css untouched (AC5)", () => {
  it("test_globals_untouched_by_ticket — working-tree globals.css carries no DA rule and no new :root", () => {
    // WORKING-TREE content invariant (no historical-rev diff — CI shallow clone).
    // 1) zero DA-surface rules leaked into the hot file
    for (const marker of DA_RULE_MARKERS) {
      expect(GLOBALS).not.toContain(marker)
    }
    // 2) no NEW / duplicate :root block — globals legitimately has exactly one
    const rootBlocks = GLOBALS.match(/(^|\n)\s*:root\s*\{/g) ?? []
    expect(rootBlocks).toHaveLength(1)
    // 3) globals' own .share-menu popover rule is still present (not removed)
    expect(GLOBALS).toMatch(/\.share-menu\s*\{/)
  })
})

// ── AC8: layout default export unchanged, additive import ──────────────────
describe("layout non-breakage (AC8)", () => {
  it("test_layout_default_export_unchanged — RootLayout default export + globals import intact", () => {
    expect(LAYOUT).toMatch(/export default function RootLayout\(/)
    expect(LAYOUT).toContain('import "./globals.css"')
  })
})

// ── AC6: no new colour palette beyond the three .proto-dot dot hexes ───────
describe("no new palette (AC6)", () => {
  it("test_css_only_dot_hex_literals — every colour literal is a permitted .proto-dot hex; no rgb()/hsl()", () => {
    const stripped = stripCssComments(CSS)
    // no functional colour literals at all
    expect(stripped).not.toMatch(/rgba?\(/)
    expect(stripped).not.toMatch(/hsla?\(/)
    // every hex literal is on a .proto-dot line and is one of the three
    const offenders: string[] = []
    for (const rawLine of stripped.split("\n")) {
      const hexes = rawLine.match(/#[0-9a-fA-F]{3,8}\b/g)
      if (!hexes) continue
      for (const hex of hexes) {
        const onDotLine = rawLine.includes(".proto-dot")
        const permitted = DOT_HEXES.includes(hex.toUpperCase())
        if (!onDotLine || !permitted) offenders.push(rawLine.trim())
      }
    }
    expect(offenders).toEqual([])
    // sanity: the three dot hexes are actually present
    for (const hex of DOT_HEXES) {
      expect(stripped).toContain(hex)
    }
  })
})
