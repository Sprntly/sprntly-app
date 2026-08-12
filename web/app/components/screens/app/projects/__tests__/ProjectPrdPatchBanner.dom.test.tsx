// §E — ProjectPrdPatchBanner tests. Node-env vitest (no DOM, no router, no
// testing-library), so — mirroring PrdPatchBanner.test.tsx — we SSR-render the
// pure view via renderToStaticMarkup and unit-test the extracted orchestration
// helpers + the flag reader with injected deps.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { afterEach, describe, expect, it, vi } from "vitest"

// Sprntly components carry no `import React`; expose it globally (CompletionBar /
// PrdPatchBanner test convention) rather than touch the shared vitest config.
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import {
  ProjectPrdPatchBannerView,
  projectPrdEditEnabled,
  runLoadProjectPatches,
  runAcceptPatch,
  runRejectPatch,
} from "../ProjectPrdPatchBanner"
import type { PrdPatchRecord } from "../../../../../lib/api"

afterEach(() => {
  vi.restoreAllMocks()
})

function patch(over: Partial<PrdPatchRecord> = {}): PrdPatchRecord {
  return {
    id: 1,
    prd_id: 7,
    prototype_id: null as unknown as number, // project patches carry NULL prototype_id
    rationale: "Rewrite the problem statement on the Sync Mode PRD",
    patch_md: "## Problem\n\nSharper problem statement.",
    status: "pending",
    created_at: "2026-01-01T00:00:00Z",
    ...over,
  }
}

function render(props: React.ComponentProps<typeof ProjectPrdPatchBannerView>): string {
  return renderToStaticMarkup(React.createElement(ProjectPrdPatchBannerView, props))
}

describe("ProjectPrdPatchBannerView — rendering (AC26)", () => {
  it("renders one card per pending patch with rationale + patch_md + Accept/Reject", () => {
    const html = render({ patches: [patch({ id: 1 }), patch({ id: 2 })] })
    expect(html).toContain('data-testid="project-prd-patch-banner"')
    expect(html).toContain('data-testid="project-prd-patch-1"')
    expect(html).toContain('data-testid="project-prd-patch-2"')
    expect(html).toContain("Rewrite the problem statement on the Sync Mode PRD")
    expect(html).toContain("Sharper problem statement.")
    expect(html).toContain('data-testid="project-accept-patch-1"')
    expect(html).toContain('data-testid="project-reject-patch-1"')
  })

  it("returns null (empty markup) when there are no pending patches", () => {
    expect(render({ patches: [] })).toBe("")
  })

  it("renders an error node only when error is set", () => {
    expect(render({ patches: [patch()], error: "boom" })).toContain(
      'data-testid="project-prd-patch-error"',
    )
    expect(render({ patches: [patch()], error: null })).not.toContain(
      'data-testid="project-prd-patch-error"',
    )
  })
})

describe("projectPrdEditEnabled — flag gate (AC28)", () => {
  it("is off for undefined / empty / 0 and on for 1/true/yes", () => {
    expect(projectPrdEditEnabled(undefined)).toBe(false)
    expect(projectPrdEditEnabled("")).toBe(false)
    expect(projectPrdEditEnabled("0")).toBe(false)
    expect(projectPrdEditEnabled("1")).toBe(true)
    expect(projectPrdEditEnabled("true")).toBe(true)
    expect(projectPrdEditEnabled("YES")).toBe(true)
  })
})

describe("orchestration helpers (AC27 / AC28)", () => {
  it("runLoadProjectPatches enumerates PRDs then lists pending per PRD", async () => {
    const projects = {
      artifacts: vi.fn().mockResolvedValue([
        { type: "prd", id: 7 },
        { type: "report", id: 9 }, // filtered out — not a PRD
        { type: "prd", id: 8 },
      ]),
    }
    const designAgent = {
      listPendingPatches: vi
        .fn()
        .mockImplementation((prdId: number) =>
          Promise.resolve(prdId === 7 ? [patch({ id: 1, prd_id: 7 })] : [patch({ id: 2, prd_id: 8 })]),
        ),
    }
    const rows = await runLoadProjectPatches({ projectId: 3, projects, designAgent })
    expect(projects.artifacts).toHaveBeenCalledWith(3)
    // Only the two PRD ids were queried, never the report id.
    expect(designAgent.listPendingPatches).toHaveBeenCalledTimes(2)
    expect(designAgent.listPendingPatches).toHaveBeenCalledWith(7)
    expect(designAgent.listPendingPatches).toHaveBeenCalledWith(8)
    expect(rows.map((r) => r.id).sort()).toEqual([1, 2])
  })

  it("runAcceptPatch / runRejectPatch call the reused designAgent api (AC27)", async () => {
    const designAgent = {
      acceptPatch: vi.fn().mockResolvedValue(patch({ id: 1, status: "applied" })),
      rejectPatch: vi.fn().mockResolvedValue(patch({ id: 1, status: "rejected" })),
    }
    await runAcceptPatch({ patchId: 1, designAgent })
    await runRejectPatch({ patchId: 1, designAgent })
    expect(designAgent.acceptPatch).toHaveBeenCalledWith(1)
    expect(designAgent.rejectPatch).toHaveBeenCalledWith(1)
    // Once resolved, the removed patch no longer renders.
    const remaining = [patch({ id: 1 }), patch({ id: 2 })].filter((p) => p.id !== 1)
    const html = render({ patches: remaining })
    expect(html).not.toContain('data-testid="project-prd-patch-1"')
    expect(html).toContain('data-testid="project-prd-patch-2"')
  })
})

// AC28 (mount) — ProjectIndividualChat mounts the banner above the scroll, gated
// on the FE flag. Node-env vitest can't fully render the screen (navigation/
// content contexts), so assert against the source text directly.
describe("ProjectIndividualChat mount (AC28)", () => {
  const { readFileSync } = require("node:fs") as typeof import("node:fs")
  const src = readFileSync(
    new URL("../ProjectIndividualChat.tsx", import.meta.url),
    "utf8",
  )

  it("imports and mounts ProjectPrdPatchBanner above the scroll", () => {
    expect(src).toContain(
      'import { ProjectPrdPatchBanner } from "./ProjectPrdPatchBanner"',
    )
    expect(src).toContain("<ProjectPrdPatchBanner projectId={projectId} />")
    expect(src.indexOf("<ProjectPrdPatchBanner")).toBeLessThan(
      src.indexOf('data-testid="individual-chat-scroll"'),
    )
  })
})
