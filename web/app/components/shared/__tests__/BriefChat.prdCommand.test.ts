// The chat command rules: which phrasings are PRD COMMANDS (open the PRD tab,
// generate) vs questions for the ask agent. The broadened tier-1 rules exist
// because "Give me a prd for …" — a real user prompt — silently fell through
// the old generate/create/write/draft-only verb list to a plain text answer.
import { describe, expect, it } from "vitest"

import { isPrdCommand, isTicketsCommand, mentionsPrd, prdCommandTask } from "../BriefChat"

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
