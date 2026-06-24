// @vitest-environment jsdom
//
// Container mount test for the read-only `roadmapdoc` artifact view
// (RoadmapDocScreen). It fetches GET /v1/company/roadmap-doc (roadmapDocApi.get)
// and renders the stored roadmap in the design's rmdoc word-doc layout.
//
// Covers: renders the fetched doc (caption + title + extracted body), the empty
// state when none is uploaded, and that the `data-art-view="roadmapdoc"` wrapper
// is present (CSS hides tabs/share for this view).
//
// Matchers: native DOM only.
import * as React from "react"
import { act, cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const getMock = vi.fn()

// Mock AppLayout to render children directly — keeps the heavy app chrome
// (Sidebar / NavigationContext / CompanyContext) out of this focused unit test.
vi.mock("../AppLayout", () => ({
  AppLayout: ({ children }: { children: React.ReactNode }) =>
    React.createElement("div", null, children),
}))
vi.mock("../../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../../lib/api")>(
    "../../../../lib/api",
  )
  return {
    ...actual,
    roadmapDocApi: { get: (...a: unknown[]) => getMock(...a) },
  }
})

import { RoadmapDocScreen } from "../RoadmapDocScreen"

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("RoadmapDocScreen (roadmapdoc artifact view)", () => {
  it("renders the fetched roadmap in the rmdoc word-doc layout", async () => {
    getMock.mockResolvedValue({
      filename: "H1-2026-Roadmap.pdf",
      content_type: "application/pdf",
      extracted_text:
        "# H1 2026 Roadmap\n\nThree strategic bets this half.\n\n## Bet 1\n\n- Ship self-serve onboarding\n- Affinity model v2",
      uploaded_at: new Date().toISOString(),
      version: 1,
    })

    await act(async () => {
      render(React.createElement(RoadmapDocScreen))
    })

    // The read-only view wrapper drives the tabs/share-hiding CSS.
    expect(
      document.querySelector('[data-art-view="roadmapdoc"]'),
    ).not.toBeNull()
    // Caption + title from the stored doc.
    expect(screen.getByText(/Your roadmap ·/i)).not.toBeNull()
    expect(screen.getByText(/H1-2026-Roadmap/i)).not.toBeNull()
    // Extracted body content is rendered.
    expect(screen.getByText(/Three strategic bets/i)).not.toBeNull()
    expect(screen.getByText(/Ship self-serve onboarding/i)).not.toBeNull()
    // A bullet list item became a rmdoc-row.
    expect(document.querySelector(".rmdoc-row")).not.toBeNull()
  })

  it("shows the empty state when no roadmap is uploaded", async () => {
    getMock.mockResolvedValue(null)

    await act(async () => {
      render(React.createElement(RoadmapDocScreen))
    })

    expect(screen.getByText(/No roadmap uploaded yet/i)).not.toBeNull()
  })

  it("shows an error state when the fetch fails", async () => {
    getMock.mockRejectedValue(new Error("API 500"))

    await act(async () => {
      render(React.createElement(RoadmapDocScreen))
    })

    expect(screen.getByText(/Couldn't load your roadmap/i)).not.toBeNull()
  })
})
