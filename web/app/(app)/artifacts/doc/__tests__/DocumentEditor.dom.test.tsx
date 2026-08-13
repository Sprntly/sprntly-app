// @vitest-environment jsdom
//
// The editor, driven for real: mount it, click the toolbar, and read what HTML
// comes out. Asserting on the SERIALIZED OUTPUT rather than on ProseMirror's
// internal state is deliberate — the output is what gets stored, what the
// sanitizer sees, and what the reader eventually renders, so it is the only
// thing that can actually be wrong for a user.
//
// jsdom does not implement the layout APIs ProseMirror asks for, so a few are
// stubbed below. They are stubbed rather than mocked-away because ProseMirror
// only calls them for cursor geometry — behaviour these tests do not assert.
import * as React from "react"
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react"
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { DocumentEditor } from "../DocumentEditor"

beforeAll(() => {
  // ProseMirror measures the caret; jsdom returns nothing for these.
  if (!Range.prototype.getBoundingClientRect) {
    Range.prototype.getBoundingClientRect = () =>
      ({ top: 0, left: 0, bottom: 0, right: 0, width: 0, height: 0 }) as DOMRect
  }
  if (!Range.prototype.getClientRects) {
    Range.prototype.getClientRects = () =>
      ({ length: 0, item: () => null, [Symbol.iterator]: function* () {} }) as unknown as DOMRectList
  }
})

afterEach(cleanup)

/** Mount and wait for TipTap to finish creating the view (it is async because
 *  `immediatelyRender` is off — see DocumentEditor's note on SSR). */
async function mount(props: Partial<React.ComponentProps<typeof DocumentEditor>> = {}) {
  const onChange = vi.fn()
  // The editor instance is captured so tests can make a REAL selection with
  // `setTextSelection`. Driving it with a DOM Range instead does not work:
  // ProseMirror keeps its own selection state and only syncs from the DOM on
  // events jsdom does not produce, so a Range-based "selection" leaves the
  // editor's own selection empty and every toolbar command applies to nothing.
  // That reads as "bold is broken" when bold is fine.
  let editor: { commands: { setTextSelection: (r: { from: number; to: number }) => void } } | null = null
  const utils = render(
    <DocumentEditor
      initialHtml={props.initialHtml ?? "<p>hello</p>"}
      onChange={onChange}
      onReady={(e) => { editor = e as unknown as typeof editor }}
      {...props}
    />,
  )
  await waitFor(() => expect(utils.container.querySelector(".tiptap")).not.toBeNull())
  await waitFor(() => expect(editor).not.toBeNull())
  const setCaret = (pos: number) => {
    editor!.commands.setTextSelection({ from: pos, to: pos })
  }
  const selectAll = () => {
    const pm = utils.container.querySelector(".tiptap") as HTMLElement
    const len = (pm.textContent ?? "").length
    editor!.commands.setTextSelection({ from: 1, to: len + 1 })
  }
  return { ...utils, onChange, selectAll, setCaret }
}

