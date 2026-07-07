import { describe, expect, it } from "vitest"
import {
  looksLikeHtmlBrief,
  stripHtmlCodeFence,
  stripHypothesisSection,
} from "../htmlBrief"

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

describe("stripHypothesisSection", () => {
  const HYP_SECTION =
    '<section>\n  <p class="kicker o">HYPOTHESIS → INPUT TO PRD</p>\n' +
    '  <div class="hyp"><h4>Value-driven hypothesis</h4>' +
    '<p class="stmt">We believe that by <span class="b">X</span>, users will <span class="x">Y</span>, which drives <span class="v">Z</span>.</p>' +
    '<p class="test"><b>Behavior:</b> more Y. Metric: retention; guardrails: none.</p></div>\n</section>'

  it("removes the <section> wrapping the hypothesis div", () => {
    const html = `<div class="wrap"><section><h2>Convergence</h2></section>${HYP_SECTION}</div>`
    const out = stripHypothesisSection(html)
    expect(out).not.toContain("hyp")
    expect(out).not.toContain("We believe that by")
    expect(out).not.toContain("INPUT TO PRD")
    // preceding content is untouched
    expect(out).toContain("<h2>Convergence</h2>")
    expect(out).toBe('<div class="wrap"><section><h2>Convergence</h2></section></div>')
  })

  it("strips an HTML comment marker preceding the section", () => {
    const html = `<div class="wrap"><section><p>keep</p></section>\n  <!-- hypothesis -->\n  ${HYP_SECTION}</div>`
    const out = stripHypothesisSection(html)
    expect(out).not.toContain("hyp")
    expect(out).not.toContain("hypothesis")
    expect(out).toContain("<p>keep</p>")
  })

  it("falls back to a bare hypothesis div with no section wrapper", () => {
    const html =
      '<div class="wrap"><p>keep</p><div class="hyp"><h4>Hypothesis</h4><p>We believe X.</p></div></div>'
    const out = stripHypothesisSection(html)
    expect(out).not.toContain("hyp")
    expect(out).not.toContain("We believe X")
    expect(out).toContain("<p>keep</p>")
  })

  it("leaves a brief with no hypothesis unchanged", () => {
    const html = '<div class="wrap"><section><h2>Evidence</h2><p>data</p></section></div>'
    expect(stripHypothesisSection(html)).toBe(html)
  })

  it("does not touch earlier sections when matching the hypothesis one", () => {
    const html = `<div class="wrap"><section class="finding"><p>finding one</p></section>${HYP_SECTION}</div>`
    const out = stripHypothesisSection(html)
    expect(out).toContain('<section class="finding"><p>finding one</p></section>')
    expect(out).not.toContain('class="hyp"')
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
