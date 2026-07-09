// @vitest-environment jsdom
//
// The PRD footer's "View/Generate Prototype" and "Send to Claude Code" actions
// were removed from PrdPanelContent: the prototype CTA already lives on the chat
// page, and Send to Claude Code will return as part of the Tickets surface. This
// file is the regression guard that the PRD panel no longer renders either of
// those footer actions (the SendToClaudeCode component itself still exists and is
// covered by SendToClaudeCode.dom.test.tsx — it is simply no longer mounted here).
import * as React from "react"
import { cleanup, render, screen } from "@testing-library/react"
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
}))

let content: Record<string, unknown>

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
}))

vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({ showToast: mocks.showToast }),
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

describe("PrdPanelContent — prototype + Send to Claude Code footer removed", () => {
  it("renders no View/Generate Prototype button in the PRD panel", () => {
    const { container } = render(<PrdPanelContent />)
    expect(container.querySelector(".prd-bottom-actions")).toBeNull()
    expect(screen.queryByRole("button", { name: "View Prototype" })).toBeNull()
    expect(screen.queryByRole("button", { name: "Generate Prototype" })).toBeNull()
    expect(container.querySelector(".fc-btn-secondary")).toBeNull()
  })

  it("renders no Send to Claude Code button in the PRD panel", () => {
    render(<PrdPanelContent />)
    expect(screen.queryByTestId("prd-send-claude")).toBeNull()
    expect(screen.queryByText(/Send to Claude Code/i)).toBeNull()
  })

  it("no longer imports the prototype CTA or Send to Claude Code into this surface", () => {
    const src = readFileSync(
      resolve(process.cwd(), "app/components/shared/PrdPanelContent.tsx"),
      "utf8",
    )
    expect(src).not.toContain("GeneratePrototypeCTA")
    expect(src).not.toContain("SendToClaudeCode")
    expect(src).not.toContain("ViewPrototypeButton")
    expect(src).not.toMatch(/pid=/)
  })
})
