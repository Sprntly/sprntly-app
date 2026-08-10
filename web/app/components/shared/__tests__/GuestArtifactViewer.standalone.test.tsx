// @vitest-environment jsdom
//
// GuestArtifactViewer's evidence arm: a standalone evidence document (no
// sibling PRD) rendered from `artifactType="evidence"`. Reuses the exact
// mock harness from GuestArtifactViewer.test.tsx (Group A), plus proves the
// PRD arm stays byte-behaviourally unchanged and the conversation-history
// fetch dispatches to conversationsApi.byEvidence for the evidence arm
// (never byPrd — a content-integrity guard, not cosmetic).
import * as React from "react"
import { cleanup, render, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

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
vi.mock("../JoinWorkspaceBanner", () => ({ JoinWorkspaceBanner: () => null }))
vi.mock("../JoinConfirmModal", () => ({ JoinConfirmModal: () => null }))

const apiCallsMock = vi.hoisted(() => ({
  prdGet: vi.fn(),
  evidenceGet: vi.fn(),
  storiesGetJob: vi.fn(),
  conversationsByPrd: vi.fn(),
  conversationsByEvidence: vi.fn(),
}))
vi.mock("../../../lib/api", () => ({
  prdApi: { get: apiCallsMock.prdGet },
  evidenceApi: { get: apiCallsMock.evidenceGet },
  storiesApi: { getJob: apiCallsMock.storiesGetJob },
  conversationsApi: {
    byPrd: apiCallsMock.conversationsByPrd,
    byEvidence: apiCallsMock.conversationsByEvidence,
  },
}))

const contentApiMock = vi.fn()
vi.mock("../../../lib/artifactShareApi", () => ({
  artifactShareApi: { content: (...a: unknown[]) => contentApiMock(...a) },
}))

const prdAccessContentMock = vi.fn()
vi.mock("../../../lib/prdAccessApi", () => ({
  prdAccessApi: { content: (...a: unknown[]) => prdAccessContentMock(...a) },
}))

import { GuestArtifactViewer } from "../GuestArtifactViewer"

const PRD_CONTENT_RESPONSE = {
  artifact_type: "prd" as const,
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
  tickets: null,
}

const EVIDENCE_CONTENT_RESPONSE = {
  artifact_type: "evidence" as const,
  evidence: {
    id: 501,
    brief_id: 9,
    insight_index: 2,
    generated_at: "2026-07-01T00:00:00Z",
    title: "Retention research",
    payload_md: "# Retention research\n\nEvidence body text.",
    status: "ready",
    question: "Why did retention drop?",
  },
}

beforeEach(() => {
  apiCallsMock.conversationsByPrd.mockResolvedValue({ conversation: null, turns: [] })
  apiCallsMock.conversationsByEvidence.mockResolvedValue({ conversation: null, turns: [] })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("GuestArtifactViewer — standalone evidence arm", () => {
  it("test_evidence_arm_renders_evidence_without_a_prd — AC10", async () => {
    contentApiMock.mockResolvedValue(EVIDENCE_CONTENT_RESPONSE)
    render(
      <GuestArtifactViewer
        token="tok-evidence-1"
        publicId={null}
        artifactId={501}
        artifactType="evidence"
        sharerName="Priya Shah"
        owningCompanyName="Acme Co"
      />,
    )

    await waitFor(() => {
      expect(contentMock).toHaveBeenCalled()
      const lastCall = contentMock.mock.calls[contentMock.mock.calls.length - 1]
      const [content, contentPanelTab] = lastCall
      expect(content.prd).toBeNull()
      expect(content.evidence).not.toBeNull()
      expect(content.evidence.html ?? content.evidence.sections).toBeDefined()
      expect(contentPanelTab).toBe("evidence")
    })
    expect(apiCallsMock.prdGet).not.toHaveBeenCalled()
    expect(apiCallsMock.evidenceGet).not.toHaveBeenCalled()
    expect(contentApiMock).toHaveBeenCalledTimes(1)
    expect(contentApiMock).toHaveBeenCalledWith("tok-evidence-1")
  })

  it("test_evidence_arm_fetches_history_via_byEvidence_not_byPrd — AC10 (mutation-sensitive)", async () => {
    contentApiMock.mockResolvedValue(EVIDENCE_CONTENT_RESPONSE)
    render(
      <GuestArtifactViewer
        token="tok-evidence-1"
        publicId={null}
        artifactId={501}
        artifactType="evidence"
        sharerName="Priya Shah"
        owningCompanyName="Acme Co"
      />,
    )

    await waitFor(() => {
      expect(apiCallsMock.conversationsByEvidence).toHaveBeenCalledTimes(1)
    })
    expect(apiCallsMock.conversationsByEvidence).toHaveBeenCalledWith(501)
    expect(apiCallsMock.conversationsByPrd).not.toHaveBeenCalled()
  })

  it("test_prd_arm_unchanged — AC11 (regression guard)", async () => {
    contentApiMock.mockResolvedValue(PRD_CONTENT_RESPONSE)
    render(
      <GuestArtifactViewer
        token="tok-prd-1"
        publicId={null}
        artifactId={482}
        artifactType="prd"
        sharerName="Priya Shah"
        owningCompanyName="Acme Co"
      />,
    )

    await waitFor(() => {
      expect(contentMock).toHaveBeenCalled()
      const lastCall = contentMock.mock.calls[contentMock.mock.calls.length - 1]
      const [content, contentPanelTab] = lastCall
      expect(content.prd?.prd_id).toBe(482)
      expect(contentPanelTab).toBe("prd")
    })
    await waitFor(() => {
      expect(apiCallsMock.conversationsByPrd).toHaveBeenCalledWith(482)
    })
    expect(apiCallsMock.conversationsByEvidence).not.toHaveBeenCalled()
  })

  it("defaults to the prd arm when artifactType is omitted (pre-existing call sites)", async () => {
    contentApiMock.mockResolvedValue(PRD_CONTENT_RESPONSE)
    render(
      <GuestArtifactViewer
        token="tok-prd-2"
        publicId={null}
        artifactId={482}
        sharerName="Priya Shah"
        owningCompanyName="Acme Co"
      />,
    )

    await waitFor(() => {
      expect(apiCallsMock.conversationsByPrd).toHaveBeenCalledWith(482)
    })
    expect(apiCallsMock.conversationsByEvidence).not.toHaveBeenCalled()
  })
})
