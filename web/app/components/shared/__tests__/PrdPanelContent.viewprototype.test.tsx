// @vitest-environment jsdom
//
// The PRD panel footer's next-pipeline-step button is TICKETS: it reads "Create
// tickets" until the PRD has been broken into stories, then "View tickets", and
// clicking it opens the Tickets tab. The prototype affordance MOVED OFF this
// footer to the Tickets tab's own bottom bar (ContentPanel), so the PRD footer no
// longer mounts GeneratePrototypeCTA. "Send to Claude Code" remains removed from
// this surface (it lives with the Tickets flow).
import * as React from "react"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { readFileSync } from "node:fs"
import { resolve } from "node:path"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

const mocks = vi.hoisted(() => ({
  showToast: vi.fn(),
  setContent: vi.fn(),
  openContentPanel: vi.fn(),
}))

let content: Record<string, unknown>

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
}))

vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({ showToast: mocks.showToast, openContentPanel: mocks.openContentPanel }),
}))

vi.mock("../../../context/ContentContext", () => ({
  useContent: () => ({ content, setContent: mocks.setContent }),
}))

vi.mock("../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme" }),
}))

vi.mock("../../../lib/api", () => {
  class ApiError extends Error {
    status: number
    constructor(status: number, message: string) {
      super(message)
      this.status = status
    }
  }
  return {
    ApiError,
    designAgentApi: {
      listPendingPatches: vi.fn(async () => []),
    },
    multiAgentApi: {
      getQaScenarios: vi.fn(async () => ({ doc: null })),
    },
    // The footer's "Create tickets" ↔ "View tickets" label reads this.
    storiesApi: {
      getForPrd: vi.fn(async () => ({ status: "none", fresh: false, stories: [] })),
    },
    prdApi: {
      latest: vi.fn(async () => { throw new ApiError(404, "none") }),
      update: vi.fn(async () => ({})),
      listVersions: vi.fn(async () => []),
      listGenerations: vi.fn(async () => []),
    },
  }
})

import { PrdPanelContent } from "../PrdPanelContent"

const PRD = {
  prd_id: 42,
  title: "Retention PRD",
  metaLine: "From Brief",
  sections: [{ type: "p", text: "Improve retention." }],
  figma_file_key: "fig-file",
}

beforeEach(() => {
  vi.clearAllMocks()
  content = { prd: PRD, prdGenerating: false }
})

afterEach(cleanup)

describe("PrdPanelContent — footer tickets CTA (prototype moved to the Tickets tab)", () => {
  it("mounts the Create/View tickets button in the footer, and NOT the prototype CTA", () => {
    const { container } = render(<PrdPanelContent />)
    const btn = screen.getByTestId("prd-footer-tickets-cta")
    // Defaults to "Create tickets" (the PRD has no stories yet).
    expect(btn.textContent).toContain("Create tickets")
    expect(btn.closest(".prd-footer-bar")).not.toBeNull()
    expect(container.querySelector(".prd-bottom-bar")).not.toBeNull()
    // Prototype generation no longer lives in the PRD footer.
    expect(screen.queryByTestId("prd-footer-prototype-cta")).toBeNull()
  })

  it("clicking the tickets button opens the Tickets tab", () => {
    render(<PrdPanelContent />)
    fireEvent.click(screen.getByTestId("prd-footer-tickets-cta"))
    expect(mocks.openContentPanel).toHaveBeenCalledWith("tickets")
  })

  it("renders no footer CTA at all when no PRD is loaded (empty panel)", () => {
    content = { prd: null, prdGenerating: false }
    render(<PrdPanelContent />)
    expect(screen.queryByTestId("prd-footer-tickets-cta")).toBeNull()
    expect(screen.queryByTestId("prd-footer-prototype-cta")).toBeNull()
  })

  it("still renders no Send to Claude Code button in the PRD panel", () => {
    render(<PrdPanelContent />)
    expect(screen.queryByTestId("prd-send-claude")).toBeNull()
    expect(screen.queryByText(/Send to Claude Code/i)).toBeNull()
  })

  it("the PRD footer no longer hosts the prototype CTA (it moved to the Tickets tab)", () => {
    const src = readFileSync(
      resolve(process.cwd(), "app/components/shared/PrdPanelContent.tsx"),
      "utf8",
    )
    // Prototype generation moved off the PRD footer onto the Tickets tab's bar.
    expect(src).not.toContain("GeneratePrototypeCTA")
    expect(src).toContain("prd-footer-tickets-cta")
    expect(src).not.toContain("SendToClaudeCode")
    expect(src).not.toContain("ViewPrototypeButton")
  })
})
