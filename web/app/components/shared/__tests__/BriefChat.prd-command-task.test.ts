// prdCommandTask — extracting the SPECIFIC task from a "generate a PRD…" chat
// command. A command that names a task ("generate a PRD for dark mode") must
// yield that task so the chat builds the PRD from the user's own words; a
// generic phrasing ("generate a PRD", "…for this week's brief") must yield null
// so the existing top-insight flow keeps handling it. Pure function — node env.
import { describe, expect, it } from "vitest"

import { isPrdCommand, prdCommandTask } from "../BriefChat"

describe("prdCommandTask — task named AFTER 'prd'", () => {
  it("extracts a 'for <task>' topic", () => {
    expect(prdCommandTask("generate a PRD for dark mode on mobile")).toBe(
      "dark mode on mobile",
    )
  })

  it("extracts 'about' / 'on' / 'to' connector topics", () => {
    expect(prdCommandTask("create a prd about billing revamp")).toBe("billing revamp")
    expect(prdCommandTask("write a PRD on offline sync")).toBe("offline sync")
    expect(prdCommandTask("generate a PRD to improve onboarding")).toBe(
      "improve onboarding",
    )
  })

  it("strips courtesy tails and trailing punctuation", () => {
    expect(prdCommandTask("generate a PRD for dark mode, please!")).toBe("dark mode")
    expect(prdCommandTask("draft a prd for CSV export now, thanks")).toBe("CSV export")
  })
})

describe("prdCommandTask — task named BETWEEN the verb and 'prd'", () => {
  it("extracts the topic and strips leading filler", () => {
    expect(prdCommandTask("draft a dark-mode PRD")).toBe("dark-mode")
    expect(prdCommandTask("generate me a detailed billing-revamp prd")).toBe(
      "billing-revamp",
    )
  })
})

describe("prdCommandTask — generic commands stay on the top-insight flow", () => {
  it.each([
    "generate a PRD",
    "generate a prd please",
    "create a new PRD",
    "make me a PRD now",
    "generate a PRD for this",
    "generate a PRD for this week's brief",
    "generate a PRD for the top insight",
    "Generate a PRD for our top product opportunity.",
    "generate a PRD for my biggest priority",
  ])("returns null for %j", (q) => {
    expect(isPrdCommand(q)).toBe(true) // still a PRD command…
    expect(prdCommandTask(q)).toBeNull() // …but with no specific task
  })

  it("returns null for non-PRD-command text even when it names a task", () => {
    expect(prdCommandTask("what should a PRD for dark mode contain?")).toBeNull()
    // tickets commands are never PRD commands
    expect(prdCommandTask("convert this PRD into tickets")).toBeNull()
  })

  it("returns null for doc-import phrasings (they name a document, not a task)", () => {
    expect(prdCommandTask("import this document as a PRD")).toBeNull()
  })
})
