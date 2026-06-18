/**
 * CROSS-STACK PIPELINE CONTRACT — the seam that has broken in the past.
 *
 * PRD/evidence generation is a two-runtime pipeline:
 *   backend template (the structure the LLM is told to emit)
 *     → LLM → markdown stored in `payload_md`
 *     → frontend adapter (markdownTo*State) parses the `:::name` semantic blocks
 *     → React renderer turns each typed block into a component.
 *
 * The backend templates and the frontend adapters must agree on the semantic
 * block vocabulary (`:::hero`, `:::tldr`, …) and on each block's JSON keys. When
 * they drift — a block renamed in the template, a JSON key changed, an adapter
 * `case` dropped — nothing crashes: the adapter silently degrades the block to a
 * "could not parse" paragraph and the rich UI quietly disappears. Per-side unit
 * tests don't catch this because each side tests against its OWN fixtures.
 *
 * This test closes that gap by reading the ACTUAL backend template + sample
 * markdown files and running them through the ACTUAL adapters, asserting nothing
 * degrades. It runs in the web CI lane; `test-web.yml` also triggers on changes
 * to the backend template files (see its `paths:`) so a backend-only edit that
 * breaks the contract still fails here.
 *
 * NB on PRD: the LIVE PRD runner emits its human half (`payload_md`) from the
 * `prd-author` SKILL template, which is plain `##` markdown (no `:::` blocks) —
 * so the PRD adapter renders it as h2/p/table/ul. The rich `:::tldr`/… PRD
 * blocks the adapter also supports come from the canonical `sprntly_prd_sample.md`
 * format; we guard BOTH below (the live plain-markdown shape AND the rich
 * adapter vocabulary) so neither regresses.
 */
import { readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"
import { describe, expect, it } from "vitest"
import { markdownToEvidenceState } from "../evidence-adapter"
import { markdownToPrdState } from "../prd-adapter"
import type { PrdSection } from "../../types/content"

const HERE = dirname(fileURLToPath(import.meta.url))
const BACKEND_DATA = join(HERE, "..", "..", "..", "..", "backend", "data")
const PRD_SKILL_TEMPLATE = join(
  HERE, "..", "..", "..", "..",
  "backend", "skills", "prd-author", "templates", "prd-template.md",
)

const read = (p: string) => readFileSync(p, "utf-8")

// The "block could not be parsed" fallback the adapters emit when a `:::block`
// body fails to parse. Its presence in the output is the precise drift signal:
// the block reached the adapter but the adapter could not turn it into its
// typed section. (See fallbackParagraphFromBlock in each adapter.)
const FALLBACK_RE = /block .* could not parse/i

function fallbackSections(sections: PrdSection[]): PrdSection[] {
  return sections.filter((s) => s.type === "p" && FALLBACK_RE.test((s as { text: string }).text))
}

// Distinct `:::name` openers that actually appear at the start of a line in a
// markdown doc (mirrors the adapters' BLOCK_OPEN_RE; ignores `:::name` that
// appears inline inside prose, backticks, or table cells).
const BLOCK_OPEN_RE = /^:::([a-z][a-z0-9-]*)(\s.*)?$/
function blockOpeners(markdown: string): string[] {
  const names = new Set<string>()
  for (const raw of markdown.replace(/\r\n/g, "\n").split("\n")) {
    const m = raw.trim().match(BLOCK_OPEN_RE)
    if (m) names.add(m[1])
  }
  return [...names].sort()
}

// The block names each adapter knows how to turn into a typed section. These
// MIRROR the `switch (name)` cases in the adapters — if you add/rename an
// adapter case you must update the matching list here, and that is the cue to
// also update the backend template that emits it.
const EVIDENCE_KNOWN_BLOCKS = [
  "hero", "context-chip", "cuts-index", "source", "callout", "quote", "forecast",
]
const PRD_KNOWN_BLOCKS = [
  "context-chip", "tldr", "problem", "hypothesis", "requirements",
  "acceptance-criteria", "metrics", "risks", "milestones", "dod", "design",
]

describe("evidence pipeline contract (backend template ↔ evidence-adapter)", () => {
  const template = read(join(BACKEND_DATA, "sprntly_evidence_template.md"))
  const sample = read(join(BACKEND_DATA, "sprntly_evidence_sample.md"))

  it("every :::block the backend evidence files emit is one the adapter handles", () => {
    // Drift guard: a block added/renamed on the backend with no adapter `case`
    // would land here. Fails with the offending names so the fix is obvious.
    for (const [label, md] of [["template", template], ["sample", sample]] as const) {
      const unknown = blockOpeners(md).filter((b) => !EVIDENCE_KNOWN_BLOCKS.includes(b))
      expect(unknown, `${label}: unhandled :::blocks → adapter case missing`).toEqual([])
    }
  })

  it("parses the canonical evidence TEMPLATE with zero degraded blocks", () => {
    const out = markdownToEvidenceState(template)
    expect(fallbackSections(out.sections)).toEqual([])
    expect(out.title.length).toBeGreaterThan(0)
  })

  it("parses the canonical evidence SAMPLE into the full rich vocabulary", () => {
    const out = markdownToEvidenceState(sample)
    expect(fallbackSections(out.sections)).toEqual([])
    const types = new Set(out.sections.map((s) => s.type))
    // The sample is the reference doc; it must exercise every rich block the
    // renderer depends on. A backend format change that drops one trips this.
    for (const t of [
      "v2-hero", "v2-cuts-index", "v2-source", "v2-rules-callout",
      "v2-quote", "v2-context-chip",
    ]) {
      expect(types.has(t as PrdSection["type"]), `missing ${t}`).toBe(true)
    }
    // The sample carries real (valid-JSON) charts; the renderer must get them.
    expect(types.has("chart")).toBe(true)
  })
})

describe("PRD pipeline contract (backend ↔ prd-adapter)", () => {
  const sample = read(join(BACKEND_DATA, "sprntly_prd_sample.md"))
  const skillTemplate = read(PRD_SKILL_TEMPLATE)

  it("every :::block the canonical PRD sample emits is one the adapter handles", () => {
    const unknown = blockOpeners(sample).filter((b) => !PRD_KNOWN_BLOCKS.includes(b))
    expect(unknown, "unhandled :::blocks → adapter case missing").toEqual([])
  })

  it("parses the rich PRD SAMPLE into the full semantic vocabulary, no degraded blocks", () => {
    const out = markdownToPrdState(sample)
    expect(fallbackSections(out.sections)).toEqual([])
    const types = new Set(out.sections.map((s) => s.type))
    for (const t of [
      "prd-tldr", "prd-problem", "prd-hypothesis", "prd-requirements",
      "prd-acceptance-criteria", "prd-metrics", "prd-risks", "prd-milestones",
      "prd-dod", "v2-context-chip",
    ]) {
      expect(types.has(t as PrdSection["type"]), `missing ${t}`).toBe(true)
    }
  })

  it("renders the LIVE plain-markdown PRD (the prd-author skill template) without degrading", () => {
    // The live runner's Part A is this skill template's plain `##` structure —
    // the adapter must turn it into a non-empty, readable section list (headings,
    // tables, lists, paragraphs) and never throw or emit a parse-failure block.
    const out = markdownToPrdState(skillTemplate)
    expect(fallbackSections(out.sections)).toEqual([])
    expect(out.sections.length).toBeGreaterThan(0)
    expect(out.title.length).toBeGreaterThan(0)
    // The 2-part method headings survive into the rendered section stream.
    const headings = out.sections
      .filter((s): s is Extract<PrdSection, { type: "h2" }> => s.type === "h2")
      .map((s) => s.text)
    expect(headings.some((h) => /Problem/i.test(h))).toBe(true)
  })
})
