// Ordinary chat questions were generating REAL PRDs (POST
// /v1/prd/generate-from-task). Two frontend detectors were responsible:
//
//   1. AIBar.tsx's private `isPrdCommand` — `/\b(generate|create|write|draft|
//      make)\b.*\bprd\b/i`. Unbounded `.*`, no question guard, no tickets
//      guard, and mounted app-wide. "how do I write a PRD?" matched.
//   2. BriefChat.tsx's `PRD_COMMAND_RE` — same unbounded `.*` where the backend
//      caps the identical rule at 40 characters, with a question guard that
//      only vetoed messages STARTING with a wh-word or `aux + pronoun`.
//
// Both now run these shared rules. This suite is the contract table: what MUST
// fire, and what MUST NOT. Every must-not row below is a phrasing that produced
// a real document before the fix.
import { describe, expect, it } from "vitest"

import {
  isPrdCommand,
  isPrdEditCommand,
  isTicketsCommand,
  mentionsPrd,
  prdCommandTask,
} from "../prd-commands"

// ─────────────────────────────────────────────────────────────────────────────
// MUST FIRE — the genuine command path. A regression here means users lose the
// ability to ask for a PRD in chat.
// ─────────────────────────────────────────────────────────────────────────────
const SHOULD_FIRE: [string, string, string | null][] = [
  // [message, why it is a command, expected extracted task]
  ["generate a PRD for dark mode on mobile", "canonical verb + indefinite noun + topic", "dark mode on mobile"],
  ["write a PRD about usage-based pricing", "verb 'write' + 'about' topic", "usage-based pricing"],
  ["draft a product brief for the referral program", "'product brief' is the same artifact", "referral program"],
  ["create a product requirements document for checkout", "long-form noun", "checkout"],
  ["Give me a prd for magic-link sign-in", "ask-shape verb + INDEFINITE noun", "magic-link sign-in"],
  ["I need a PRD covering the billing revamp", "'need' + indefinite", "billing revamp"],
  ["can you draft a PRD for checkout?", "polite 'can you' + AUTHORING verb is still a command", "checkout"],
  ["could you build me a quick PRD about offline mode", "'could you' + authoring verb", "offline mode"],
  ["put together a quick one-page prd for SSO", "multi-word filler inside the 40-char gap", "SSO"],
  ["PRD for the checkout revamp", "noun-first shape, no verb at all", "checkout revamp"],
  ["a PRD on usage-based pricing", "noun-first with an indefinite article", "usage-based pricing"],
  ["spec this out", "deictic command, no artifact noun", null],
  ["spec this out for the notifications rebuild", "deictic command WITH a topic", "notifications rebuild"],
  ["generate a PRD", "generic command — still a command, topic resolved by the caller", null],
  ["have it make a PRD for the checkout revamp", "imperative 'have it …', not a question", "checkout revamp"],
  [
    "generate a PRD for billing based on the requirements doc from legal",
    "the FIRST noun is indefinite; a later definite reference must not veto it",
    "billing based on the requirements doc from legal",
  ],
]

describe("isPrdCommand — must fire", () => {
  it.each(SHOULD_FIRE)("%j is a command (%s)", (q) => {
    expect(isPrdCommand(q)).toBe(true)
  })

  it.each(SHOULD_FIRE.filter(([, , task]) => task !== null))(
    "%j extracts its task (%s)",
    (q, _why, task) => {
      expect(prdCommandTask(q)).toBe(task)
    },
  )

  it.each(SHOULD_FIRE.filter(([, , task]) => task === null))(
    "%j is generic — no task extracted (%s)",
    (q) => {
      expect(prdCommandTask(q)).toBeNull()
    },
  )
})

