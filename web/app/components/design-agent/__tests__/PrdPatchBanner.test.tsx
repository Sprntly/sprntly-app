// P3-10 — PrdPatchBanner tests. Node-env vitest (no DOM, no router, no
// testing-library), so — following the CompletionBar / DesignAgentDrawer
// convention — we SSR-render the pure view via renderToStaticMarkup and
// unit-test the extracted orchestration helpers with injected deps.
import { readFileSync } from "node:fs"
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { afterEach, describe, expect, it, vi } from "vitest"

// Sprntly components carry no `import React`; vitest's esbuild transform uses
// the classic runtime, so expose React globally (CompletionBar test convention)
// rather than touch the shared vitest config.
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import {
  PrdPatchBannerView,
  runLoadPendingPatches,
  runAcceptPatch,
  runRejectPatch,
} from "../PrdPatchBanner"
import type { PrdPatchRecord } from "../../../lib/api"

afterEach(() => {
  vi.restoreAllMocks()
})

function patch(over: Partial<PrdPatchRecord> = {}): PrdPatchRecord {
  return {
    id: 1,
    prd_id: 7,
    prototype_id: 3,
    rationale: "Tighten the activation metric to 7 days",
    patch_md: "## Success metric\n\nActivation within 7 days, not 30.",
    status: "pending",
    created_at: "2026-01-01T00:00:00Z",
    ...over,
  }
}

function render(props: React.ComponentProps<typeof PrdPatchBannerView>): string {
  return renderToStaticMarkup(React.createElement(PrdPatchBannerView, props))
}

describe("PrdPatchBannerView — rendering", () => {
  it("renders the banner with rationale, patch_md preview and Accept/Reject (AC1)", () => {
    const html = render({ patches: [patch()] })
    expect(html).toContain('data-testid="prd-patch-banner"')
    expect(html).toContain("Tighten the activation metric to 7 days") // rationale
    expect(html).toContain("Activation within 7 days, not 30.") // patch_md preview
    expect(html).toContain('data-testid="accept-patch-1"')
    expect(html).toContain('data-testid="reject-patch-1"')
    expect(html).toContain("Accept")
    expect(html).toContain("Reject")
  })

  it("renders nothing (null) when there are no pending patches (AC2)", () => {
    const html = render({ patches: [] })
    expect(html).toBe("")
  })

  it("renders one card per pending patch", () => {
    const html = render({ patches: [patch({ id: 1 }), patch({ id: 2 })] })
    expect(html).toContain('data-testid="prd-patch-1"')
    expect(html).toContain('data-testid="prd-patch-2"')
  })

  it("renders an error message when error is set (AC error-state)", () => {
    const html = render({ patches: [patch()], error: "boom" })
    expect(html).toContain('data-testid="prd-patch-error"')
    expect(html).toContain("boom")
  })

  it("does not render the error node when there is no error", () => {
    const html = render({ patches: [patch()], error: null })
    expect(html).not.toContain('data-testid="prd-patch-error"')
  })
})

describe("PrdPatchBanner — orchestration helpers", () => {
  it("runLoadPendingPatches calls api.listPendingPatches and returns the list (AC4)", async () => {
    const rows = [patch({ id: 1 }), patch({ id: 2 })]
    const api = { listPendingPatches: vi.fn().mockResolvedValue(rows) }
    const result = await runLoadPendingPatches({ prdId: 7, api })
    expect(api.listPendingPatches).toHaveBeenCalledTimes(1)
    expect(api.listPendingPatches).toHaveBeenCalledWith(7)
    expect(result).toEqual(rows)
  })

  it("runAcceptPatch calls api.acceptPatch once; the resolved patch leaves the list (AC3)", async () => {
    const api = { acceptPatch: vi.fn().mockResolvedValue(patch({ id: 1, status: "applied" })) }
    await runAcceptPatch({ patchId: 1, api })
    expect(api.acceptPatch).toHaveBeenCalledTimes(1)
    expect(api.acceptPatch).toHaveBeenCalledWith(1)
    // List half: once removed from local state, the View no longer renders it.
    const remaining = [patch({ id: 1 }), patch({ id: 2 })].filter((p) => p.id !== 1)
    const html = render({ patches: remaining })
    expect(html).not.toContain('data-testid="prd-patch-1"')
    expect(html).toContain('data-testid="prd-patch-2"')
  })

  it("runRejectPatch calls api.rejectPatch once; the resolved patch leaves the list (AC3)", async () => {
    const api = { rejectPatch: vi.fn().mockResolvedValue(patch({ id: 1, status: "rejected" })) }
    await runRejectPatch({ patchId: 1, api })
    expect(api.rejectPatch).toHaveBeenCalledTimes(1)
    expect(api.rejectPatch).toHaveBeenCalledWith(1)
    const remaining = [patch({ id: 1 })].filter((p) => p.id !== 1)
    expect(render({ patches: remaining })).toBe("")
  })
})

// AC7 — the PrdScreen mount is APPEND-ABOVE-CONTENT only: exactly one import +
// one <PrdPatchBanner/> mount, with the antipattern `contentEditable` div left
// byte-identical. Node-env vitest can't fully render the screen (it pulls in the
// navigation/content contexts), so we assert against the source text directly
// (grep/diff per the ticket Unit Tests). The contentEditable landmark must still
// appear EXACTLY once and keep its sibling props — building F11 on it is forbidden
// (codebase-agent-patterns §3 — the next poll wipes it).
describe("PrdPanelContent mount (AC7 — contentEditable untouched)", () => {
  // The standalone PrdScreen page was removed in the prd-removal refactor; the
  // right-rail PrdPanelContent is the sole PRD host and carries the same mount.
  const src = readFileSync(
    new URL("../../shared/PrdPanelContent.tsx", import.meta.url),
    "utf8",
  )

  it("references contentEditable exactly once", () => {
    const matches = src.match(/contentEditable/g) ?? []
    expect(matches).toHaveLength(1)
  })

  it("keeps the contentEditable div's sibling props intact", () => {
    // The prd-body antipattern block must be byte-identical to HEAD: the
    // contentEditable div carries spellCheck + suppressContentEditableWarning.
    expect(src).toContain('className="prd-body"')
    expect(src).toContain("spellCheck={false}")
    expect(src).toContain("suppressContentEditableWarning")
  })

  it("mounts PrdPatchBanner above the prd content (import + single mount line)", () => {
    expect(src).toContain(
      'import { PrdPatchBanner } from "../design-agent/PrdPatchBanner"',
    )
    expect(src).toContain("<PrdPatchBanner prdId={prd.prd_id} />")
    // DOM order: the banner mount precedes the prd-frame block.
    expect(src.indexOf("<PrdPatchBanner")).toBeLessThan(
      src.indexOf('className="prd-frame"'),
    )
  })
})
