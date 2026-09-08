// @vitest-environment node
//
// Which attachments the browser may read as text.
//
// THE BUG THIS ENCODES. Both composers used a DENY-LIST of binary formats and
// read everything else with `readAsText`. A .zip attached to a chat was
// therefore decoded as text — and because mojibake is a non-empty string it
// landed in `content` as though extraction had succeeded, so the server
// parser (only consulted when `content` is empty) was never called. The
// observed answer described "the ZIP local file header (PK signature)" and
// concluded the archive held one file: the model reading raw container bytes.
//
// Inverting to an allow-list makes the failure safe by default — an unknown
// extension goes to the server, which either extracts real text or says
// plainly that it cannot.
import { describe, expect, it } from "vitest"

import {
  CLIENT_READABLE_TEXT_EXTENSIONS,
  isClientReadableText,
} from "../attachmentText"

describe("text formats are read in the browser", () => {
  it("accepts the formats whose bytes ARE their text", () => {
    for (const ext of CLIENT_READABLE_TEXT_EXTENSIONS) {
      expect(isClientReadableText(`notes.${ext}`), ext).toBe(true)
    }
  })

  it("is case-insensitive — a file from Windows is still text", () => {
    expect(isClientReadableText("NOTES.MD")).toBe(true)
    expect(isClientReadableText("Data.CSV")).toBe(true)
  })
})

describe("everything else goes to the server", () => {
  it("refuses ARCHIVES — the case that started this", () => {
    expect(isClientReadableText("docs.zip")).toBe(false)
    expect(isClientReadableText("DOCS.ZIP")).toBe(false)
  })

  it("refuses spreadsheets, which are archives wearing another name", () => {
    // An .xlsx IS a zip. This was found once before and patched into only one
    // of the two composers, which is why the predicate is now shared.
    expect(isClientReadableText("model.xlsx")).toBe(false)
    expect(isClientReadableText("model.xls")).toBe(false)
  })

  it("refuses documents", () => {
    for (const name of ["deck.pptx", "brief.pdf", "spec.docx", "old.doc"]) {
      expect(isClientReadableText(name), name).toBe(false)
    }
  })

  it("refuses a format nobody has thought of yet", () => {
    // The whole point of inverting the rule: the DEFAULT is safe. A new binary
    // format costs nothing — it goes to the server, which can say it cannot
    // read it, rather than being silently transcribed into noise.
    expect(isClientReadableText("recording.m4a")).toBe(false)
    expect(isClientReadableText("archive.7z")).toBe(false)
    expect(isClientReadableText("photo.heic")).toBe(false)
  })

  it("refuses a name with no extension at all", () => {
    // It could be anything, and guessing "text" is the guess that corrupts
    // silently. The server can still read it.
    expect(isClientReadableText("README")).toBe(false)
    expect(isClientReadableText("")).toBe(false)
  })

  it("matches only the FINAL extension", () => {
    // `report.md.zip` is an archive, whatever the middle of its name says.
    expect(isClientReadableText("report.md.zip")).toBe(false)
    expect(isClientReadableText("archive.zip.md")).toBe(true)
  })

  it("is not fooled by an extension in the middle of a name", () => {
    expect(isClientReadableText("notes.txt.exe")).toBe(false)
  })
})
