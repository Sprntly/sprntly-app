// @vitest-environment jsdom
//
// GuestArtifactViewer mounts its OWN fresh NavigationProvider + ContentProvider
// (the real ones — confirmed zero-dependency on auth/workspace) and populates
// content directly from artifactShareApi.content(token). It must never call
// prdApi.get / evidenceApi.get / storiesApi.getJob (AC10), and must fetch
// exactly once per mount.
import * as React from "react"
import { cleanup, render, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

// The REAL NavigationProvider (mounted by GuestArtifactViewer itself) calls
// useRouter()/usePathname() — mock next/navigation so it doesn't need a real
// Next router context, matching how every other test in this suite avoids it.
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/",
}))

const contentMock = vi.fn()
vi.mock("../ContentPanel", async () => {
  const { useContent } = await import("../../../context/ContentContext")
  const { useNavigation } = await import("../../../context/NavigationContext")
  return {
    ContentPanel: () => {
      const { content } = useContent()
      const { contentPanelTab } = useNavigation()
      contentMock(content, contentPanelTab)
      return <div data-testid="content-probe" />
    },
  }
})
vi.mock("../GuestRail", () => ({ GuestRail: () => <div data-testid="guest-rail" /> }))
vi.mock("../Toast", () => ({ Toast: () => null }))
// Group B's join banner/modal have their own dedicated test files (and their
// own auth-context needs) — mocked here so this file stays focused on Group
// A's standalone-mount/fetch-once concerns.
vi.mock("../JoinWorkspaceBanner", () => ({ JoinWorkspaceBanner: () => null }))
vi.mock("../JoinConfirmModal", () => ({ JoinConfirmModal: () => null }))

const apiCallsMock = vi.hoisted(() => ({
  prdGet: vi.fn(),
  evidenceGet: vi.fn(),
  storiesGetJob: vi.fn(),
}))
vi.mock("../../../lib/api", () => ({
  prdApi: { get: apiCallsMock.prdGet },
  evidenceApi: { get: apiCallsMock.evidenceGet },
  storiesApi: { getJob: apiCallsMock.storiesGetJob },
}))

const contentApiMock = vi.fn()
vi.mock("../../../lib/artifactShareApi", () => ({
  artifactShareApi: { content: (...a: unknown[]) => contentApiMock(...a) },
}))

import { GuestArtifactViewer } from "../GuestArtifactViewer"

const CONTENT_RESPONSE = {
  prd: {
    id: 482,
    brief_id: 9,
    insight_index: 2,
    generated_at: "2026-07-01T00:00:00Z",
    title: "Q3 Retention PRD",
    payload_md: "# Q3 Retention PRD\n\nSome problem statement text.",
    status: "ready",
    source: "brief",
    question: null,
  },
  evidence: null,
  tickets: { stories: [{ title: "Ticket one", body: "b", acceptance_criteria: [], priority: null, route: null }] },
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("GuestArtifactViewer", () => {
  it("test_guest_artifact_viewer_mounts_own_navigation_and_content_providers", () => {
    contentApiMock.mockResolvedValue(CONTENT_RESPONSE)
    // Rendered standalone, with NO ancestor NavigationProvider/ContentProvider
    // in the tree — must not throw "must be used within a Provider".
    expect(() =>
      render(
        <GuestArtifactViewer
          token="tok-1"
          artifactId={482}
          sharerName="Priya Shah"
          owningCompanyName="Acme Co"
        />,
      ),
    ).not.toThrow()
  })

  it("test_guest_artifact_viewer_fetches_content_once_and_never_calls_authed_prd_apis — AC10", async () => {
    contentApiMock.mockResolvedValue(CONTENT_RESPONSE)
    render(
      <GuestArtifactViewer
        token="tok-1"
        artifactId={482}
        sharerName="Priya Shah"
        owningCompanyName="Acme Co"
      />,
    )

    await waitFor(() => {
      expect(contentApiMock).toHaveBeenCalledTimes(1)
    })
    expect(contentApiMock).toHaveBeenCalledWith("tok-1")
    expect(apiCallsMock.prdGet).not.toHaveBeenCalled()
    expect(apiCallsMock.evidenceGet).not.toHaveBeenCalled()
    expect(apiCallsMock.storiesGetJob).not.toHaveBeenCalled()
  })

  it("populates content.prd/prdMeta/guestTickets directly and opens the prd tab", async () => {
    contentApiMock.mockResolvedValue(CONTENT_RESPONSE)
    render(
      <GuestArtifactViewer
        token="tok-1"
        artifactId={482}
        sharerName="Priya Shah"
        owningCompanyName="Acme Co"
      />,
    )

    await waitFor(() => {
      expect(contentMock).toHaveBeenCalled()
      const lastCall = contentMock.mock.calls[contentMock.mock.calls.length - 1]
      const [content, contentPanelTab] = lastCall
      expect(content.prd?.prd_id).toBe(482)
      expect(content.prdMeta).toEqual({ briefId: 9, insightIndex: 2 })
      expect(content.guestTickets).toEqual(CONTENT_RESPONSE.tickets.stories)
      expect(contentPanelTab).toBe("prd")
    })
  })

  it("does not populate content when the fetch fails (best-effort, no throw)", async () => {
    contentApiMock.mockRejectedValue(new Error("network"))
    expect(() =>
      render(
        <GuestArtifactViewer
          token="tok-1"
          artifactId={482}
          sharerName="Priya Shah"
          owningCompanyName="Acme Co"
        />,
      ),
    ).not.toThrow()
    await waitFor(() => {
      expect(contentApiMock).toHaveBeenCalledTimes(1)
    })
  })
})
