import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { readFileSync } from "node:fs"
import { fileURLToPath } from "node:url"
import { dirname, resolve } from "node:path"
import { describe, expect, it } from "vitest"
import {
  buildGenerateParams,
  DesignAgentDrawerView,
  redirectToConnect,
} from "../DesignAgentDrawer"
import { connectorsApi } from "../../../lib/api"

// PrdSections-style shim: Sprntly components have no `import React`; vitest's
// esbuild transform defaults to the classic runtime, so expose React globally
// rather than touch the shared vitest config (outside the engagement's map).
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const here = dirname(fileURLToPath(import.meta.url))
const noop = () => {}

function render(
  props: Partial<Parameters<typeof DesignAgentDrawerView>[0]> = {},
) {
  return renderToStaticMarkup(
    React.createElement(DesignAgentDrawerView, {
      open: true,
      onOpenChange: noop,
      prdId: 1,
      figmaFileKey: null,
      showToast: noop,
      ...props,
    }),
  )
}

const drawerSource = readFileSync(
  resolve(here, "../DesignAgentDrawer.tsx"),
  "utf8",
)
const cssSource = readFileSync(
  resolve(here, "../design-agent.css"),
  "utf8",
)

// ─── AC1: the three source options render ────────────────────────────────────

describe("source IA renders three options (AC1)", () => {
  it("test_source_ia_renders_three_options — Figma block, repo block, website fallback + floor", () => {
    const html = render({ figmaFileKey: null })
    // Section heading
    expect(html).toContain("Source for this prototype")
    // (a) Figma block
    expect(html).toContain("src-block")
    expect(html).toContain(">Figma<")
    // (b) Repo / codebase block
    expect(html).toContain("Connect a repo")
    // (c) website-style inference labelled as the EXPLICIT fallback
    expect(html).toContain("src-fallback-note")
    // …above the retained website-URL + manual color/font floor
    expect(html).toContain('id="dap-website-url"')
    expect(html).toContain('id="dap-manual-color"')
    expect(html).toContain('id="dap-manual-font"')
  })
})

// ─── AC2: Figma block reflects connected state ───────────────────────────────

describe("Figma block connected state (AC2)", () => {
  it("test_figma_block_connected — key present → 'Figma design files detected', no Connect button", () => {
    const html = render({ figmaFileKey: "abc" })
    expect(html).toContain("Figma design files detected")
    expect(html).not.toContain("Connect Figma")
    // The website fallback floor is hidden when a Figma source is connected.
    expect(html).not.toContain('id="dap-website-url"')
  })

  it("test_figma_block_not_connected — no key → 'No Figma source connected' + Connect Figma button", () => {
    const html = render({ figmaFileKey: null })
    expect(html).toContain("No Figma source connected")
    expect(html).toContain("Connect Figma")
    expect(html).toContain("src-connect-btn")
  })
})

// ─── AC3: connect affordances wire to the EXISTING entry point ───────────────

describe("connect affordances wire to existing entry points (AC3)", () => {
  it("test_connect_figma_routes_to_existing_entry_point — redirect target is connectorsApi.figmaAuthorizeUrl()", () => {
    const loc = { href: "" }
    redirectToConnect(connectorsApi.figmaAuthorizeUrl, loc)
    expect(loc.href).toBe(connectorsApi.figmaAuthorizeUrl())
    expect(loc.href).toContain("/v1/connectors/figma/authorize")
    // The button's onClick wires to that exact helper (not a no-op, not inline OAuth).
    expect(drawerSource).toContain(
      "redirectToConnect(connectorsApi.figmaAuthorizeUrl)",
    )
  })

  it("test_connect_repo_routes_to_existing_entry_point — redirect target is connectorsApi.githubAuthorizeUrl()", () => {
    const loc = { href: "" }
    redirectToConnect(connectorsApi.githubAuthorizeUrl, loc)
    expect(loc.href).toBe(connectorsApi.githubAuthorizeUrl())
    expect(loc.href).toContain("/v1/connectors/github/authorize")
    expect(drawerSource).toContain(
      "redirectToConnect(connectorsApi.githubAuthorizeUrl)",
    )
  })
})

// ─── AC5: no connector-lane / OAuth-handshake code added to the drawer ───────

describe("drawer adds no OAuth handshake / connector-lane code (AC5)", () => {
  it("test_drawer_adds_no_oauth_handshake — only *AuthorizeUrl from connectorsApi; no token/connections/status primitives", () => {
    // The drawer reuses ONLY the two authorize-URL helpers from connectorsApi.
    expect(drawerSource).toContain("connectorsApi.figmaAuthorizeUrl")
    expect(drawerSource).toContain("connectorsApi.githubAuthorizeUrl")
    // It does NOT touch any connector-internals / OAuth-state / status fetch.
    for (const forbidden of [
      "disconnectFigma",
      "disconnectGithub",
      "connectorsApi.status",
      "listConnectors",
      "exchangeToken",
      "client_id",
      "connections",
      "oauth",
      "access_token",
    ]) {
      expect(drawerSource).not.toContain(forbidden)
    }
    // No hand-rolled authorize URL beyond reusing the helpers verbatim.
    expect(drawerSource).not.toContain("/authorize`")
    expect(drawerSource).not.toContain('"/v1/connectors')
  })
})

