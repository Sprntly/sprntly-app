// @vitest-environment jsdom
//
// Unit tests for the v3 HTML PRD Evidence truncation (viewer-only): show the top
// 3 evidence items, fold the rest behind a "View more evidence" link, and strip
// both injected artifacts before the doc is persisted so the STORED PRD keeps
// every evidence item.
import { describe, expect, it, vi } from "vitest"
import {
  EV_MAX_VISIBLE,
  EV_MORE_ID,
  EV_TRUNC_STYLE_ID,
  applyEvidenceTruncation,
  stripEvidenceTruncation,
} from "../prdEvidenceTruncate"

/** Build a minimal PRD-shaped document with an `ul.ev` of `n` evidence items. */
function docWithEvidence(n: number): Document {
  const items = Array.from({ length: n }, (_, i) =>
    `<li><strong>${i + 1}%</strong> claim number ${i + 1}` +
    `<div class="evmeta"><span class="evtype">Data analysis</span></div></li>`,
  ).join("")
  const html =
    `<!DOCTYPE html><html><head></head><body>` +
    `<div class="page" contenteditable="true">` +
    `<div class="eyebrow">Evidence</div><ul class="ev">${items}</ul>` +
    `<div class="eyebrow">Users</div>` +
    `</div></body></html>`
  return new DOMParser().parseFromString(html, "text/html")
}

describe("applyEvidenceTruncation", () => {
  it("does nothing when there are 3 or fewer evidence items", () => {
    const doc = docWithEvidence(EV_MAX_VISIBLE)
    expect(applyEvidenceTruncation(doc, vi.fn())).toBe(false)
    expect(doc.getElementById(EV_MORE_ID)).toBeNull()
    expect(doc.getElementById(EV_TRUNC_STYLE_ID)).toBeNull()
  })

  it("injects the link + hide-style when there are more than 3 items", () => {
    const doc = docWithEvidence(5)
    expect(applyEvidenceTruncation(doc, vi.fn())).toBe(true)

    const link = doc.getElementById(EV_MORE_ID)
    expect(link).not.toBeNull()
    expect(link!.textContent).toBe("View more evidence")
    expect(link!.getAttribute("contenteditable")).toBe("false")
    // Sits directly AFTER the evidence list (i.e. "below it").
    expect(doc.querySelector("ul.ev")!.nextElementSibling).toBe(link)

    // The hide-rule keeps only the top 3 items visible.
    const style = doc.getElementById(EV_TRUNC_STYLE_ID)
    expect(style).not.toBeNull()
    expect(style!.textContent).toContain(
      `ul.ev > li:nth-child(n+${EV_MAX_VISIBLE + 1}){display:none`,
    )
    // All items remain in the DOM — hiding is CSS-only, never a deletion.
    expect(doc.querySelectorAll("ul.ev > li").length).toBe(5)
  })

  it("invokes onViewMore (and prevents default) when the link is clicked", () => {
    const doc = docWithEvidence(4)
    const onViewMore = vi.fn()
    applyEvidenceTruncation(doc, onViewMore)

    const link = doc.getElementById(EV_MORE_ID)!
    // The parsed document shares the jsdom realm, so a global MouseEvent
    // dispatches fine (its own defaultView is null — DOMParser docs aren't
    // browsing contexts).
    const evt = new MouseEvent("click", { bubbles: true, cancelable: true })
    link.dispatchEvent(evt)

    expect(onViewMore).toHaveBeenCalledTimes(1)
    expect(evt.defaultPrevented).toBe(true)
  })

  it("is idempotent — a second call does not inject a duplicate link", () => {
    const doc = docWithEvidence(6)
    expect(applyEvidenceTruncation(doc, vi.fn())).toBe(true)
    expect(applyEvidenceTruncation(doc, vi.fn())).toBe(false)
    expect(doc.querySelectorAll(`#${EV_MORE_ID}`).length).toBe(1)
  })

  it("no-ops on a document without an evidence list", () => {
    const doc = new DOMParser().parseFromString(
      "<!DOCTYPE html><html><body><p>no evidence</p></body></html>",
      "text/html",
    )
    expect(applyEvidenceTruncation(doc, vi.fn())).toBe(false)
  })
})

describe("stripEvidenceTruncation", () => {
  it("removes both injected artifacts so the persisted clone is byte-clean", () => {
    const doc = docWithEvidence(5)
    applyEvidenceTruncation(doc, vi.fn())

    // Mirror PrdHtmlView.readDoc: strip on a CLONE, leaving the live doc intact.
    const clone = doc.documentElement.cloneNode(true) as HTMLElement
    stripEvidenceTruncation(clone)

    expect(clone.querySelector(`#${EV_MORE_ID}`)).toBeNull()
    expect(clone.querySelector(`#${EV_TRUNC_STYLE_ID}`)).toBeNull()
    // Every evidence item survives in the persisted output.
    expect(clone.querySelectorAll("ul.ev > li").length).toBe(5)
    // The live document still shows the affordance (only the clone was stripped).
    expect(doc.getElementById(EV_MORE_ID)).not.toBeNull()
  })

  it("is safe to call when nothing was injected", () => {
    const doc = docWithEvidence(2)
    const clone = doc.documentElement.cloneNode(true) as HTMLElement
    expect(() => stripEvidenceTruncation(clone)).not.toThrow()
  })
})
