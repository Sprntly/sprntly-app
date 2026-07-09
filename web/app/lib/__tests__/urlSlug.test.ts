// Unit tests for the public-share-URL slugifier (urlSlugify) + mirror-parity
// with the backend Python slugifier.
//
// SHARED_PARITY_CASES is copied verbatim (input, fallback, expected) from the
// backend test (backend/tests/test_design_agent_url_slug.py :: SHARED_PARITY_CASES).
// Asserting the TS port against the SAME expected outputs proves the two
// slugifiers produce identical results for identical inputs (AC3).
import { describe, it, expect } from "vitest"
import { urlSlugify } from "../urlSlug"

// (input, fallback, expected) — mirrored verbatim from the Python test table.
const SHARED_PARITY_CASES: Array<[string, string, string]> = [
  ["Lab X", "item", "lab-x"],
  ["  Acme!! Corp  ", "item", "acme-corp"],
  ["Customer Onboarding Revamp", "prototype", "customer-onboarding-revamp"],
  ["", "company", "company"],
  ["Foo & Bar / Baz", "item", "foo-bar-baz"],
  [
    "aaaaaaa-bbbbbbb-ccccccc-ddddddd-eeeeeee-fffffff",
    "item",
    "aaaaaaa-bbbbbbb-ccccccc-ddddddd-eeeeeee",
  ],
]

describe("urlSlugify", () => {
  it("lowercases and dashes (test_url_slugify_lowercases_and_dashes)", () => {
    expect(urlSlugify("Lab X", "item")).toBe("lab-x")
  })

  it("collapses runs and strips ends (test_url_slugify_collapses_and_strips)", () => {
    expect(urlSlugify("  Acme!! Corp  ", "item")).toBe("acme-corp")
  })

  it("empty / whitespace / all-punctuation input returns the fallback", () => {
    expect(urlSlugify("", "company")).toBe("company")
    expect(urlSlugify("   ", "company")).toBe("company")
    expect(urlSlugify("!!! ???", "item")).toBe("item")
  })

  it("tolerates null/undefined input (returns the fallback, never throws)", () => {
    // The container may pass a null prdTitle / undefined name; guard with `?? ""`.
    expect(urlSlugify(null as unknown as string, "prototype")).toBe("prototype")
    expect(urlSlugify(undefined as unknown as string, "prototype")).toBe("prototype")
  })

  it("caps length and strips a trailing dash left by the cut (test_url_slugify_caps_length_no_trailing_dash)", () => {
    const long = "word-".repeat(12) // 60 chars, ends "...word-"
    const out = urlSlugify(long, "item", 40)
    expect(out.length).toBeLessThanOrEqual(40)
    expect(out.endsWith("-")).toBe(false)
    expect(out).toBe("word-word-word-word-word-word-word-word")
  })

  it("matches the backend slugifier on the shared parity table (AC3 mirror-parity)", () => {
    for (const [input, fallback, expected] of SHARED_PARITY_CASES) {
      expect(urlSlugify(input, fallback)).toBe(expected)
    }
  })
})
