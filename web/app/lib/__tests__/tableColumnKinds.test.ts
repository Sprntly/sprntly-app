import { describe, expect, it } from "vitest"

import {
  columnKinds,
  labelColumnClasses,
  tableRows,
} from "../tableColumnKinds"

/** Minimal hast shapes, as react-markdown hands them to a `table` override. */
const text = (value: string) => ({ type: "text", value })
const cell = (tag: "td" | "th", value: string) => ({
  type: "element",
  tagName: tag,
  children: [text(value)],
})
const row = (tag: "td" | "th", values: string[]) => ({
  type: "element",
  tagName: "tr",
  children: values.map((v) => cell(tag, v)),
})
const table = (header: string[], body: string[][]) => ({
  type: "element",
  tagName: "table",
  children: [
    { type: "element", tagName: "thead", children: [row("th", header)] },
    {
      type: "element",
      tagName: "tbody",
      children: body.map((r) => row("td", r)),
    },
  ],
})

const PROSE =
  "Everything happening in and around your product — analytics, customer " +
  "voice, CRM, support tickets, codebase, design, competitor moves"

describe("tableRows", () => {
  it("reads header and body cells as one grid", () => {
    const rows = tableRows(table(["Dimension", "What It Is"], [["Memory", PROSE]]))
    expect(rows).toEqual([
      ["Dimension", "What It Is"],
      ["Memory", PROSE],
    ])
  })

  it("flattens inline markup inside a cell", () => {
    const bold = {
      type: "element",
      tagName: "td",
      children: [
        text("D-"),
        { type: "element", tagName: "strong", children: [text("001")] },
      ],
    }
    const node = {
      type: "element",
      tagName: "table",
      children: [{ type: "element", tagName: "tr", children: [bold] }],
    }
    // A cell's width is driven by its rendered text, so bold/link markup must
    // not split one value into two shorter ones.
    expect(tableRows(node)).toEqual([["D-001"]])
  })

  it("returns nothing for a node with no rows", () => {
    expect(tableRows({ type: "element", tagName: "table", children: [] })).toEqual([])
  })
})

describe("columnKinds", () => {
  it("marks the short column of a label/prose table", () => {
    // The shape from the screenshot that prompted this.
    const rows = tableRows(
      table(
        ["Dimension", "What It Is"],
        [
          ["Inputs & Data Sources", PROSE],
          ["Artifact Generation", PROSE],
        ],
      ),
    )
    expect(columnKinds(rows)).toEqual(["label", "prose"])
  })

  it("marks several short columns in a report table", () => {
    // The Decision Register shape: ids and statuses beside two prose columns.
    const rows = tableRows(
      table(
        ["ID", "Status", "Decision", "Rationale"],
        [
          ["D-001", "open", PROSE, PROSE],
          ["D-002", "closed", PROSE, PROSE],
        ],
      ),
    )
    expect(columnKinds(rows)).toEqual(["label", "label", "prose", "prose"])
  })

  it("sizes a column by its HEADER, not just its values", () => {
    // "done" is a one-word value, but the header above it is 34 chars, so the
    // column is NOT a label — the column can never render narrower than its
    // header (th carries nowrap), and pinning it to the values would set a
    // width the header immediately overrides. Classifying on the header makes
    // this a prose column, which leaves nothing to pin and the whole table
    // untouched.
    const rows = tableRows(
      table(
        ["Implementation status across teams", "Notes"],
        [["done", PROSE]],
      ),
    )
    expect(columnKinds(rows)).toEqual([])
  })

  it("declines when no column is long enough to be crowding the others", () => {
    // All columns comparable — the default layout already balances these, and
    // pinning one would only add dead space.
    const rows = tableRows(
      table(["Name", "Role"], [["Ada", "Engineer"], ["Grace", "Admiral"]]),
    )
    expect(columnKinds(rows)).toEqual([])
  })

  it("declines on a single-column table", () => {
    expect(columnKinds([["Heading"], [PROSE]])).toEqual([])
  })

  it("declines past the column ceiling, where the table scrolls anyway", () => {
    const header = ["a", "b", "c", "d", "e", "f", "g", "h", "i"]
    const body = [["1", "2", "3", "4", "5", "6", "7", "8", PROSE]]
    expect(columnKinds([header, ...body])).toEqual([])
  })

  it("handles a ragged row without crashing", () => {
    // Malformed markdown tables reach the renderer; a short row must not make
    // a column look narrow enough to pin.
    const rows = [["ID", "Notes"], ["D-001"], ["D-002", PROSE]]
    expect(columnKinds(rows)).toEqual(["label", "prose"])
  })

  it("is stable as a streamed table gains rows", () => {
    // Answers stream, so this runs on every token. The classification must not
    // flip once the shape is established, or the columns visibly jitter.
    const header = ["Dimension", "What It Is"]
    const first = columnKinds([header, ["Inputs & Data Sources", PROSE]])
    const later = columnKinds([
      header,
      ["Inputs & Data Sources", PROSE],
      ["Artifact Generation", PROSE],
      ["Skills & Intelligence", PROSE],
    ])
    expect(first).toEqual(["label", "prose"])
    expect(later).toEqual(first)
  })
})

describe("labelColumnClasses", () => {
  it("names each label column by its 1-based position", () => {
    expect(labelColumnClasses(["label", "prose", "label"])).toBe(
      "md-label-col-1 md-label-col-3",
    )
  })

  it("is empty when the classifier declined", () => {
    expect(labelColumnClasses([])).toBe("")
  })
})
