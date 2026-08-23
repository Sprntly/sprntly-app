import { describe, expect, it } from "vitest"

import { friendlyPhase, FRIENDLY_PHASE_GENERIC } from "../friendlyPhase"

// The egress contract: `friendlyPhase` is the ONLY thing that curates a raw
// backend phase label into what a user sees. Its guarantee — no internal detail
// ever reaches the output — is load-bearing, so it is tested hard: every mapped
// case, the fail-safe fallback, and a battery of adversarial/leaky inputs that
// must all collapse to safe copy.

// The full set of strings the function is ALLOWED to return. The core property
// (below) asserts every output is one of these — which by construction means no
// input substring, count, name, path, or tool name can ever pass through.
const ALLOWED_OUTPUTS = new Set<string>([
  "Looking through your connected sources…",
  "Writing your answer…",
  "Putting your answer together…",
  "Reviewing your latest report…",
  "Researching your competitors…",
  "Writing your report…",
  // Shared report vocabulary (backend/app/report_phases.ReportPhase).
  "Gathering the latest information…",
  "Analyzing the findings…",
  "Researching products & features…",
  "Researching positioning…",
  "Researching pricing…",
  "Researching market & recent news…",
  FRIENDLY_PHASE_GENERIC, // "Working on your answer…"
])

// The RAW labels the shared report primitive emits (one per ReportPhase). Each
// must map to its own curated line — a report path that emits one of these can
// never fall through to the generic wait. Kept as an independent copy of the
// backend vocabulary on purpose: this is the egress gate, so it declares for
// itself exactly which labels it accepts.
const REPORT_VOCAB_MAPPINGS: ReadonlyArray<[string, string]> = [
  ["Gathering the latest information…", "Gathering the latest information…"],
  ["Analyzing the findings…", "Analyzing the findings…"],
  ["Writing your report…", "Writing your report…"],
  ["Researching products & features…", "Researching products & features…"],
  ["Researching positioning…", "Researching positioning…"],
  ["Researching pricing…", "Researching pricing…"],
  ["Researching market & recent news…", "Researching market & recent news…"],
]

// Real backend labels (the raw strings emitted by ask_runner / qa_agent /
// competitive_intel), including the ones that interpolate detail that must be
// stripped.
const KNOWN_MAPPINGS: ReadonlyArray<[string, string]> = [
  ["Searching your connected sources…", "Looking through your connected sources…"],
  ["Writing the answer…", "Writing your answer…"],
  ["Putting your answer together…", "Putting your answer together…"],
  ["Reading the last competitive review…", "Reviewing your latest report…"],
  ["Researched Acme Corp…", "Researching your competitors…"],
  ["Writing the review from 47 sourced observations…", "Writing your report…"],
  ...REPORT_VOCAB_MAPPINGS,
]

// Adversarial / leaky inputs: raw activity that would violate the contract if
// echoed. Every one must map to an allowed constant (never contain the leak).
const LEAKY_INPUTS: readonly string[] = [
  "Writing src/screens/Dashboard.tsx",
  "Editing /Users/someone/app/main.py",
  "Running kNN over the knowledge graph corpus",
  "Querying pgvector with embedding dim 1536",
  "tool_call: fetch_figma(path=foo.tsx)",
  "Researched MegaCompetitorInc with 9 findings",
  "error: connection refused on port 5432",
  "Traceback (most recent call last): KeyError",
  "iteration 12 of 40 — retrying max_tokens",
  "Reading Confluence page Template-Roadmap.md",
]

describe("friendlyPhase — egress contract", () => {
  it("only ever returns an approved user-facing constant (core property)", () => {
    const samples: (string | null | undefined)[] = [
      ...KNOWN_MAPPINGS.map(([raw]) => raw),
      ...LEAKY_INPUTS,
      null,
      undefined,
      "",
      "   ",
      "something we have never seen",
      "SEARCHING YOUR CONNECTED SOURCES…", // case-insensitive
    ]
    for (const s of samples) {
      expect(ALLOWED_OUTPUTS.has(friendlyPhase(s))).toBe(true)
    }
  })

  it("maps every known raw backend label to its curated copy", () => {
    for (const [raw, expected] of KNOWN_MAPPINGS) {
      expect(friendlyPhase(raw)).toBe(expected)
    }
  })

  it("maps every shared report-vocabulary label to a curated non-generic line", () => {
    // The one cross-cutting egress risk the audit called out: a wired backend
    // label with no mapping is silently dropped to the fallback. This guards
    // that every ReportPhase label the reports emit is curated.
    for (const [raw, expected] of REPORT_VOCAB_MAPPINGS) {
      const out = friendlyPhase(raw)
      expect(out).toBe(expected)
      expect(out).not.toBe(FRIENDLY_PHASE_GENERIC)
    }
  })

  it("falls back to the generic line for anything unmapped", () => {
    expect(friendlyPhase("wat is happening")).toBe(FRIENDLY_PHASE_GENERIC)
    expect(friendlyPhase(null)).toBe(FRIENDLY_PHASE_GENERIC)
    expect(friendlyPhase(undefined)).toBe(FRIENDLY_PHASE_GENERIC)
    expect(friendlyPhase("")).toBe(FRIENDLY_PHASE_GENERIC)
  })

  it("never leaks a path separator, extension, or file path", () => {
    for (const s of [...LEAKY_INPUTS, ...KNOWN_MAPPINGS.map(([r]) => r)]) {
      const out = friendlyPhase(s)
      expect(out).not.toMatch(/\//) // path separator
      expect(out).not.toMatch(/\.[a-z]{2,4}\b/i) // file extension (.tsx/.py/.md…)
      expect(out.toLowerCase()).not.toContain("src/")
    }
  })

  it("never leaks a raw digit-run (count / iteration / port / dimension)", () => {
    for (const s of LEAKY_INPUTS) {
      expect(friendlyPhase(s)).not.toMatch(/\d/)
    }
    // Specifically the interpolated backend counts/names.
    expect(friendlyPhase("Writing the review from 47 sourced observations…")).not.toMatch(/\d/)
    expect(friendlyPhase("Researched Acme Corp…")).not.toContain("Acme")
  })

  it("never leaks a known tool name or the substring 'error'", () => {
    const forbidden = [
      "fetch_figma", "pgvector", "knn", "kNN", "corpus", "knowledge graph",
      "tool_call", "max_tokens", "traceback", "confluence", "embedding", "error",
    ]
    for (const s of LEAKY_INPUTS) {
      const out = friendlyPhase(s).toLowerCase()
      for (const bad of forbidden) {
        expect(out).not.toContain(bad.toLowerCase())
      }
    }
  })

  it("never echoes an arbitrary company/competitor name from the input", () => {
    // A raw label carrying a company name must not surface it.
    expect(friendlyPhase("Researched Globex Corporation…")).not.toContain("Globex")
    expect(friendlyPhase("Researched Initech…")).not.toContain("Initech")
  })
})