// ─── AC4: website fallback retained + buildGenerateParams parity ─────────────

describe("website fallback retained + buildGenerateParams parity (AC4)", () => {
  it("test_website_fallback_inputs_render_when_no_figma — floor inputs render under the fallback note", () => {
    const html = render({ figmaFileKey: null })
    const noteIdx = html.indexOf("src-fallback-note")
    const urlIdx = html.indexOf('id="dap-website-url"')
    expect(noteIdx).toBeGreaterThanOrEqual(0)
    expect(urlIdx).toBeGreaterThan(noteIdx) // floor sits UNDER the fallback note
    expect(html).toContain('id="dap-manual-color"')
    expect(html).toContain('id="dap-manual-font"')
  })

  it("test_build_generate_params_unchanged — produces the documented P5-02 shape, identical to pre-restructure", () => {
    const params = buildGenerateParams({
      prdId: 9,
      platform: "both",
      instructions: "go dark",
      figmaFileKey: null,
      websiteUrl: "https://acme.com",
      manualColor: "#3b82f6",
      manualFont: "Inter",
    })
    expect(params).toEqual({
      prd_id: 9,
      target_platform: "both",
      instructions: "go dark",
      figma_file_key: null,
      website_url: "https://acme.com",
      manual_design: { primary_color: "#3b82f6", font_family: "Inter" },
    })

    // Floor-absent shape: blank URL + missing font → both null.
    const empty = buildGenerateParams({
      prdId: 9,
      platform: "both",
      instructions: "",
      figmaFileKey: null,
      websiteUrl: "",
      manualColor: "#3b82f6",
      manualFont: "",
    })
    expect(empty.website_url).toBeNull()
    expect(empty.manual_design).toBeNull()

    // Figma path threads the key unchanged.
    const figma = buildGenerateParams({
      prdId: 5,
      platform: "mobile",
      instructions: "x",
      figmaFileKey: "FK",
      websiteUrl: "",
      manualColor: "#000000",
      manualFont: "",
    })
    expect(figma.figma_file_key).toBe("FK")
    expect(figma.target_platform).toBe("mobile")
  })
})

// ─── AC6: appended CSS is scoped + token-only ────────────────────────────────

/** Slice the appended P6-15 block (from its sentinel header comment to EOF). */
function appendedCssBlock(): string {
  const marker = "Source-first IA (P6-15 / UX-5)"
  const idx = cssSource.indexOf(marker)
  expect(idx).toBeGreaterThanOrEqual(0)
  return cssSource.slice(idx)
}

describe("appended src-* CSS is scoped + token-only (AC6)", () => {
  it("test_css_src_rules_scoped_and_token_only — every .src-* selector prefixed .design-agent-surface; no colour literal", () => {
    const block = appendedCssBlock()
    // Every selector line that targets a .src-* class is scoped.
    const selectorLines = block
      .split("\n")
      .filter((l) => l.includes("{") && l.includes(".src-"))
    expect(selectorLines.length).toBeGreaterThan(0)
    for (const line of selectorLines) {
      expect(line.trim().startsWith(".design-agent-surface")).toBe(true)
    }
    // No literal colour anywhere in the appended block — all var(--…).
    expect(block).not.toMatch(/#[0-9a-fA-F]{3,8}\b/)
    expect(block).not.toMatch(/\brgba?\(/)
    expect(block).not.toMatch(/\bhsla?\(/)
    // The src-* family is present.
    for (const sel of [
      ".design-agent-surface .src-block",
      ".design-agent-surface .src-connect-btn",
      ".design-agent-surface .src-fallback-note",
    ]) {
      expect(block).toContain(sel)
    }
  })
})

// ─── AC7: P6-11-owned rules unchanged (append-only, working-tree invariant) ───

describe("P6-11 CSS unchanged — append-only (AC7)", () => {
  it("test_p6_11_css_blocks_unchanged — src-* appended at EOF, after the P6-11 tail block; P6-11 header intact", () => {
    // P6-11 still owns the header.
    expect(cssSource).toContain("P6-11 (UX-1) OWNS this file")
    // P6-11's current last block is `.prd-design-empty`; my appended block must
    // come AFTER it (proves append-at-EOF without diffing a git rev — UX-WAVE
    // rule 1 forbids git show <sha> in test code; this is a working-tree
    // invariant only).
    const p611TailIdx = cssSource.indexOf(".prd-design-empty")
    const appendedIdx = cssSource.indexOf("Source-first IA (P6-15 / UX-5)")
    expect(p611TailIdx).toBeGreaterThanOrEqual(0)
    expect(appendedIdx).toBeGreaterThan(p611TailIdx)
    // Nothing but the appended src-* block lives after the marker — no P6-11
    // rule was relocated below it.
    const after = cssSource.slice(appendedIdx)
    expect(after).not.toContain(".prd-design-empty")
    expect(after).not.toContain(".da-public-root")
  })
})
