// @vitest-environment jsdom
//
// useArtifactUrlSync — the fork-nav reflect guard (Deliverable, load-bearing
// non-regression). `skipArtifactReflectOnNavRef` (NavigationContext) is read
// + one-shot-consumed ONLY inside the unified `// ── drawer → URL ──` effect's
// `PRD_PARAM` reflect arm — this file drives that effect directly (via
// `content`/`contentPanelTab`, mirroring useArtifactUrlSync.dom.test.tsx's
// own harness) and proves: (a) the guard skips exactly that one `?prd=`
// reflect and resets itself; (b) with the guard unset the `?prd=` reflect
// fires exactly as before; (c) the guard is scoped to the PRD/tickets arm
// ONLY — the evidence reflect and the strip-all branch both still fire even
// with the guard set (they are provably unreachable by this guard: strip-all
// returns before `want` is computed, evidence is a different `want.key`);
// (d) every URL→drawer OPEN flow is unaffected in BOTH ref states.
import * as React from "react"
import { act, cleanup, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

window.scrollTo = (() => {}) as typeof window.scrollTo

vi.mock("../../../lib/api", () => ({
  evidenceApi: { get: vi.fn() },
  prdApi: { resolveIdByPublicId: vi.fn() },
}))

let searchString = ""
let currentPathname = "/backlog"
const pushSpy = vi.fn()
const replaceSpy = vi.fn()
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushSpy, replace: replaceSpy, prefetch: vi.fn() }),
  usePathname: () => currentPathname,
  useSearchParams: () => new URLSearchParams(searchString),
}))

import { evidenceApi, prdApi } from "../../../lib/api"
import { NavigationProvider, useNavigation } from "../../../context/NavigationContext"
import { ContentProvider, useContent } from "../../../context/ContentContext"
import { useArtifactUrlSync } from "../useArtifactUrlSync"

function Harness() {
  useArtifactUrlSync()
  const nav = useNavigation()
  const { content, setContent } = useContent()
  ;(window as unknown as { __harness: unknown }).__harness = { nav, content, setContent }
  return (
    <div>
      <div data-testid="pending-prd-tab">{JSON.stringify(nav.pendingPrdTab)}</div>
      <div data-testid="content-panel-tab">{String(nav.contentPanelTab)}</div>
    </div>
  )
}

function renderHarness() {
  return render(
    <NavigationProvider>
      <ContentProvider>
        <Harness />
      </ContentProvider>
    </NavigationProvider>,
  )
}

function harness() {
  return (window as unknown as {
    __harness: {
      nav: ReturnType<typeof useNavigation>
      content: ReturnType<typeof useContent>["content"]
      setContent: ReturnType<typeof useContent>["setContent"]
    }
  }).__harness
}

beforeEach(() => {
  searchString = ""
  currentPathname = "/backlog"
  pushSpy.mockClear()
  replaceSpy.mockClear()
  vi.mocked(evidenceApi.get).mockReset()
  vi.mocked(prdApi.resolveIdByPublicId).mockReset()
})
afterEach(() => {
  cleanup()
})

