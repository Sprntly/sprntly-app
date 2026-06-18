// @vitest-environment jsdom
//
// Regression tests for the PRD "LLM-readable" view's empty/generating states.
// This view used to FABRICATE content when a PRD was missing or had empty
// sections — a generic "guided, inline experience" feature blurb plus hardcoded
// acceptance-criteria ("Completes in under 60 seconds") and definition-of-done
// ("verified in Mixpanel dashboard") lists. That is exactly the "random data"
// the empty-state pass removes: the view must show a real generating/empty state
// and only ever render what the PRD actually contains.
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

describe("LlmReadableView — honest empty/generating states (no fabricated data)", () => {
  it("shows a generating state, not an empty prompt, while a PRD is generating", () => {
    view(null, { generating: true })
    expect(screen.getByTestId("llm-generating")).toBeTruthy()
    expect(screen.queryByText(/No PRD yet/i)).toBeNull()
  })

  it("shows the empty state when there is no PRD and nothing in flight", () => {
    view(null)
    expect(screen.getByText(/No PRD yet/i)).toBeTruthy()
  })

  it("renders ONLY the PRD's real acceptance criteria and DoD — never the old fake lists", () => {
    const prd = {
      prd_id: 1,
      title: "Team Folders",
      metaLine: "From Brief · insight 0",
      sections: [
        { type: "prd-tldr", problem: "PMs lose track of folders.", fix: "Add shared team folders.", impact: "+8% retention" },
        { type: "prd-acceptance-criteria", rows: [{ givenWhenThen: "Given a team, when a folder is shared, then all members see it" }] },
        { type: "prd-dod", items: ["Migration ships behind a flag"] },
      ],
    }
    view(prd)
    // Real content is present…
    expect(screen.getByText(/all members see it/i)).toBeTruthy()
    expect(screen.getByText(/Migration ships behind a flag/i)).toBeTruthy()
    expect(screen.getByText(/Add shared team folders/i)).toBeTruthy() // tldr.fix in FEATURE
    expect(screen.getByText(/PMs lose track of folders/i)).toBeTruthy() // tldr.problem in WHY
    // …and none of the previously-hardcoded fabrications leak through.
    expect(screen.queryByText(/Completes in under 60 seconds/i)).toBeNull()
    expect(screen.queryByText(/Mixpanel/i)).toBeNull()
    expect(screen.queryByText(/guided, inline experience/i)).toBeNull()
  })

  it("shows honest 'not specified' lines instead of inventing AC/DoD when sections are empty", () => {
    const prd = { prd_id: 2, title: "Bare PRD", metaLine: "m", sections: [] }
    view(prd)
    expect(screen.getByText(/No acceptance criteria were specified in this PRD/i)).toBeTruthy()
    expect(screen.getByText(/No definition of done was specified in this PRD/i)).toBeTruthy()
    expect(screen.getByText(/Not specified in this PRD/i)).toBeTruthy() // WHY
    // The fabricated fallback lists must be gone.
    expect(screen.queryByText(/Telemetry: started/i)).toBeNull()
    expect(screen.queryByText(/PM sign-off/i)).toBeNull()
  })
})
