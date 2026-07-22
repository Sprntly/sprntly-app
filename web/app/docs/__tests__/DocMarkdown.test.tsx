// @vitest-environment node
//
// Render smoke test for the docs Markdown mapping. Verifies the semantic HTML
// the docs prose styling hooks onto: GFM tables get wrapped in a horizontally
// scrollable container, blockquotes render (the green callout convention),
// inline code renders as <code>, and external links open safely.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, it, expect } from "vitest"

// DocMarkdown.tsx uses the classic JSX runtime (no `import React`); expose it
// globally so its elements render under renderToStaticMarkup (repo test
// convention, mirrors app/lib/__tests__/inline-md.test.tsx).
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { DocMarkdown } from "../DocMarkdown"

const html = (body: string) =>
  renderToStaticMarkup(React.createElement(DocMarkdown, { body }))

describe("DocMarkdown", () => {
  it("wraps GFM tables in an overflow container", () => {
    const out = html(
      ["| Tool | What it does |", "| --- | --- |", "| `get_prd` | context |"].join(
        "\n",
      ),
    )
    expect(out).toContain('class="docs-table-wrap"')
    expect(out).toContain("<table>")
    expect(out).toContain("<th>Tool</th>")
    expect(out).toContain("get_prd")
  })

  it("renders blockquotes as callouts", () => {
    const out = html("> **Heads up.** This can take up to 3 minutes.")
    expect(out).toContain("<blockquote>")
    expect(out).toContain("<strong>Heads up.</strong>")
  })

  it("renders inline code and lists", () => {
    const out = html("Run `list_tickets`:\n\n- one\n- two")
    expect(out).toContain("<code>list_tickets</code>")
    expect(out).toContain("<ul>")
    expect(out).toContain("<li>one</li>")
  })

  it("opens external links in a new tab with a safe rel", () => {
    const out = html("[api](https://api.sprntly.ai/mcp)")
    expect(out).toContain('href="https://api.sprntly.ai/mcp"')
    expect(out).toContain('target="_blank"')
    expect(out).toContain('rel="noopener noreferrer"')
  })

  it("wraps everything in the prose container", () => {
    expect(html("hello")).toContain('class="docs-prose"')
  })
})