describe("the editor renders the document it is given", () => {
  it("shows the stored HTML", async () => {
    const { container } = await mount({ initialHtml: "<p>hello world</p>" })
    expect(container.querySelector(".tiptap")?.textContent).toContain("hello world")
  })

  it("keeps the formatting the sanitizer allows", async () => {
    // A round trip through the editor must not quietly drop a mark the server
    // would have kept — that would make opening a document destructive.
    const html = "<h2>Title</h2><p><strong>bold</strong> <em>it</em> <u>u</u> <s>s</s></p><ul><li>one</li></ul>"
    const { container } = await mount({ initialHtml: html })
    const out = container.querySelector(".tiptap")!.innerHTML
    expect(out).toContain("<h2>")
    expect(out).toContain("<strong>")
    expect(out).toContain("<em>")
    expect(out).toContain("<u>")
    expect(out).toContain("<s>")
    expect(out).toContain("<li>")
  })

  it("renders a font/colour span from stored styles", async () => {
    const { container } = await mount({
      initialHtml: '<p><span style="font-family: Georgia; color: #B42318">styled</span></p>',
    })
    const out = container.querySelector(".tiptap")!.innerHTML.toLowerCase()
    expect(out).toContain("georgia")
    // The COLOUR survives; its notation does not. A style attribute is
    // normalized by the DOM, so `#B42318` comes back as `rgb(180, 35, 24)`.
    // Asserted on the channel values, because the stored notation is the
    // browser's business — and both forms pass the server's allowlist, which
    // gates the PROPERTY (`color`) and only rejects values that fetch or
    // execute.
    expect(out).toMatch(/#b42318|rgb\(\s*180,\s*35,\s*24\s*\)/)
  })
})

describe("the toolbar", () => {
  it("is shown when editable and hidden when not", async () => {
    const { container, unmount } = await mount({ editable: true })
    expect(container.querySelector("[data-doc-toolbar]")).not.toBeNull()
    unmount()

    const ro = await mount({ editable: false })
    expect(ro.container.querySelector("[data-doc-toolbar]")).toBeNull()
  })

  it("offers exactly the controls the requirement names", async () => {
    // "bold, italic, change fonts etc" — plus the ones a document needs to be
    // a document rather than a styled paragraph.
    const { getByTestId } = await mount()
    for (const id of [
      "doc-bold", "doc-italic", "doc-underline", "doc-strike",
      "doc-heading", "doc-font", "doc-size", "doc-color", "doc-highlight",
      "doc-bulletList", "doc-orderedList", "doc-blockquote", "doc-codeBlock",
      "doc-link", "doc-clear-format",
    ]) {
      expect(getByTestId(id)).toBeTruthy()
    }
  })

  it("does NOT offer a heading level the server would strip", async () => {
    // h5/h6 would be unwrapped on save; a button for one is a promise the
    // storage layer breaks.
    const { getByTestId } = await mount()
    const options = Array.from(
      (getByTestId("doc-heading") as HTMLSelectElement).options,
    ).map((o) => o.value)
    expect(options).toEqual(["p", "1", "2", "3", "4"])
  })

  it("bold applies to the selection", async () => {
    const { getByTestId, onChange, selectAll } = await mount({ initialHtml: "<p>hello</p>" })
    selectAll()
    fireEvent.click(getByTestId("doc-bold"))
    await waitFor(() => {
      expect(onChange.mock.calls.at(-1)?.[0] ?? "").toContain("<strong>")
    })
  })

  it("italic and underline apply too", async () => {
    const { getByTestId, onChange, selectAll } = await mount({ initialHtml: "<p>hello</p>" })
    selectAll()
    fireEvent.click(getByTestId("doc-italic"))
    fireEvent.click(getByTestId("doc-underline"))
    await waitFor(() => {
      const html = onChange.mock.calls.at(-1)?.[0] ?? ""
      expect(html).toContain("<em>")
      expect(html).toContain("<u>")
    })
  })

  it("a heading choice changes the block", async () => {
    const { getByTestId, onChange, selectAll } = await mount({ initialHtml: "<p>hello</p>" })
    selectAll()
    fireEvent.change(getByTestId("doc-heading"), { target: { value: "2" } })
    await waitFor(() => {
      expect(onChange.mock.calls.at(-1)?.[0] ?? "").toContain("<h2>")
    })
  })

  it("a font choice writes a font-family span — the 'change fonts' requirement", async () => {
    const { getByTestId, onChange, selectAll } = await mount({ initialHtml: "<p>hello</p>" })
    selectAll()
    fireEvent.change(getByTestId("doc-font"), {
      target: { value: "Georgia, 'Times New Roman', serif" },
    })
    await waitFor(() => {
      const html = (onChange.mock.calls.at(-1)?.[0] ?? "").toLowerCase()
      expect(html).toContain("font-family")
      expect(html).toContain("georgia")
    })
  })

  it("a size choice writes a font-size span", async () => {
    const { getByTestId, onChange, selectAll } = await mount({ initialHtml: "<p>hello</p>" })
    selectAll()
    fireEvent.change(getByTestId("doc-size"), { target: { value: "19px" } })
    await waitFor(() => {
      expect((onChange.mock.calls.at(-1)?.[0] ?? "").toLowerCase()).toContain("font-size")
    })
  })

  it("a list choice writes real list markup", async () => {
    const { getByTestId, onChange, selectAll } = await mount({ initialHtml: "<p>one</p>" })
    selectAll()
    fireEvent.click(getByTestId("doc-bulletList"))
    await waitFor(() => {
      const html = onChange.mock.calls.at(-1)?.[0] ?? ""
      expect(html).toContain("<ul>")
      expect(html).toContain("<li>")
    })
  })
})

describe("changes reach the caller", () => {
  it("onChange fires with the serialized document", async () => {
    const { getByTestId, onChange, selectAll } = await mount({ initialHtml: "<p>x</p>" })
    selectAll()
    fireEvent.change(getByTestId("doc-heading"), { target: { value: "3" } })
    await waitFor(() => expect(onChange).toHaveBeenCalled())
    // A string of HTML, not a ProseMirror document — the save layer stores it
    // verbatim.
    expect(typeof onChange.mock.calls.at(-1)![0]).toBe("string")
  })
})

// ─── Regression from review of #1161 ────────────────────────────────────────

describe("the toolbar follows the caret", () => {
  it("reflects the block the cursor is in, without typing", async () => {
    // THE BUG THIS PINS: TipTap v3's `useEditor` does not re-render on a
    // transaction by default, and the toolbar reads `editor.isActive(...)`
    // during render. So moving the caret into an existing <h2> left the style
    // select showing "Body" — and choosing "Heading 2" then TOGGLED THE
    // HEADING OFF, turning it into a paragraph. A control that does the
    // opposite of its label is worse than a missing one.
    const { getByTestId, setCaret } = await mount({
      initialHtml: "<h2>a heading</h2><p>a paragraph</p>",
    })
    const value = () => (getByTestId("doc-heading") as HTMLSelectElement).value

    // Inside the <h2> (offset 1 is its first text position).
    setCaret(2)
    await waitFor(() => expect(value()).toBe("2"))

    // ...and back into the paragraph, which begins after the heading node.
    setCaret(15)
    await waitFor(() => expect(value()).toBe("p"))
  })

  it("shows bold as active when the caret sits in bold text", async () => {
    const { getByTestId, setCaret } = await mount({
      initialHtml: "<p><strong>bold</strong> plain</p>",
    })
    setCaret(2)
    await waitFor(() =>
      expect(getByTestId("doc-bold").getAttribute("data-active")).toBe("true"),
    )
    setCaret(8)
    await waitFor(() =>
      expect(getByTestId("doc-bold").getAttribute("data-active")).toBe("false"),
    )
  })
})
