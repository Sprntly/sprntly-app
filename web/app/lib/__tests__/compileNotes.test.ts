// The no-jargon rule, as a test.
//
// The compiler's vocabulary is the PRD's CSS class names — `ul.ev`,
// `ul.inputs` inside `.appendix`, `p.hyp`, the empty `<style>` marker. Those
// names are load-bearing server-side and meaningless on a screen. The last
// describe block below is the whole rule: for every note shape the backend can
// hand us, the OUTPUT contains none of them. It runs over the raw-note shapes
// too, not just the documented `{code, message}` one, because the guarantee has
// to hold when the contract slips.
import { describe, expect, it } from "vitest"
import {
  ARTIFACT_TYPE_DOC,
  ARTIFACT_TYPE_IDS,
  ARTIFACT_TYPE_LABELS,
  GENERIC_COMPILE_NOTE,
  activationRefusal,
  addFormatLabel,
  builtinFormatName,
  notWiredNote,
  translateCompileNote,
  translateCompileNotes,
} from "../compileNotes"

/** Every code in store.COMPILE_NOTE_CODES, with the phrase that proves the
 *  right sentence came back (not merely "something non-generic"). */
const KNOWN: [string, RegExp][] = [
  ["missing_evidence_list", /bulleted evidence list/],
  ["missing_input_questions", /collects open questions/],
  ["missing_hypothesis", /hypothesis statement inside it/],
  ["missing_requirements", /row or a user story/],
  ["missing_title", /no single document title/],
  ["missing_style_marker", /come out unformatted/],
  ["unsafe_script", /won't run scripts inside a document/],
  ["unsafe_attribute", /runs when a document is opened/],
  ["unsafe_remote_asset", /only allows Google Fonts/],
  ["compile_error", /Nothing about your file has changed/],
]

/** Jargon that must never survive translation. */
const JARGON = ["ul.ev", "ul.inputs", "p.hyp", ".appendix", "<style>"]

describe("translateCompileNote", () => {
  it("maps every known code to its own sentence", () => {
    for (const [code, phrase] of KNOWN) {
      const out = translateCompileNote({ code, message: "raw ul.ev missing" })
      expect(out, code).toMatch(phrase)
      expect(out, code).not.toBe(GENERIC_COMPILE_NOTE)
    }
  })

  it("falls back to the generic line for an unknown code with no clue", () => {
    expect(translateCompileNote({ code: "quantum_flux", message: "???" })).toBe(
      GENERIC_COMPILE_NOTE,
    )
  })

  it("null / undefined / empty are the generic line, never a crash", () => {
    expect(translateCompileNote(null)).toBe(GENERIC_COMPILE_NOTE)
    expect(translateCompileNote(undefined)).toBe(GENERIC_COMPILE_NOTE)
    expect(translateCompileNote("")).toBe(GENERIC_COMPILE_NOTE)
  })

  it("recovers the code by substring when only a bare message arrives", () => {
    // This is a list row's `compile_summary`: the server drops the code and
    // sends the first note's message alone.
    expect(translateCompileNote("We couldn't find ul.ev in your format")).toMatch(
      /bulleted evidence list/,
    )
    expect(
      translateCompileNote("no ul.inputs inside the .appendix section"),
    ).toMatch(/collects open questions/)
    expect(translateCompileNote("<script> tag found in the upload")).toMatch(
      /won't run scripts/,
    )
  })

  it("never echoes the raw message, even when it is the only input", () => {
    const raw = "ul.ev is missing from .appendix and <style> was not empty"
    const out = translateCompileNote(raw)
    for (const bad of JARGON) expect(out).not.toContain(bad)
  })
})

describe("translateCompileNotes", () => {
  it("translates each note and de-duplicates collapsed lines", () => {
    const out = translateCompileNotes([
      { code: "quantum_flux", message: "???" },
      { code: "warp_core", message: "???" },
      { code: "missing_title", message: "no <h1>" },
    ])
    // Two unknowns collapse onto one generic line; the preview must not print
    // the same sentence twice.
    expect(out).toHaveLength(2)
    expect(out.filter((l) => l === GENERIC_COMPILE_NOTE)).toHaveLength(1)
  })

  it("answers [] for a missing or non-array value", () => {
    expect(translateCompileNotes(null)).toEqual([])
    expect(translateCompileNotes(undefined)).toEqual([])
  })
})

describe("no backend jargon ever reaches a screen", () => {
  // Shapes the backend really produces: the documented {code,message}, the
  // list row's bare summary, an unknown code, and a raw validator sentence.
  const FIXTURES: (string | { code: string; message: string })[] = [
    { code: "missing_evidence_list", message: "no `ul.ev` in the skeleton" },
    { code: "missing_input_questions", message: "`ul.inputs` not inside `.appendix`" },
    { code: "missing_hypothesis", message: "no `p.hyp` under the hypothesis heading" },
    { code: "missing_requirements", message: "no <table> with a .pill Type column" },
    { code: "missing_title", message: "expected exactly one <h1>" },
    { code: "missing_style_marker", message: "expected one empty <style></style>" },
    { code: "unsafe_script", message: "<script src=…> is not allowed" },
    { code: "unsafe_attribute", message: "onclick= handler found" },
    { code: "unsafe_remote_asset", message: "src= points off the allowlist" },
    { code: "compile_error", message: "the model returned unparseable JSON" },
    { code: "brand_new_code", message: "something about .appendix > ul.inputs" },
    "no `ul.ev` in the skeleton",
    "`ul.inputs` not inside `.appendix`",
    "expected one empty <style></style>",
    "",
  ]

  it("no output for any fixture contains ul.ev, ul.inputs, p.hyp, .appendix or <style>", () => {
    for (const note of FIXTURES) {
      const out = translateCompileNote(note)
      for (const bad of JARGON) {
        expect(out, `${JSON.stringify(note)} → ${out}`).not.toContain(bad)
      }
    }
    // And through the plural path, which is what the preview panel renders.
    for (const line of translateCompileNotes(FIXTURES)) {
      for (const bad of JARGON) expect(line).not.toContain(bad)
    }
  })
})

describe("activationRefusal — the 409 apiErrorMessage cannot render", () => {
  // lib/api.ts:27 handles a STRING or a validation-LIST detail. Activate's
  // detail is an OBJECT, so err.message degrades to "Request failed (409)".
  it("translates a not_ready refusal through the compile-note table", () => {
    const r = activationRefusal({
      detail: {
        message: "This format isn't ready.",
        code: "not_ready",
        notes: [{ code: "missing_evidence_list", message: "no ul.ev" }],
      },
    })
    expect(r.title).toBe("This format isn't ready yet")
    expect(r.reason).toMatch(/bulleted evidence list/)
    expect(r.reason).not.toContain("ul.ev")
    expect(r.refetch).toBe(true)
  })

  it("does NOT feed the 409's own codes to the note table", () => {
    // `not_ready` and `activation_raced` are deliberately outside
    // COMPILE_NOTE_CODES — they describe the refusal, not the format.
    const r = activationRefusal({
      detail: { message: "This format isn't ready.", code: "not_ready", notes: [] },
    })
    expect(r.reason).toBe(GENERIC_COMPILE_NOTE)
  })

  it("uses the server's own sentence for a raced activation", () => {
    const r = activationRefusal({
      detail: {
        message: "Another format just became your team's PRD format. Refresh and try again.",
        code: "activation_raced",
        notes: [],
      },
    })
    expect(r.title).toMatch(/Someone else just changed/)
    expect(r.reason).toMatch(/Refresh and try again/)
  })

  it("degrades to the generic line for a body it can't read", () => {
    expect(activationRefusal(null).reason).toBe(GENERIC_COMPILE_NOTE)
    expect(activationRefusal({ detail: "a plain string" }).reason).toBe(
      GENERIC_COMPILE_NOTE,
    )
  })
})

describe("type copy", () => {
  it("never shows a raw column value like impl_spec", () => {
    for (const t of ARTIFACT_TYPE_IDS) {
      expect(ARTIFACT_TYPE_LABELS[t]).not.toMatch(/impl_spec/)
      expect(addFormatLabel(t)).not.toMatch(/impl_spec/)
      expect(builtinFormatName(t)).not.toMatch(/impl_spec/)
      expect(notWiredNote(t)).not.toMatch(/impl_spec/)
    }
    expect(ARTIFACT_TYPE_LABELS.impl_spec).toBe("Engineering spec")
    expect(ARTIFACT_TYPE_DOC.impl_spec).toBe("engineering spec")
  })

  it("gets the article right for engineering spec", () => {
    expect(addFormatLabel("prd")).toBe("Add a PRD format")
    expect(addFormatLabel("tickets")).toBe("Add a ticket format")
    expect(addFormatLabel("impl_spec")).toBe("Add an engineering-spec format")
  })

  it("names the type in the not-wired-yet note", () => {
    expect(notWiredNote("tickets")).toMatch(
      /Sprntly doesn't write tickets from a custom format yet/,
    )
    expect(notWiredNote("prd")).toMatch(/Sprntly doesn't write PRDs/)
  })
})
