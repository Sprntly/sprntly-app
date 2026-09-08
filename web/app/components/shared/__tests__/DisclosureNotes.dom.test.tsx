// @vitest-environment jsdom
//
// THE CLASS GUARD.
//
// Five recorded instances in this feature share one shape: the backend
// computes a named disclosure and the frontend has to independently know it
// exists to render it, so the default outcome of adding one is that it
// silently goes nowhere. `DisclosureNotes` inverts that — it renders every
// note in `GoalRunPlan["notes"]` by its `text`, with no switch and no
// allowlist on `kind` — so a disclosure added tomorrow reaches a reader the
// day it ships, with no frontend change required.
//
// THE LOAD-BEARING ASSERTION is the one below using a `kind` invented for
// this file and never referenced anywhere else in the app: if that fails,
// the class is not closed, no matter how well `source_scope` itself renders.
import * as React from "react"
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"

import { DisclosureNotes } from "../DisclosureNotes"
import type { GoalDisclosureNote } from "../../../lib/api"

afterEach(cleanup)

describe("the disclosure notes channel", () => {
  it("renders a note whose kind this file has never seen before, by its text", () => {
    // A kind invented for this assertion alone — not "source_scope", not any
    // kind a component elsewhere in the app has a case for. If this needs a
    // frontend change to appear, the class this component exists to close is
    // still open.
    const notes: GoalDisclosureNote[] = [
      { kind: "a_kind_nobody_has_written_a_case_for_yet", text: "A brand new disclosure, never special-cased anywhere." },
    ]
    render(<DisclosureNotes notes={notes} testIdPrefix="t" />)
    expect(
      screen.getByTestId("t-a_kind_nobody_has_written_a_case_for_yet").textContent,
    ).toBe("A brand new disclosure, never special-cased anywhere.")
  })

  it("renders every note in the array, not just the first", () => {
    const notes: GoalDisclosureNote[] = [
      { kind: "one", text: "First disclosure." },
      { kind: "two", text: "Second disclosure." },
      { kind: "three", text: "Third disclosure." },
    ]
    render(<DisclosureNotes notes={notes} testIdPrefix="t" />)
    expect(screen.getByTestId("t-one").textContent).toBe("First disclosure.")
    expect(screen.getByTestId("t-two").textContent).toBe("Second disclosure.")
    expect(screen.getByTestId("t-three").textContent).toBe("Third disclosure.")
  })

  it("renders the backend's text verbatim, reformatting nothing", () => {
    const text = "Exactly this — punctuation, numbers (4) and all, unmodified."
    render(<DisclosureNotes notes={[{ kind: "x", text }]} testIdPrefix="t" />)
    expect(screen.getByTestId("t-x").textContent).toBe(text)
  })

  it("renders nothing for an empty or absent notes array", () => {
    const { container: empty } = render(<DisclosureNotes notes={[]} testIdPrefix="t" />)
    expect(empty.textContent).toBe("")
    cleanup()
    const { container: absent } = render(<DisclosureNotes notes={undefined} testIdPrefix="t" />)
    expect(absent.textContent).toBe("")
  })

  it("hands the caller's className to each rendered note", () => {
    render(
      <DisclosureNotes
        notes={[{ kind: "x", text: "styled" }]}
        className="caller-register"
        testIdPrefix="t"
      />,
    )
    expect(screen.getByTestId("t-x").className).toBe("caller-register")
  })
})