describe("useArtifactUrlSync — PRD reflect guard (AC-4, AC-14)", () => {
  it("test_prd_reflect_skipped_and_ref_reset_when_guard_set — guard=true: the PRD_PARAM arm issues NO router.replace and one-shot-resets the ref", async () => {
    await act(async () => {
      renderHarness()
    })
    await act(async () => {
      harness().nav.skipArtifactReflectOnNavRef.current = true
      harness().setContent({ prd: { prd_id: 99, title: "t", metaLine: "", sections: [] } })
      harness().nav.openContentPanel("prd")
    })
    // Give the effect a full settle window — a genuine skip never fires.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50))
    })
    expect(replaceSpy).not.toHaveBeenCalled()
    expect(harness().nav.skipArtifactReflectOnNavRef.current).toBe(false)
  })

  it("test_prd_reflect_fires_when_guard_unset — guard=false: the ?prd= reflect fires exactly as at base", async () => {
    await act(async () => {
      renderHarness()
    })
    expect(harness().nav.skipArtifactReflectOnNavRef.current).toBe(false)
    await act(async () => {
      harness().setContent({ prd: { prd_id: 99, title: "t", metaLine: "", sections: [] } })
      harness().nav.openContentPanel("prd")
    })
    await waitFor(() => {
      expect(replaceSpy).toHaveBeenCalledWith("/backlog?prd=99", { scroll: false })
    })
  })

  it("test_evidence_reflect_fires_even_when_guard_set — the EVIDENCE_PARAM reflect is untouched by the guard (gated on want.key===PRD_PARAM only)", async () => {
    await act(async () => {
      renderHarness()
    })
    await act(async () => {
      harness().nav.skipArtifactReflectOnNavRef.current = true
      harness().setContent({ evidenceId: 42 })
      harness().nav.openContentPanel("evidence")
    })
    await waitFor(() => {
      expect(replaceSpy).toHaveBeenCalledWith("/backlog?evidence=42", { scroll: false })
    })
    // The guard is scoped to the PRD arm — an evidence reflect never
    // consumes it (still true here, untouched).
    expect(harness().nav.skipArtifactReflectOnNavRef.current).toBe(true)
  })

  it("test_strip_all_branch_fires_even_when_guard_set — contentPanelTab=null STILL strips existing params (it returns before `want` is computed — unreachable by the guard)", async () => {
    searchString = "prd=99"
    await act(async () => {
      renderHarness()
    })
    // Land on the PRD tab first (matches the incoming URL — no-op replace).
    await act(async () => {
      harness().setContent({ prd: { prd_id: 99, title: "t", metaLine: "", sections: [] } })
      harness().nav.openContentPanel("prd")
    })
    replaceSpy.mockClear()
    await act(async () => {
      harness().nav.skipArtifactReflectOnNavRef.current = true
      harness().nav.closeContentPanel()
    })
    await waitFor(() => {
      expect(replaceSpy).toHaveBeenCalledWith("/backlog", { scroll: false })
    })
    // The strip-all branch never reads or resets the ref (it can't reach
    // this code path at all) — still true.
    expect(harness().nav.skipArtifactReflectOnNavRef.current).toBe(true)
  })

  it("test_open_flows_unchanged_both_ref_states — the ?prd= URL→drawer OPEN effect is byte-behaviour-unchanged with the guard set", async () => {
    searchString = "prd=515"
    await act(async () => {
      renderHarness()
    })
    // Set the guard AFTER mount (simulating it being left set from a prior
    // fork nav) — the OPEN effect (114-150) never reads this ref at all, so
    // it must resolve identically either way.
    harness().nav.skipArtifactReflectOnNavRef.current = true
    await waitFor(() => {
      const pending = JSON.parse(screen.getByTestId("pending-prd-tab").textContent || "null")
      expect(pending?.source?.kind).toBe("load")
      expect(pending?.source?.prdId).toBe(515)
    })
    expect(pushSpy).toHaveBeenCalledWith("/")
    // Untouched by the OPEN effect — only the drawer→URL reflect effect
    // ever reads/resets it, and this test never opened a NEW panel state.
    expect(harness().nav.skipArtifactReflectOnNavRef.current).toBe(true)
  })

  it("the ?evidence= URL→drawer OPEN effect is unchanged with the guard set", async () => {
    vi.mocked(evidenceApi.get).mockResolvedValue({
      id: 42, brief_id: 7, insight_index: 2, generated_at: "", title: "Dark mode",
      payload_md: "<html></html>", status: "ready", variant: "v3",
    } as never)
    searchString = "evidence=42"
    await act(async () => {
      renderHarness()
    })
    harness().nav.skipArtifactReflectOnNavRef.current = true
    await waitFor(() => expect(evidenceApi.get).toHaveBeenCalledWith(42))
    await waitFor(() => {
      const pending = JSON.parse(screen.getByTestId("pending-prd-tab").textContent || "null")
      expect(pending?.source?.kind).toBe("evidence")
    })
  })

  it("the ?ticket= URL→drawer OPEN effect is unchanged with the guard set", async () => {
    searchString = `ticket=${encodeURIComponent("prd-77-story-abc123")}`
    await act(async () => {
      renderHarness()
    })
    harness().nav.skipArtifactReflectOnNavRef.current = true
    await waitFor(() => {
      const pending = JSON.parse(screen.getByTestId("pending-prd-tab").textContent || "null")
      expect(pending?.source?.kind).toBe("load")
      expect(pending?.source?.prdId).toBe(77)
    })
  })
})