// ─────────────────────────────────────────────────────────────────────────────
// MUST NOT FIRE — every one of these generated a real PRD before the fix.
// ─────────────────────────────────────────────────────────────────────────────
const MUST_NOT_FIRE: [string, string][] = [
  // ── The reported symptom: an information question about PRDs ──────────────
  ["how do I write a PRD?", "AIBar's regex matched 'write' + 'prd' and generated from brief.insights[0]"],
  ["How do I write a good PRD?", "same, with an adjective in the gap"],
  ["what is a PRD?", "definitional question"],
  ["what makes a good product requirements document?", "definitional question, long-form noun"],
  ["why do we write PRDs at all?", "process question"],
  ["should we have a prd for this?", "aux + 'we' question"],
  ["when do you usually draft a PRD?", "wh-question with an authoring verb"],
  ["hey, what's in the PRD for billing?", "wh-question behind a conversational lead-in"],
  ["so how would you write a PRD for this kind of thing?", "lead-in + wh-question"],

  // ── The three false positives quoted in the bug report ────────────────────
  ["Can you give me the PRD for billing?", "a request to VIEW an existing PRD — 'the', not 'a'"],
  ["let's make sure the product specs are updated", "'make' + 'product specs' with only 'sure the' between them"],
  ["I need the requirements doc from legal", "'need' + a DEFINITE bare requirements doc"],

  // ── Reference to an existing document (backend: referencing is `answer`) ──
  ["the PRD for dark mode is missing metrics", "statement about an existing PRD"],
  ["can you send me the product spec?", "retrieval verb + definite noun"],
  ["show me the PRD we wrote for onboarding", "retrieval verb"],
  ["pull up the requirements document for the API", "retrieval phrase"],
  ["where is the product requirements doc?", "wh-question + definite noun"],
  ["is the product spec approved yet?", "aux + 'the' question"],
  ["review our PRD for gaps", "possessive determiner"],
  ["Alex's PRD needs another pass", "possessive-'s determiner"],
  ["that half-finished product spec still has no metrics", "demonstrative + two adjectives"],

  // ── Past tense: reporting a PRD exists, not asking for one ────────────────
  ["I wrote a PRD for billing last week", "past-tense authoring verb"],
  ["we drafted a product brief for onboarding already", "past-tense authoring verb"],
  ["Jose created a PRD for the mobile app in Q2", "past-tense authoring verb"],

  // ── Long-range verb/noun pairs the unbounded `.*` used to bridge ──────────
  [
    "draft the launch email once you've read through everything the team put in the PRD",
    "verb and noun are in different clauses, far past the backend's 40-char cap",
  ],
  [
    "make the dashboard load faster, and while you're at it check the numbers in the product spec",
    "'make' reaches a PRD noun 60+ characters away",
  ],

  // ── Tickets phrasings win in every dispatcher ─────────────────────────────
  ["convert this PRD into tickets", "tickets guard — AIBar had none at all"],
  ["break the PRD into tickets for the team", "tickets guard"],

  // ── No PRD mention at all ────────────────────────────────────────────────
  ["what's our churn?", "no artifact noun"],
  ["make sure product specs are updated", "'make sure' is a status nudge, not an authoring verb"],
]

