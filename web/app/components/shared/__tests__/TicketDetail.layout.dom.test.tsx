import { describe, expect, it } from "vitest"
import {
  descLayoutOf,
  parseDescBlocks,
  storyToEditableText,
} from "../TicketDetail"

// The fourth of the four mirrors. `storyToEditableText` here must produce
// exactly what backend `sync.story_editable_text` produces for the same story —
// if they disagree about a label, the editor parses text it can't recognise and
// the tracker sync sees a permanent phantom diff.

const BASE = {
  id: "t1",
  title: "Ship it",
  body: "As a user, I want X, so that Y.",
  what: "Build the thing",
  why_now: "Churn is up 12%",
  user_story: "As a user, I want X, so that Y.",
  scope: ["cover A", "cover B"],
  out_of_scope: "Not the mobile app",
} as never

const CUSTOM_LAYOUT = [
  { label: "Summary", source: "what" },
  { label: "Acceptance owner", source: "custom:acceptance_owner" },
  { label: "The ask", source: "user_story" },
  { label: "Covers", source: "scope" },
]

describe("ticket description layout", () => {
  it("falls back to the default layout for a ticket with no format", () => {
    expect(descLayoutOf(BASE).map((e) => e.label)).toEqual([
      "What",
      "Why now",
      "User story",
      "The ticket must cover",
      "Out of scope",
    ])
  })

  it("serializes the default layout byte-identically to the legacy form", () => {
    // Mirrors backend sync.story_editable_text under DEFAULT_LAYOUT, including
    // the `scope` -> "The ticket must cover" rename.
    expect(storyToEditableText(BASE)).toBe(
      "What\nBuild the thing\n\n" +
        "Why now\nChurn is up 12%\n\n" +
        "User story\nAs a user, I want X, so that Y.\n\n" +
        "The ticket must cover\n- cover A\n- cover B\n\n" +
        "Out of scope\nNot the mobile app",
    )
  })

  it("serializes a custom layout in the company's own labels and order", () => {
    const story = {
      ...(BASE as object),
      description_layout: CUSTOM_LAYOUT,
      custom_sections: { acceptance_owner: "QA lead" },
    } as never
    expect(storyToEditableText(story)).toBe(
      "Summary\nBuild the thing\n\n" +
        "Acceptance owner\nQA lead\n\n" +
        "The ask\nAs a user, I want X, so that Y.\n\n" +
        "Covers\n- cover A\n- cover B",
    )
  })

  it("parses edited text back using the ticket's own labels", () => {
    // Without the ticket's labels, "Summary" reads as body text and the
    // section loses its heading the moment the user edits it.
    const text = "Summary\nBuild the thing\n\nCovers\n- cover A"
    const labels = CUSTOM_LAYOUT.map((e) => e.label)
    expect(parseDescBlocks(text, labels).map((b) => b.label)).toEqual([
      "Summary",
      "Covers",
    ])
    // With the default labels it collapses to one unlabelled block.
    expect(parseDescBlocks(text).map((b) => b.label)).toEqual([null])
  })

  it("keeps an empty custom section's label but still skips empty canonical ones", () => {
    // House keep-empty-fields rule: the format says a ticket HAS that section,
    // so a reader must see it is blank rather than wonder if it was dropped.
    const story = {
      ...(BASE as object),
      why_now: "",
      description_layout: CUSTOM_LAYOUT,
      custom_sections: {},
    } as never
    const layout = descLayoutOf(story)
    expect(layout.map((e) => e.label)).toContain("Acceptance owner")
    // storyToEditableText is the EDIT form and still omits empties (it is
    // compared byte-for-byte against the backend); the display renderer is what
    // keeps the label — asserted via the layout it renders from.
    expect(storyToEditableText(story)).not.toContain("Acceptance owner")
  })
})
