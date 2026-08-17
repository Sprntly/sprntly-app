// @vitest-environment jsdom
//
// SavedChatMarkdown renders a `skill=="saved-chat"` report's stored body —
// raw markdown — as rich prose: the same react-markdown + remark-gfm pass
// AskReplyBody uses for an ordinary answer, WITHOUT rehype-raw. That absence
// is the whole safety property under test here: raw HTML embedded in the
// markdown source must never become a live tag.
import * as React from "react"
import { cleanup, render } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { SavedChatMarkdown } from "../SavedChatMarkdown"

afterEach(cleanup)

describe("SavedChatMarkdown — rich rendering", () => {
  it("renders a heading as an actual <h1>, not printed '#' text", () => {
    const { container } = render(<SavedChatMarkdown markdown="# Prioritization" />)
    const h1 = container.querySelector("h1")
    expect(h1).toBeTruthy()
    expect(h1?.textContent).toBe("Prioritization")
    expect(container.textContent).not.toContain("# Prioritization")
  })

  it("renders bold text as an actual <strong>, not printed '**' text", () => {
    const { container } = render(<SavedChatMarkdown markdown="Ship **A** first" />)
    const strong = container.querySelector("strong")
    expect(strong).toBeTruthy()
    expect(strong?.textContent).toBe("A")
    expect(container.textContent).not.toContain("**A**")
  })

  it("renders a markdown list as an actual <ul><li>, not printed '-' text", () => {
    const { container } = render(
      <SavedChatMarkdown markdown={"- Ship A first\n- Then B"} />,
    )
    const ul = container.querySelector("ul")
    expect(ul).toBeTruthy()
    const items = Array.from(ul?.querySelectorAll("li") ?? []).map((li) => li.textContent)
    expect(items).toEqual(["Ship A first", "Then B"])
  })

  it("renders a GFM table (remark-gfm) as an actual <table>", () => {
    const md = "| A | B |\n| - | - |\n| 1 | 2 |"
    const { container } = render(<SavedChatMarkdown markdown={md} />)
    expect(container.querySelector("table")).toBeTruthy()
    expect(container.querySelectorAll("td").length).toBe(2)
  })
})

describe("SavedChatMarkdown — XSS safety (no rehype-raw)", () => {
  it("never executes a <script> tag embedded in the saved markdown", () => {
    const { container } = render(
      <SavedChatMarkdown markdown={"before <script>window.__xss = true</script> after"} />,
    )
    // No live <script> element in the rendered tree — react-markdown without
    // rehype-raw prints embedded raw HTML as inert, escaped text instead of
    // mounting it (same behaviour AskReplyBody documents for a chat answer).
    expect(container.querySelector("script")).toBeNull()
    expect(container.textContent).toContain("<script>window.__xss = true</script>")
    expect((window as unknown as { __xss?: boolean }).__xss).toBeUndefined()
  })

  it("never turns a javascript: link into a clickable href", () => {
    const { container } = render(
      <SavedChatMarkdown markdown={"[click me](javascript:alert(1))"} />,
    )
    const anchor = container.querySelector("a")
    expect(anchor).toBeTruthy()
    // react-markdown's default urlTransform allow-lists http(s)/irc(s)/mailto/
    // xmpp only — an unsafe protocol is neutralised to an empty href rather
    // than emitted verbatim.
    expect(anchor?.getAttribute("href")).toBe("")
  })

  it("prints an <img onerror=...> attribute as inert text, never a live handler", () => {
    const { container } = render(
      <SavedChatMarkdown markdown={'before <img src=x onerror="window.__xss2=true"> after'} />,
    )
    const img = container.querySelector("img")
    expect(img).toBeNull()
    expect(container.textContent).toContain('<img src=x onerror="window.__xss2=true">')
    expect((window as unknown as { __xss2?: boolean }).__xss2).toBeUndefined()
  })
})