describe("isPrdCommand — must NOT fire", () => {
  it.each(MUST_NOT_FIRE)("%j is not a command (%s)", (q) => {
    expect(isPrdCommand(q)).toBe(false)
  })

  it.each(MUST_NOT_FIRE)("%j extracts no task either (%s)", (q) => {
    expect(prdCommandTask(q)).toBeNull()
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// The gap bound, stated as its own contract so it can't silently regress to an
// unbounded `.*` again.
// ─────────────────────────────────────────────────────────────────────────────
describe("the verb→noun gap is bounded at 40 characters (mirrors backend/app/skill_router.py _RULES)", () => {
  const filler = (n: number) => "x".repeat(n)

  it("fires when the gap is inside the cap", () => {
    expect(isPrdCommand(`generate ${filler(30)} a PRD for checkout`)).toBe(true)
  })

  it("does NOT fire once the gap exceeds the cap", () => {
    expect(isPrdCommand(`generate ${filler(80)} a PRD for checkout`)).toBe(false)
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// Guards that must NOT have been widened by this change.
// ─────────────────────────────────────────────────────────────────────────────
describe("adjacent rules are unchanged", () => {
  it("mentionsPrd stays broad — it only gates the LLM fallback tier, never fires generation", () => {
    // Deliberately true for messages isPrdCommand rejects: the classifier gets
    // to look at them, the regex tier does not act on them.
    expect(mentionsPrd("Can you give me the PRD for billing?")).toBe(true)
    expect(mentionsPrd("the requirements doc needs another pass")).toBe(true)
    expect(isPrdCommand("Can you give me the PRD for billing?")).toBe(false)
    expect(mentionsPrd("what's our churn?")).toBe(false)
  })

  it("isPrdEditCommand still targets 'the PRD' — the reference guard is not applied to edits", () => {
    // An edit is SUPPOSED to point at an existing document; applying the
    // isPrdCommand reference guard here would break chat-edit entirely.
    expect(isPrdEditCommand("make this PRD shorter")).toBe(true)
    expect(isPrdEditCommand("add a rollout section to the prd")).toBe(true)
    expect(isPrdEditCommand("can you tighten the PRD")).toBe(true)
    // …but a question about the PRD is still not an edit.
    expect(isPrdEditCommand("does this PRD cover mobile?")).toBe(false)
  })

  it("isTicketsCommand is untouched and still wins over PRD phrasings", () => {
    expect(isTicketsCommand("convert this PRD into tickets")).toBe(true)
    expect(isTicketsCommand("create tickets from this PRD")).toBe(true)
    expect(isPrdCommand("create tickets from this PRD")).toBe(false)
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// Source invariant: neither detector may reintroduce an unbounded gap, and
// AIBar must not grow a private copy of the rules again.
// ─────────────────────────────────────────────────────────────────────────────
describe("source invariants", () => {
  async function readSource(...parts: string[]) {
    const fs = await import("node:fs")
    const path = await import("node:path")
    const { fileURLToPath } = await import("node:url")
    const here = path.dirname(fileURLToPath(import.meta.url))
    return fs.readFileSync(path.join(here, ...parts), "utf8")
  }

  it("AIBar detects NOTHING — every message goes to the planner", async () => {
    // This invariant got stronger. It used to assert that AIBar imported the
    // SHARED rules rather than keeping a private regex, because a private copy
    // drifted from the guards everyone else had. AIBar now runs no detection at
    // all: it calls POST /v1/chat/intent and executes the verdict.
    //
    // That matters most for the multi-agent trigger it used to own. That regex
    // gated the heaviest thing the product does — seven cross-referenced
    // artifacts — on a bare pattern match, and its own comment recorded the near
    // miss ("how do I generate a PRD first?" once kicked off the whole run).
    const src = await readSource("..", "..", "components", "shared", "AIBar.tsx")

    expect(src).toContain("chatIntentApi")
    // No detector, shared or private.
    expect(src).not.toMatch(/isPrdCommand\s*\(/)
    expect(src).not.toMatch(/isMultiAgentCommand/)
    expect(src).not.toMatch(/prdCommandTask\s*\(/)
    // …and nothing resembling the old private detector's shape.
    expect(src).not.toMatch(/\.\*\?b?prd/i)
    expect(src).not.toContain("const isPrdCommand =")
  })

  it("AIBar no longer generates a PRD from the brief's top insight", async () => {
    const src = await readSource("..", "..", "components", "shared", "AIBar.tsx")
    // handlePrdCommand used to read brief.insights[0] and ignore the message.
    expect(src).not.toContain("insightIndex = 0")
    expect(src).toContain("runPrdGenerationFromTask")
  })

  it("the shared rules contain no unbounded gap between verb and noun", async () => {
    const src = await readSource("..", "prd-commands.ts")
    expect(src).not.toContain("PRD_VERB_SRC}\\b.*")
    expect(src).toContain("const PRD_GAP = 40")
  })
})
