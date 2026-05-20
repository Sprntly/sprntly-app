import { describe, expect, it } from "vitest"
import {
  formatRelativeDate,
  humanizeBytes,
  iconForKind,
  truncateFilename,
} from "../sources-helpers"

describe("humanizeBytes", () => {
  it("formats under 1 KB as bytes", () => {
    expect(humanizeBytes(0)).toBe("0 B")
    expect(humanizeBytes(512)).toBe("512 B")
    expect(humanizeBytes(1023)).toBe("1023 B")
  })

  it("formats KB with one decimal under 10 KB, integer otherwise", () => {
    expect(humanizeBytes(1024)).toBe("1.0 KB")
    expect(humanizeBytes(1536)).toBe("1.5 KB")
    expect(humanizeBytes(240000)).toMatch(/^234 KB$/)
  })

  it("formats MB and GB", () => {
    expect(humanizeBytes(1024 * 1024)).toBe("1.0 MB")
    expect(humanizeBytes(50 * 1024 * 1024)).toBe("50 MB")
    expect(humanizeBytes(2 * 1024 * 1024 * 1024)).toBe("2.0 GB")
  })

  it("guards against bad input", () => {
    expect(humanizeBytes(-5)).toBe("—")
    expect(humanizeBytes(Number.NaN)).toBe("—")
  })
})

describe("formatRelativeDate", () => {
  const now = new Date("2026-05-19T18:00:00Z")

  it("returns 'just now' for sub-minute deltas", () => {
    expect(formatRelativeDate("2026-05-19T17:59:30Z", now)).toBe("just now")
  })

  it("returns minutes / hours / days for fresh content", () => {
    expect(formatRelativeDate("2026-05-19T17:30:00Z", now)).toBe("30 minutes ago")
    expect(formatRelativeDate("2026-05-19T15:00:00Z", now)).toBe("3 hours ago")
    expect(formatRelativeDate("2026-05-16T18:00:00Z", now)).toBe("3 days ago")
  })

  it("singularizes 1-unit deltas", () => {
    expect(formatRelativeDate("2026-05-19T17:00:00Z", now)).toBe("1 hour ago")
    expect(formatRelativeDate("2026-05-18T18:00:00Z", now)).toBe("1 day ago")
  })

  it("falls back to an absolute date past ~a month", () => {
    const out = formatRelativeDate("2025-12-01T12:00:00Z", now)
    expect(out).toMatch(/^Dec 1, 2025$/)
  })

  it("returns the raw string when the input is not parseable", () => {
    expect(formatRelativeDate("not-a-date", now)).toBe("not-a-date")
  })

  it("treats future timestamps as 'just now' (clock skew tolerance)", () => {
    expect(formatRelativeDate("2026-05-19T18:05:00Z", now)).toBe("just now")
  })
})

describe("truncateFilename", () => {
  it("returns short names unchanged", () => {
    expect(truncateFilename("notes.md")).toBe("notes.md")
  })

  it("truncates with an ellipsis past the max", () => {
    const long = "a".repeat(60)
    const out = truncateFilename(long, 40)
    expect(out.length).toBe(40)
    expect(out.endsWith("…")).toBe(true)
  })

  it("respects a custom max", () => {
    expect(truncateFilename("hello world", 5)).toBe("hell…")
  })
})

describe("iconForKind", () => {
  it("maps known extensions to a glyph", () => {
    expect(iconForKind("pdf")).toBe("📕")
    expect(iconForKind("PDF")).toBe("📕")
    expect(iconForKind("docx")).toBe("📄")
    expect(iconForKind("xlsx")).toBe("📊")
    expect(iconForKind("csv")).toBe("📊")
    expect(iconForKind("md")).toBe("📝")
    expect(iconForKind("txt")).toBe("📃")
  })

  it("falls back to a generic page for unknown kinds", () => {
    expect(iconForKind("xyz")).toBe("📄")
    expect(iconForKind("")).toBe("📄")
  })
})
