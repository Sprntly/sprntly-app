// @vitest-environment jsdom
//
// useArtifactUrlSync — shell-level `?prd=`/`?evidence=`/`?ticket=` deep-link
// sync, mounted once in AppShell (not per-page). This test drives the hook
// directly under real NavigationProvider/ContentProvider so both directions
// (URL → drawer, drawer → URL) exercise real context state, with only the
// network boundary (`evidenceApi`) and `next/navigation` mocked.
import * as React from "react"
import { act, cleanup, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

// openPrdTab calls window.scrollTo (unimplemented in jsdom) — stub it.
window.scrollTo = (() => {}) as typeof window.scrollTo

vi.mock("../../../lib/api", () => ({
  evidenceApi: {
    get: vi.fn(),
  },
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

import { evidenceApi } from "../../../lib/api"
import { NavigationProvider, useNavigation } from "../../../context/NavigationContext"
import { ContentProvider, useContent } from "../../../context/ContentContext"
import { useArtifactUrlSync } from "../useArtifactUrlSync"

// Renders the hook + exposes enough context state/setters as test-ids/handles
// for assertions and for driving the drawer→URL direction imperatively.
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
})
afterEach(() => {
  cleanup()
})

describe("useArtifactUrlSync — URL → drawer", () => {
  it("opens the PRD named by ?prd= from a non-chat page", async () => {
    searchString = "prd=515"
    await act(async () => {
      renderHarness()
    })
    await waitFor(() => {
      const pending = JSON.parse(screen.getByTestId("pending-prd-tab").textContent || "null")
      expect(pending?.source?.kind).toBe("load")
      expect(pending?.source?.prdId).toBe(515)
    })
    // openPrdTab always routes to `/` — the panel's home, regardless of the
    // page the link was opened from (proves "works from any page").
    expect(pushSpy).toHaveBeenCalledWith("/")
  })

  it("does nothing without any artifact param", async () => {
    searchString = ""
    await act(async () => {
      renderHarness()
    })
    await Promise.resolve()
    expect(screen.getByTestId("pending-prd-tab").textContent).toBe("null")
    expect(pushSpy).not.toHaveBeenCalled()
  })

  it("ignores a non-numeric ?prd= value", async () => {
    searchString = "prd=abc"
    await act(async () => {
      renderHarness()
    })
    await Promise.resolve()
    expect(screen.getByTestId("pending-prd-tab").textContent).toBe("null")
  })

  it("resolves ?evidence= via evidenceApi.get and opens the evidence-first tab", async () => {
    vi.mocked(evidenceApi.get).mockResolvedValue({
      id: 42, brief_id: 7, insight_index: 2, generated_at: "", title: "Dark mode",
      payload_md: "<html></html>", status: "ready", variant: "v3",
    } as never)
    searchString = "evidence=42"
    await act(async () => {
      renderHarness()
    })
    await waitFor(() => expect(evidenceApi.get).toHaveBeenCalledWith(42))
    await waitFor(() => {
      const pending = JSON.parse(screen.getByTestId("pending-prd-tab").textContent || "null")
      expect(pending?.source?.kind).toBe("evidence")
      expect(pending?.source?.meta).toEqual({ briefId: 7, insightIndex: 2 })
    })
  })

  it("a foreign-tenant/missing ?evidence= 404s quietly — no content, no crash", async () => {
    vi.mocked(evidenceApi.get).mockRejectedValue(new Error("Not found"))
    searchString = "evidence=999"
    await act(async () => {
      renderHarness()
    })
    await waitFor(() => expect(evidenceApi.get).toHaveBeenCalledWith(999))
    await Promise.resolve()
    expect(screen.getByTestId("pending-prd-tab").textContent).toBe("null")
  })

  it("parses the prd_id out of a ?ticket= key and opens that PRD", async () => {
    searchString = `ticket=${encodeURIComponent("prd-77-story-abc123")}`
    await act(async () => {
      renderHarness()
    })
    await waitFor(() => {
      const pending = JSON.parse(screen.getByTestId("pending-prd-tab").textContent || "null")
      expect(pending?.source?.kind).toBe("load")
      expect(pending?.source?.prdId).toBe(77)
    })
  })

  it("ignores a ?ticket= value with no embedded prd id", async () => {
    searchString = "ticket=some-legacy-slug"
    await act(async () => {
      renderHarness()
    })
    await Promise.resolve()
    expect(screen.getByTestId("pending-prd-tab").textContent).toBe("null")
  })
})

describe("useArtifactUrlSync — drawer → URL", () => {
  it("reflects the open PRD tab's id onto the URL", async () => {
    await act(async () => {
      renderHarness()
    })
    await act(async () => {
      harness().setContent({ prd: { prd_id: 99, title: "t", metaLine: "", sections: [] } })
      harness().nav.openContentPanel("prd")
    })
    await waitFor(() => {
      expect(replaceSpy).toHaveBeenCalledWith("/backlog?prd=99", { scroll: false })
    })
  })

  it("reflects the open Evidence tab's id onto the URL", async () => {
    await act(async () => {
      renderHarness()
    })
    await act(async () => {
      harness().setContent({ evidenceId: 42 })
      harness().nav.openContentPanel("evidence")
    })
    await waitFor(() => {
      expect(replaceSpy).toHaveBeenCalledWith("/backlog?evidence=42", { scroll: false })
    })
  })

  it("strips the param when the panel closes", async () => {
    searchString = "prd=99"
    await act(async () => {
      renderHarness()
    })
    // Land on the PRD tab first, matching the incoming URL (no-op replace),
    // then close — the close must strip it.
    await act(async () => {
      harness().setContent({ prd: { prd_id: 99, title: "t", metaLine: "", sections: [] } })
      harness().nav.openContentPanel("prd")
    })
    replaceSpy.mockClear()
    await act(async () => {
      harness().nav.closeContentPanel()
    })
    await waitFor(() => {
      expect(replaceSpy).toHaveBeenCalledWith("/backlog", { scroll: false })
    })
  })
})
