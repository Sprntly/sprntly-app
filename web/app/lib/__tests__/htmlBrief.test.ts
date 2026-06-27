import { describe, expect, it } from "vitest"
import { looksLikeHtmlBrief, stripHtmlCodeFence } from "../htmlBrief"

describe("stripHtmlCodeFence", () => {
  it("strips a ```html fence around the document", () => {
    const out = stripHtmlCodeFence("```html\n<!DOCTYPE html>\n<div>x</div>\n```")
    expect(out).toBe("<!DOCTYPE html>\n<div>x</div>")
    expect(out).not.toContain("```")
  })

  it("strips a fence with no language", () => {
    expect(stripHtmlCodeFence("```\nhello\n```")).toBe("hello")
  })

  it("leaves unfenced HTML unchanged", () => {
    const html = '<div class="wrap"><h1>x</h1></div>'
    expect(stripHtmlCodeFence(html)).toBe(html)
  })

  it("tolerates surrounding whitespace", () => {
    expect(stripHtmlCodeFence("\n  ```html\n<p>hi</p>\n```  \n")).toBe("<p>hi</p>")
  })

  it("does not strip a fence that only opens mid-document", () => {
    const s = "<div>a</div>\n```\ncode\n```\n<div>b</div>"
    expect(stripHtmlCodeFence(s)).toBe(s)
  })
})

describe("looksLikeHtmlBrief", () => {
  it.each([
    "<!doctype html><html></html>",
    '  <div class="wrap"></div>',
    "<meta charset=\"utf-8\">",
    // fenced HTML is still detected (the fence is unwrapped first)
    "```html\n<!DOCTYPE html><div></div>\n```",
  ])("true for HTML (incl. fenced) %s", (s) => {
    expect(looksLikeHtmlBrief(s)).toBe(true)
  })

  it.each([":::hero\n[]\n:::", "# Heading", "", null, undefined])(
    "false for non-HTML %s",
    (s) => {
      expect(looksLikeHtmlBrief(s as string | null | undefined)).toBe(false)
    },
  )
})
