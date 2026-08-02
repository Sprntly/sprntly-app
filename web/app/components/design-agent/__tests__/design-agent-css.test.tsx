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

import { DesignAgentLauncher } from "../DesignAgentLauncher"
import {
  PostGenerationResultView,
  type PostGenerationResultViewProps,
} from "../PostGenerationResult"

const HERE = dirname(fileURLToPath(import.meta.url))
// __tests__ → design-agent → components → app
const APP_DIR = join(HERE, "..", "..", "..")
const CSS_PATH = join(HERE, "..", "design-agent.css")
const LAYOUT_PATH = join(APP_DIR, "layout.tsx")
const GENERATION_LOADING_SCREEN_PATH = join(
  HERE,
  "..",
  "GenerationLoadingScreen.tsx",
)
const PUBLIC_VIEWER_PATH = join(APP_DIR, "p", "PublicTokenViewer.tsx")
// The anon-viewer chrome (mark tool, comments, name capture, single-device
// gate) was extracted out of PublicTokenViewer.tsx into this sibling file; the
// ready-state markup this suite checks for (da-ready / PrototypeViewer mount)
// now lives here instead.
const PUBLIC_CHROME_PATH = join(APP_DIR, "p", "PublicPrototypeChrome.tsx")

const GLOBALS_PATH = join(APP_DIR, "globals.css")

const CSS = readFileSync(CSS_PATH, "utf8")
const LAYOUT = readFileSync(LAYOUT_PATH, "utf8")
const GENERATION_LOADING_SCREEN = readFileSync(
  GENERATION_LOADING_SCREEN_PATH,
  "utf8",
)
const PUBLIC_VIEWER = readFileSync(PUBLIC_VIEWER_PATH, "utf8")
const PUBLIC_CHROME = readFileSync(PUBLIC_CHROME_PATH, "utf8")
const GLOBALS = readFileSync(GLOBALS_PATH, "utf8")

const DOT_HEXES = ["#E5806B", "#E8C24A", "#6FBF8F"]
// Intentionally shared across the DA surface + the design-source settings pane (DesignSourceSettings, cd1cc20) + BriefChat — these render outside .design-agent-surface, so the strict surface-scope check exempts them BY NAME (any other unscoped selector still fails).
const UNSCOPED_ALLOWLIST = new Set([
  ".src-not-connected {",
  ".src-not-connected.muted {",
  ".src-connect-btn {",
  ".src-connect-btn:hover {",
  ".src-connect-btn.ghost {",
  ".src-connect-btn.ghost:hover {",
  ".radio-group {",
  ".radio-pill {",
  ".radio-pill:hover {",
  ".radio-pill.selected {",
  ".fc-preview-img {",
])
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
      (l) => !l.startsWith(".design-agent-surface") && !UNSCOPED_ALLOWLIST.has(l),
    )
    expect(offenders).toEqual([])
  })

  it("allowlist is exact-match, not a prefix/blanket — a new accidental unscoped selector still fails", () => {
    // The exemption is a Set of exact selector strings, NOT a `.src-`/`.radio-`
    // prefix. A NEW unscoped leak (e.g. `.src-foo {` or `.totally-unscoped-xyz`)
    // is NOT exempted and would surface as an offender.
    expect(UNSCOPED_ALLOWLIST.has(".totally-unscoped-xyz {")).toBe(false)
    expect(UNSCOPED_ALLOWLIST.has(".src-foo {")).toBe(false)
    expect(UNSCOPED_ALLOWLIST.has(".radio-foo {")).toBe(false)
  })
})

