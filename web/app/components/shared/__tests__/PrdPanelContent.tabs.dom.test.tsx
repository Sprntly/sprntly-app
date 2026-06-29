// @vitest-environment jsdom
//
// Regression: the machine/LLM PRD has been removed as a viewable surface. The
// PRD panel must NOT render the old "Human-readable" / "LLM-readable" sub-tabs
// (the machine spec is now produced on demand via "Send to Claude Code", not a
// browsable tab).
import * as React from "react"
import { cleanup, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({ showToast: vi.fn(), openContentPanel: vi.fn() }),
}))
vi.mock("../../../context/ContentContext", () => ({
  useContent: () => ({ content: { prd: null, prdGenerating: false }, setContent: vi.fn() }),
}))
vi.mock("../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme" }),
}))
vi.mock("../../../lib/api", () => ({
  ApiError: class ApiError extends Error {
    status = 404
  },
  prdApi: {
    // The mount effect fetches the latest PRD; an empty payload short-circuits.
    latest: vi.fn().mockResolvedValue({ payload_md: "" }),
    get: vi.fn(),
    update: vi.fn(),
    listVersions: vi.fn(),
    listGenerations: vi.fn(),
    restoreVersion: vi.fn(),
    sendToClaudeCode: vi.fn(),
  },
  designAgentApi: { getByPrd: vi.fn() },
  multiAgentApi: { getQaScenarios: vi.fn() },
}))

import { PrdPanelContent } from "../PrdPanelContent"

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("PrdPanelContent — machine/LLM PRD tab removed", () => {
  it("renders no Human-readable / LLM-readable sub-tabs", async () => {
    render(React.createElement(PrdPanelContent))
    // Let the latest-PRD mount effect settle.
    await waitFor(() => {})
    expect(screen.queryByText(/LLM-readable/i)).toBeNull()
    expect(screen.queryByText(/Human-readable/i)).toBeNull()
    // The old implementation-brief surface is gone too.
    expect(screen.queryByTestId("llm-part-b")).toBeNull()
    expect(screen.queryByTestId("llm-part-b-empty")).toBeNull()
    // The empty PRD pane still renders (panel is intact).
    expect(screen.getByText(/No PRD draft loaded/i)).toBeTruthy()
  })
})
