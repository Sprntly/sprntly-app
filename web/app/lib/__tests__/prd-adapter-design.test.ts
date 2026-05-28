import { describe, expect, it } from "vitest"
import { markdownToPrdState } from "../prd-adapter"
import type { PrdDesignBlock } from "../../types/content"

/** Run the full adapter and return every parsed :::design block. */
function designBlocks(blockMd: string): PrdDesignBlock[] {
  const out = markdownToPrdState(`# T\n\n${blockMd}`)
  return out.sections.filter(
    (s): s is PrdDesignBlock => s.type === "prd-design",
  )
}

/** Return the first parsed :::design block (throws if none). */
function firstDesign(blockMd: string): PrdDesignBlock {
  const blocks = designBlocks(blockMd)
  if (blocks.length === 0) throw new Error("expected a prd-design block")
  return blocks[0]
}

describe("markdownToPrdState — :::design block", () => {
  it("parses both keys", () => {
    const d = firstDesign(
      [":::design", "platform_hint: both", "notes: foo", ":::"].join("\n"),
    )
    expect(d).toEqual({ type: "prd-design", platformHint: "both", notes: "foo" })
  })

  it("parses an empty body to a minimal object (lenient, no throw)", () => {
    const d = firstDesign([":::design", ":::"].join("\n"))
    expect(d).toEqual({ type: "prd-design" })
    expect(d.platformHint).toBeUndefined()
    expect(d.notes).toBeUndefined()
  })

  it("parses only platform_hint", () => {
    const d = firstDesign(
      [":::design", "platform_hint: mobile", ":::"].join("\n"),
    )
    expect(d).toEqual({ type: "prd-design", platformHint: "mobile" })
  })

  it("parses only notes", () => {
    const d = firstDesign(
      [":::design", "notes: use dark theme", ":::"].join("\n"),
    )
    expect(d).toEqual({ type: "prd-design", notes: "use dark theme" })
  })

  it("drops an unrecognised platform_hint value leniently (no throw)", () => {
    const d = firstDesign(
      [":::design", "platform_hint: tablet", ":::"].join("\n"),
    )
    expect(d).toEqual({ type: "prd-design" })
    expect(d.platformHint).toBeUndefined()
  })

  it("salvages a malformed body to a minimal object (no throw)", () => {
    const d = firstDesign(
      [":::design", "this is not a key value line at all", "12345", ":::"].join(
        "\n",
      ),
    )
    expect(d).toEqual({ type: "prd-design" })
  })

  it("takes the first :::design block when several are present", () => {
    const md = [
      ":::design",
      "platform_hint: desktop",
      "notes: first",
      ":::",
      "",
      ":::design",
      "platform_hint: mobile",
      "notes: second",
      ":::",
    ].join("\n")
    const d = firstDesign(md)
    expect(d.platformHint).toBe("desktop")
    expect(d.notes).toBe("first")
  })
})