// ── AC2: wrapper class present on all three roots ─────────────────────────
describe("wrapper class on the three DA roots (AC2)", () => {
  it("test_launcher_root_has_surface_class — prd-design-launcher root carries design-agent-surface", () => {
    const html = renderToStaticMarkup(
      React.createElement(DesignAgentLauncher, {
        prdId: 1,
        figmaFileKey: null,
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
    // main ready-state return: PublicTokenViewer mounts PublicPrototypeChrome,
    // which itself wraps design-agent-surface around da-ready which wraps
    // da-stage which wraps PrototypeViewer (collapsible sidebar layout). The
    // anon-viewer chrome (and this markup) was extracted out of
    // PublicTokenViewer.tsx into that sibling file.
    expect(PUBLIC_CHROME).toContain('className="design-agent-surface"')
    expect(PUBLIC_CHROME).toContain("da-ready")
    expect(PUBLIC_CHROME).toContain("<PrototypeViewer")
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

// ── Empty chrome-slot collapse: no gap between the toolbar and the iframe ──
describe("empty chrome-slot collapse", () => {
  it("test_empty_chrome_collapse_rule_present — .da-prototype-chrome:empty zeroes padding + drops the border", () => {
    const block = CSS.match(
      /\.design-agent-surface\s+\.da-prototype-chrome:empty\s*\{([^}]*)\}/,
    )
    expect(block).not.toBeNull()
    const body = block![1]
    expect(body).toMatch(/padding:\s*0\b/)
    expect(body).toMatch(/border-bottom:\s*none/)
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

// ── Notify-button hover flash regression ───────────────────────────────────
describe("notify-button hover flash regression", () => {
  it("test_notify_btn_hover_rule_absent — no dedicated :hover override remains for .proto-gen-notify-btn", () => {
    // Regression: the stale ghost-era override forced `background: transparent`
    // on hover at 4-class specificity, outranking the shared
    // `.btn-primary:hover:not(:disabled)` accent fill and flashing the panel
    // white. The control is now `btn btn-primary` in every mode, so the shared
    // hover rule (globals.css) owns its hover state — no dedicated override
    // for this selector should exist at all.
    expect(CSS).not.toContain(".proto-gen-notify-btn:hover")
  })

  it("test_notify_btn_empty_rule_body_absent — no empty/comment-only rule remains for the bare .proto-gen-notify-btn selector", () => {
    const block = CSS.match(
      /\.design-agent-surface\.proto-gen-overlay\s+\.proto-gen-notify-btn\s*\{([^}]*)\}/,
    )
    if (block) {
      // If the bare (non-.btn-primary-qualified) selector still exists at
      // all, its body must not be empty/comment-only — that was exactly the
      // dead rule this ticket removes.
      const body = stripCssComments(block[1]).trim()
      expect(body.length).toBeGreaterThan(0)
    } else {
      expect(block).toBeNull()
    }
  })
})

// ── GenerationLoadingScreen notify-button class regression pin (AC5) ──────
describe("notify-button JSX class unchanged (AC5)", () => {
  it("test_generation_loading_screen_notify_btn_class_unchanged — class attribute stays exactly 'btn btn-primary proto-gen-notify-btn'", () => {
    // Regression pin: this ticket only touches design-agent.css, never the
    // JSX. The notify control must remain btn-primary (no btn-ghost
    // reintroduced) and no other class added or removed.
    expect(GENERATION_LOADING_SCREEN).toContain(
      'className="btn btn-primary proto-gen-notify-btn"',
    )
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

// ── Reskinned viewer placeholder (iterate-reload load mask) ────────────────
describe("viewer placeholder layout matches the bundle-loading cover", () => {
  it("test_design_agent_css_viewer_placeholder_matches_bundle_loading_layout", () => {
    const match = CSS.match(
      /\.design-agent-surface\s+\.da-viewer-placeholder\s*\{([^}]*)\}/,
    )
    expect(match).not.toBeNull()
    const rule = match![1]
    expect(rule).toContain("display: flex")
    expect(rule).toContain("align-items: center")
    expect(rule).toContain("justify-content: center")
    expect(rule).toContain("backdrop-filter: blur(")
  })
})

describe("public chrome PrototypeViewer mount stays timing-unaffected", () => {
  it("test_public_chrome_prototype_viewer_has_no_on_bundle_load_prop", () => {
    const start = PUBLIC_CHROME.indexOf("<PrototypeViewer")
    expect(start).toBeGreaterThan(-1)
    // The mount's own self-closing `/>` sits at the SAME indent as the opening
    // tag; nested JSX (e.g. headControls/stageOverlay children) closes at a
    // deeper indent, so matching on indent (not the first `/>` seen) finds the
    // mount's real end rather than a nested self-closing child element.
    const lineStart = PUBLIC_CHROME.lastIndexOf("\n", start) + 1
    const indent = PUBLIC_CHROME.slice(lineStart, start)
    const closeMarker = `\n${indent}/>`
    const end = PUBLIC_CHROME.indexOf(closeMarker, start)
    expect(end).toBeGreaterThan(start)
    const block = PUBLIC_CHROME.slice(start, end)
    expect(block).not.toContain("onBundleLoad=")
  })
})

describe("bundle-loading cover has no fade-in (residual-exposure closure)", () => {
  it("test_da_bundle_loading_has_no_fade_in_animation", () => {
    // A mount-time opacity ramp would technically paint whatever sits
    // underneath (including a raw error body) at near-zero opacity during
    // its opening frames — the cover must render at full strength the
    // instant it mounts, with no animation at all.
    const match = CSS.match(
      /\.design-agent-surface\s+\.da-bundle-loading\s*\{([^}]*)\}/,
    )
    expect(match).not.toBeNull()
    const rule = match![1]
    expect(rule).not.toMatch(/animation:/)
    expect(CSS).not.toContain("@keyframes da-bundle-fade")
  })
})

// ── Done-turn response body colour (branding: ink, not accent) ────────────
describe("done-turn response body colour", () => {
  it("test_da_activity_done_body_deemphasized_typography — .da-activity-done-body reads as de-emphasized body copy, not a shouted accent label", () => {
    const match = CSS.match(
      /\.design-agent-surface\s+\.da-activity-done-body\s*\{([^}]*)\}/,
    )
    expect(match).not.toBeNull()
    const rule = match![1]
    expect(rule).toContain("font-weight: 400;")
    expect(rule).toContain("color: var(--ink-2);")
    expect(rule).toContain("align-items: flex-start;")
    expect(rule).not.toContain("font-weight: 600;")
    expect(rule).not.toContain("color: var(--ink);")
    expect(rule).not.toContain("var(--accent-ink)")
    expect(rule).not.toContain("align-items: center;")
  })

  it("test_da_activity_done_body_layout_declarations_unchanged — .da-activity-done-body keeps its display/gap layout declarations", () => {
    const match = CSS.match(
      /\.design-agent-surface\s+\.da-activity-done-body\s*\{([^}]*)\}/,
    )
    expect(match).not.toBeNull()
    const rule = match![1]
    expect(rule).toContain("display: flex;")
    expect(rule).toContain("gap: 8px;")
  })

  it("test_da_activity_done_icon_has_optical_margin_nudge — .da-activity-done-icon gains a 1px top margin to align with the de-emphasized, top-aligned body", () => {
    const match = CSS.match(
      /\.design-agent-surface\s+\.da-activity-done-icon\s*\{([^}]*)\}/,
    )
    expect(match).not.toBeNull()
    const rule = match![1]
    expect(rule).toContain("margin-top: 1px;")
  })

  it("test_da_activity_done_icon_other_declarations_unchanged — .da-activity-done-icon's other declarations are byte-unchanged", () => {
    const match = CSS.match(
      /\.design-agent-surface\s+\.da-activity-done-icon\s*\{([^}]*)\}/,
    )
    expect(match).not.toBeNull()
    const rule = match![1]
    expect(rule).toContain("flex-shrink: 0;")
    expect(rule).toContain("width: 16px;")
    expect(rule).toContain("height: 16px;")
    expect(rule).toContain("background: var(--accent-soft);")
    expect(rule).toContain("color: var(--accent);")
    expect(rule).toContain("border-radius: 50%;")
    expect(rule).toContain("font-size: 10px;")
  })

  it("test_da_activity_terminal_done_label_has_scoped_margin — a new, more-specific selector scopes the done-card label's margin to 6px", () => {
    const block = [
      ".design-agent-surface .da-activity-terminal--done .da-activity-agent-label {",
      "  margin: 0 0 6px;",
      "}",
    ].join("\n")
    expect(CSS).toContain(block)
  })

  it("test_da_activity_agent_label_base_rule_unaffected_by_scoped_override — the BASE, unscoped .da-activity-agent-label rule keeps its 4px margin", () => {
    const match = CSS.match(
      /\.design-agent-surface\s+\.da-activity-agent-label\s*\{([^}]*)\}/,
    )
    expect(match).not.toBeNull()
    const rule = match![1]
    expect(rule).toContain("margin: 0 0 4px;")
  })

  it("test_da_activity_terminal_base_rule_is_a_flex_row — the shared .da-activity-terminal rule (skipped/error single-line layout) stays an unstacked flex row", () => {
    const match = CSS.match(
      /\.design-agent-surface\s+\.da-activity-terminal\s*\{([^}]*)\}/,
    )
    expect(match).not.toBeNull()
    const rule = match![1]
    expect(rule).toContain("display: flex;")
    expect(rule).toContain("align-items: center;")
    expect(rule).not.toContain("flex-direction")
  })

  it("test_da_activity_terminal_done_overrides_to_a_column_stack — the done variant overrides the shared row layout into a full-width column stack (label above the response body, not squeezed beside it)", () => {
    const match = CSS.match(
      /\.design-agent-surface\s+\.da-activity-terminal--done\s*\{([^}]*)\}/,
    )
    expect(match).not.toBeNull()
    const rule = match![1]
    expect(rule).toContain("flex-direction: column;")
    expect(rule).toContain("align-items: stretch;")
  })

  it("test_da_activity_terminal_skipped_and_error_unaffected_by_done_override — the --skipped/error terminal kinds are untouched by the done-only column override", () => {
    expect(CSS).not.toMatch(
      /\.design-agent-surface\s+\.da-activity-terminal--skipped\s*\{[^}]*flex-direction/,
    )
    // the --skipped selector itself still exists unmodified (icon + text rules).
    expect(CSS).toContain(
      ".design-agent-surface .da-activity-terminal--skipped .da-activity-terminal-icon {",
    )
    expect(CSS).toContain(
      ".design-agent-surface .da-activity-terminal--skipped .da-activity-terminal-text {",
    )
  })

  it("test_da_activity_agent_label_color_unchanged — .da-activity-agent-label keeps its accent colour (regression pin, label unaffected)", () => {
    const match = CSS.match(
      /\.design-agent-surface\s+\.da-activity-agent-label\s*\{([^}]*)\}/,
    )
    expect(match).not.toBeNull()
    const rule = match![1]
    expect(rule).toContain("color: var(--accent);")
  })

  it("test_da_activity_done_rule_byte_unchanged — the sibling .da-activity-done rule (confirmed dead) is untouched, still accent-ink", () => {
    const block = [
      ".design-agent-surface .da-activity-done {",
      "  display: flex;",
      "  align-items: center;",
      "  gap: 8px;",
      "  font-size: 12.5px;",
      "  font-weight: 600;",
      "  color: var(--accent-ink);",
      "}",
    ].join("\n")
    expect(CSS).toContain(block)
  })
})
