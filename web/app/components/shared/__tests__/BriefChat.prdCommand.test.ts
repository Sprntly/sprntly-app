// The chat command rules: which phrasings are PRD COMMANDS (open the PRD tab,
// generate) vs questions for the ask agent. The broadened tier-1 rules exist
// because "Give me a prd for …" — a real user prompt — silently fell through
// the old generate/create/write/draft-only verb list to a plain text answer.
import { describe, expect, it } from "vitest"

import { isPrdCommand, isPrdEditCommand, isTicketsCommand, mentionsPrd, prdCommandTask } from "../BriefChat"

// The exact prompt from the user report (issue c): rich requirements, "Give
// me a prd for …" phrasing, no verb from the old list.
const MACHINE_PO_PROMPT =
  "Give me a prd for the Machine Purchase Order project with these requirements: " +
  "Customer submits contract → deal created in Fraznet → rejected (bundle ineligible). " +
  "HubSpot triggers email with URL containing deal information (not just an ID). " +
  "URL opens Sales Order Portal cart, no login, prefilled: name, address, contact info, " +
  "email, machine count, brand, shipping. Nothing editable in v1 — pure display/confirm. " +
  "Brand locked, pulled from deal. No distributor field. PO number — still open: hide or " +
  "leave, no functional impact either way. Checkout via existing EBIZ flow → creates " +
  "machine purchase job/order. On change in HubSpot (not on checkout) → bundle deal " +
  "converts to machine purchase deal: pipeline + deal type change."

describe("isPrdCommand — broadened command phrasings", () => {
  it.each([
    MACHINE_PO_PROMPT,
    "generate a PRD for dark mode on mobile", // the original verb list still works
    "Give me a prd for magic-link sign-in",
    "I need a PRD covering the billing revamp",
    "we want a prd for offline mode",
    "can you put together a product requirements doc for checkout",
    "could you build me a quick PRD about offline mode",
    "prepare a requirements document for the referral program",
    "PRD for the checkout revamp", // noun-first, no verb at all
    "a PRD on usage-based pricing",
    "generate a PRD", // generic — still a command (topic resolved downstream)
    "import this document as a PRD",
  ])("treats %j as a command", (q) => {
    expect(isPrdCommand(q)).toBe(true)
  })

  it.each([
    "what is a PRD?", // information question
    "How do I write a good PRD?", // contains a command verb, but interrogative
    "should we have a prd for this?", // aux question about process
    "does the PRD cover mobile?",
    "the PRD for dark mode is missing metrics", // statement about an EXISTING PRD
    "summarize the requirements doc I uploaded",
    "what's our churn?", // no PRD mention at all
  ])("does NOT treat %j as a command", (q) => {
    expect(isPrdCommand(q)).toBe(false)
  })

  it("tickets phrasings win over PRD phrasings in every dispatcher order", () => {
    const q = "convert this PRD into tickets"
    expect(isTicketsCommand(q)).toBe(true)
    expect(isPrdCommand(q)).toBe(false)
  })

  it("polite 'can you …' phrasings are commands, not vetoed as questions", () => {
    expect(isPrdCommand("can you draft a PRD for checkout?")).toBe(true)
  })
})

// Apurva's canonical command list (2026-07-23): every one of these MUST route
// as a PRD command AND extract the topic that follows, on both frontend and
// backend (see backend/tests/test_qa_router_evals.py for the router half).
const CANONICAL_PRD_COMMANDS = [
  "generate a PRD for",
  "make a PRD for",
  "make me a PRD for",
  "create a PRD for",
  "write a PRD for",
  "draft a PRD for",
  "build a PRD for",
  "put together a PRD for",
  "have it make a PRD for",
  "generate a product requirements document for",
  "create a product requirements document for",
  "write a product requirements document for",
  "draft a product requirements document for",
  "make a product requirements document for",
  "write a product brief for",
  "create a product brief for",
  "generate a product brief for",
  "draft a product brief for",
  "make a product brief for",
  "generate a product brief based on",
  "write a product spec for",
  "create a product spec for",
  "generate a product spec for",
  "draft a product spec for",
  "make a product spec for",
  "write a product specification for",
  "create a product specification for",
  "generate a product specification for",
  "spec this out for",
  "spec it out for",
]

