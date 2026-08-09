/**
 * CROSS-STACK EVIDENCE RENDER CONTRACT (web side).
 *
 * The evidence artifact's CONTENT is now written by the LLM directly and the
 * `evidence-brief` skill supplies only the rendering contract (backend
 * evidence-kg-v6). Freer prose is the point; freer MARKUP is the risk, because
 * the frontend decides which renderer runs from the first few characters of
 * `payload_md`:
 *
 *   - `looksLikeHtmlBrief` is an ANCHORED sniff (`^\s*<(!doctype|meta|html|div|
 *     style)`).
 *   - `markdownToEvidenceState` — the parser behind the artifact-panel Evidence
 *     tab, the Artifacts screen, share links / GuestArtifactViewer, and the
 *     combined Evidence+PRD export — branches on that sniff ALONE. No variant
 *     fallback. Miss it and the HTML goes through the legacy `:::block` markdown
 *     parser: `sections: []`, `html: undefined`, a BLANK panel and an export
 *     that silently degrades to PRD-only. Nothing throws.
 *   - The full-page EvidenceScreen is the one surface with a second chance
 *     (`variant === "v3" || looksLikeHtmlBrief(...)`).
 *
 * This file runs the SAME golden brief the backend asserts against
 * (`backend/tests/fixtures/evidence/golden_brief.html`, pinned by
 * `backend/tests/test_evidence_render_contract.py`) through the REAL parsers, so
 * the two runtimes cannot drift. `test-web.yml` triggers on that fixture path.
 */
import { readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"
import { describe, expect, it } from "vitest"
import { markdownToEvidenceState } from "../evidence-adapter"
import {
  htmlPrdToPlainText,
  looksLikeHtmlBrief,
  stripHtmlCodeFence,
  stripHypothesisSection,
} from "../htmlBrief"

const HERE = dirname(fileURLToPath(import.meta.url))
const BACKEND = join(HERE, "..", "..", "..", "..", "backend")
const GOLDEN = readFileSync(
  join(BACKEND, "tests", "fixtures", "evidence", "golden_brief.html"),
  "utf-8",
)
const CANONICAL_CSS = readFileSync(
  join(BACKEND, "skills", "evidence-brief", "assets", "evidence.css"),
  "utf-8",
)

// What the server actually stores: the model's empty <style> marker replaced by
// the canonical stylesheet (backend app/html_style.py::inject_canonical_css).
const STORED = GOLDEN.replace(
  "<style></style>",
  `<style>\n${CANONICAL_CSS.trim()}\n</style>`,
)

describe("evidence render contract (backend golden brief ↔ web parsers)", () => {
  it("the stored payload is recognised as an HTML brief", () => {
    expect(looksLikeHtmlBrief(STORED)).toBe(true)
    // …and so is the model's own output, before the server injects the CSS.
    expect(looksLikeHtmlBrief(GOLDEN)).toBe(true)
  })

  it("CONSUMER: markdownToEvidenceState passes the document through intact", () => {
    // Artifact-panel Evidence tab (lib/runEvidenceGeneration.ts), Artifacts
    // screen (ArtifactsScreen.tsx:522) and share links
    // (GuestArtifactViewer.tsx:122) all go through this one function.
    const out = markdownToEvidenceState(STORED)
    expect(out.html).toBe(STORED)
    expect(out.sections).toEqual([])
    // The degraded path is `html === undefined` — that is the blank panel.
    expect(out.html).toBeTruthy()
  })

  it("CONSUMER: the combined Evidence+PRD export sees a non-empty html field", () => {
    // ContentPanel gates the combined download on `evidence?.html` being
    // truthy; a sniff miss silently downgrades it to a PRD-only export.
    expect(markdownToEvidenceState(STORED).html).toBeTruthy()
  })

  it("CONSUMER: EvidenceScreen picks the HTML renderer on both branches", () => {
    const byVariant = (variant: string, payload: string) =>
      variant === "v3" || looksLikeHtmlBrief(payload)
    expect(byVariant("v3", STORED)).toBe(true)
    expect(byVariant("v2", STORED)).toBe(true) // sniff alone is enough
  })

  it("CONSUMER: EvidenceHtmlBrief's strips leave the brief untouched", () => {
    // The viewer runs stripHypothesisSection(stripHtmlCodeFence(html)). The
    // contract forbids `class="hyp"`, so both must be no-ops here — an
    // over-eager strip would silently delete a real section.
    const rendered = stripHypothesisSection(stripHtmlCodeFence(STORED))
    expect(rendered).toBe(STORED)
    expect(rendered).toContain('<div class="wrap">')
    expect(rendered).toContain("<svg")
  })

  it("the brief carries the .wrap container the viewer's width override keys on", () => {
    // EvidenceHtmlBrief injects `.wrap { max-width: 940px }` into the iframe.
    // No .wrap → the override is inert and the brief renders full-bleed.
    expect(STORED).toContain('class="wrap"')
  })

  it("carries no script and no external resource", () => {
    // The iframe is sandbox="allow-same-origin" with NO allow-scripts, so a
    // script could not run — but it must not be there in the first place, and
    // a remote font/image would render as a hole.
    expect(STORED.toLowerCase()).not.toContain("<script")
    expect(STORED).not.toMatch(/(?:src|href)\s*=\s*["']https?:\/\//i)
  })

  it("CONSUMER: plain-text extraction yields the brief's prose", () => {
    // htmlPrdToPlainText is how non-rendering consumers (ticket description,
    // Claude-context builder) read an HTML document. The stylesheet must not
    // leak into it.
    const text = htmlPrdToPlainText(STORED)
    expect(text).toContain("SSO Is the Gate on Enterprise Expansion")
    expect(text).toContain("Four HubSpot opportunities worth $1.4M")
    expect(text).not.toContain("box-sizing")
  })

  it("a fenced payload still renders (defensive client-side strip)", () => {
    // The backend strips the fence before storing; this is the counterpart for
    // rows stored before that, and for any caller passing the raw row.
    const fenced = "```html\n" + STORED + "\n```"
    expect(looksLikeHtmlBrief(fenced)).toBe(true)
    // stripHtmlCodeFence trims the unwrapped content, so compare trimmed.
    expect(markdownToEvidenceState(fenced).html).toBe(STORED.trim())
  })

  it("PROVES THE RISK: one sentence of preamble breaks every panel consumer", () => {
    // This is the failure the backend normaliser exists to prevent — asserted
    // here so the consequence is written down on the side that suffers it.
    // It is not even a blank panel: the markdown parser turns the document's
    // own markup into paragraphs, so the user sees raw HTML as body text.
    const withPreamble = "Here's the evidence brief you asked for:\n\n" + STORED
    expect(looksLikeHtmlBrief(withPreamble)).toBe(false)
    const out = markdownToEvidenceState(withPreamble)
    expect(out.html).toBeUndefined() // → no iframe render, no combined export
    const asText = out.sections
      .map((s) => ("text" in s ? String(s.text) : ""))
      .join("\n")
    expect(asText).toContain('<div class="wrap">')
  })
})
