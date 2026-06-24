// @vitest-environment jsdom
//
// Tests for the PRD "LLM-readable" view. This view renders the REAL Part B
// (the implementation-spec markdown the backend stores in `llm_part`) faithfully
// via the shared markdown renderer — EARS requirements, design/contracts,
// dependency-ordered tasks, acceptance tests, Definition of Done, verification
// report. It must NOT reconstruct a summary from Part A's parsed sections, and
// it must NOT fabricate content when Part B is absent: an empty `llm_part`
// shows an honest empty line instead.
import * as React from "react"
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

// Module has top-level JSX; expose global React before it evaluates.
vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({ showToast: vi.fn() }),
}))

import { LlmReadableView } from "../PrdPanelContent"

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const view = (prd: any, extra?: { generating?: boolean; loading?: boolean }) =>
  render(React.createElement(LlmReadableView, { prd, ...extra }))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("LlmReadableView — renders the real Part B (llm_part)", () => {
  it("shows a generating state, not an empty prompt, while a PRD is generating", () => {
    view(null, { generating: true })
    expect(screen.getByTestId("llm-generating")).toBeTruthy()
    expect(screen.queryByText(/No PRD yet/i)).toBeNull()
  })

  it("shows the empty state when there is no PRD and nothing in flight", () => {
    view(null)
    expect(screen.getByText(/No PRD yet/i)).toBeTruthy()
  })

  it("renders the real Part B markdown (EARS requirement + Tasks heading), not a reconstructed summary", () => {
    const llmPart = [
      "# Implementation Spec: Team Folders",
      "",
      "## Requirements",
      "",
      "- WHEN a folder is shared THE SYSTEM SHALL make it visible to all team members",
      "",
      "## Tasks",
      "",
      "1. Add a folders table behind a feature flag",
      "2. Wire the share endpoint",
    ].join("\n")
    const prd = {
      prd_id: 1,
      title: "Team Folders",
      metaLine: "From Brief · insight 0",
      // Part A sections are present but must NOT drive the LLM view.
      sections: [
        { type: "prd-tldr", problem: "PMs lose track of folders.", fix: "Add shared team folders.", impact: "+8% retention" },
      ],
      llmPart,
    }
    view(prd)
    // The real Part B content renders…
    expect(screen.getByTestId("llm-part-b")).toBeTruthy()
    expect(screen.getByText(/WHEN a folder is shared THE SYSTEM SHALL/i)).toBeTruthy()
    expect(screen.getByText(/^Tasks$/i)).toBeTruthy()
    expect(screen.getByText(/Add a folders table behind a feature flag/i)).toBeTruthy()
    // …and the empty state is NOT shown.
    expect(screen.queryByTestId("llm-part-b-empty")).toBeNull()
    // The Part A tldr fix must NOT be reconstructed into a FEATURE blurb here.
    expect(screen.queryByText(/^FEATURE$/)).toBeNull()
  })

  it("shows an honest empty line when llm_part is absent/empty (Part B not generated)", () => {
    const prd = { prd_id: 2, title: "Bare PRD", metaLine: "m", sections: [], llmPart: "" }
    view(prd)
    expect(screen.getByTestId("llm-part-b-empty")).toBeTruthy()
    expect(screen.getByText(/No implementation spec yet/i)).toBeTruthy()
    expect(screen.queryByTestId("llm-part-b")).toBeNull()
  })

  it("does not crash and shows the empty line when llmPart is undefined entirely", () => {
    const prd = { prd_id: 3, title: "No Part B", metaLine: "m", sections: [] }
    view(prd)
    expect(screen.getByTestId("llm-part-b-empty")).toBeTruthy()
  })
})