describe("isPrdCommand + prdCommandTask — the canonical command list", () => {
  it.each(CANONICAL_PRD_COMMANDS)(
    "'%s the checkout revamp' is a command and extracts the topic",
    (prefix) => {
      const q = `${prefix} the checkout revamp`
      expect(isPrdCommand(q)).toBe(true)
      expect(prdCommandTask(q)).toBe("checkout revamp")
    },
  )

  it("bare 'spec this out' is a generic command (topic comes from the conversation)", () => {
    expect(isPrdCommand("spec this out")).toBe(true)
    expect(prdCommandTask("spec this out")).toBeNull()
  })
})

describe("mentionsPrd — the LLM-fallback gate", () => {
  it("is broader than isPrdCommand: any PRD-ish noun qualifies", () => {
    expect(mentionsPrd("let's get a PRD going for the checkout revamp")).toBe(true)
    expect(mentionsPrd("the requirements doc needs another pass")).toBe(true)
    expect(mentionsPrd("what's our churn?")).toBe(false)
  })
})

describe("prdCommandTask — task extraction over the broadened phrasings", () => {
  it("keeps the FULL requirement details from the Machine Purchase Order prompt", () => {
    const task = prdCommandTask(MACHINE_PO_PROMPT)
    expect(task).toBeTruthy()
    expect(task).toContain("Machine Purchase Order project")
    // The requirement details must ride along verbatim — they are the PRD's source.
    expect(task).toContain("EBIZ")
    expect(task).toContain("bundle deal converts to machine purchase deal")
    // The command boilerplate is stripped.
    expect(task!.toLowerCase().startsWith("give me")).toBe(false)
  })

  it.each([
    ["generate a PRD for dark mode on mobile", "dark mode on mobile"],
    ["Give me a prd for magic-link sign-in", "magic-link sign-in"],
    ["I need a PRD covering the billing revamp", "billing revamp"],
    ["can you put together a product requirements doc for checkout", "checkout"],
    ["PRD for the checkout revamp", "checkout revamp"],
  ])("extracts %j → %j", (q, task) => {
    expect(prdCommandTask(q)).toBe(task)
  })

  it("returns null for generic commands (topic resolved from the conversation)", () => {
    expect(prdCommandTask("generate a PRD")).toBeNull()
    expect(prdCommandTask("give me a prd")).toBeNull()
  })

  it("returns null for deictic topics that point at the brief, not a task", () => {
    expect(prdCommandTask("generate a PRD for the top insight")).toBeNull()
    expect(prdCommandTask("generate a PRD for this")).toBeNull()
  })
})

describe("isPrdEditCommand — edit phrasings aimed at an existing PRD", () => {
  it.each([
    "make this PRD shorter",
    "make that PRD more concise",
    "make the current PRD two pages",
    "add SSO requirements to the PRD",
    "add a rollout section to the prd",
    "update the PRD to include usage metrics",
    "shorten the product spec",
    "rewrite the prd's goal section",
    "remove the appendix from the PRD",
    "can you tighten the PRD",
  ])("treats %j as an edit", (q) => {
    expect(isPrdEditCommand(q)).toBe(true)
  })

  it.each([
    "make a PRD for dark mode", // indefinite article = CREATE, not edit
    "make me a new prd for checkout",
    "generate a PRD for dark mode",
    "does this PRD cover mobile?", // question, not an edit
    "what should we change in the PRD?",
    "make this ticket shorter", // no PRD noun — ticket deictic stays an ask
    "create tickets from this PRD", // tickets phrasing wins
    "shorten it", // no PRD noun — precision over recall
  ])("does NOT treat %j as an edit", (q) => {
    expect(isPrdEditCommand(q)).toBe(false)
  })

  it("creation phrasings stay commands even when they'd also verb-match edit", () => {
    // The ChatScreen dispatcher checks edit BEFORE command on a PRD tab; these
    // must fall through to the command branch (isPrdCommand) untouched.
    expect(isPrdEditCommand("make a PRD for dark mode")).toBe(false)
    expect(isPrdCommand("make a PRD for dark mode")).toBe(true)
  })
})
