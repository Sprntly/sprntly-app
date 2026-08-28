// @vitest-environment jsdom
//
// The panel header must name what the panel is SHOWING.
//
// Three tabs have now shipped this bug. Reports fixed it, Document fixed it
// again with a comment explaining why, and Goal Analysis still fell through to
// the literal "PRD" — found on staging, with the tab beside it correctly
// reading "Goal Analysis". So this test is written over the LADDER rather than
// over one tab: a new thread-level tab that forgets to extend it fails here.
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

import { readFileSync } from "node:fs"
import { join } from "node:path"

afterEach(cleanup)

const PANEL = readFileSync(
  join(__dirname, "..", "ContentPanel.tsx"), "utf8",
)

describe("the panel header names the active tab", () => {
  it("every thread-level tab has its own name in the ladder", () => {
    // Thread-level tabs are the ones that are NOT part of the PRD pipeline —
    // exactly the set that must not be titled "PRD". Kept in sync with
    // `panelPrdScope.ts`, which lists the same tabs for the same reason.
    for (const tab of ["reports", "document", "goal"]) {
      expect(
        PANEL.includes(`activeTab === "${tab}"`),
        `panel header has no branch for the "${tab}" tab, so it falls through `
          + `to "PRD" — the bug Reports and Document each had to fix`,
      ).toBe(true)
    }
  })

  it("names Goal Analysis, not PRD", () => {
    const i = PANEL.indexOf('activeTab === "goal"')
    expect(i).toBeGreaterThan(-1)
    expect(PANEL.slice(i, i + 120)).toContain("Goal Analysis")
  })

  it("the scope rule and the header agree on which tabs are thread-level", () => {
    // They drifted once already: `goal` was added to the scope rule and not to
    // the header, which is precisely how the wrong title shipped.
    const scope = readFileSync(
      join(__dirname, "..", "..", "..", "lib", "panelPrdScope.ts"), "utf8",
    )
    for (const tab of ["reports", "document", "goal"]) {
      expect(scope.includes(`"${tab}"`), `${tab} missing from panelPrdScope`).toBe(true)
    }
  })
})
