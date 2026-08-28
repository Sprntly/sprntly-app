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
import {
  execDocumentCommand,
  UNSUPPORTED_DOCUMENT_COMMANDS,
} from "../../../../lib/documentToolbarExec"

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
  let editor: {
    commands: {
      setTextSelection: (r: { from: number; to: number }) => void
      insertContentAt: (pos: number, content: string) => void
    }
  } | null = null
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
  return { ...utils, onChange, selectAll, setCaret, getEditor: () => editor! }
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

  it("keeps a table a table, rather than flattening it into a paragraph", async () => {
    // THE DEFECT: a report's summary grid arrived as one run-on paragraph —
    // "ThemeAccounts Raising ItNature of Signal…", every cell concatenated with
    // no separator. Nothing was wrong with the data: the row held real <table>
    // markup, and `report_markdown` converts the engines' markdown grids with
    // markdown's `tables` extension. The schema had no table node, and
    // ProseMirror drops a node it cannot place while KEEPING ITS TEXT.
    //
    // Asserted on the cells rather than on the tag alone, because the failure
    // was never a missing <table> at the reader — it was two cells becoming
    // one string, which is what makes the grid unreadable.
    const html =
      "<table><thead><tr><th>Theme</th><th>Accounts</th></tr></thead>" +
      "<tbody><tr><td>Async exercises</td><td>11</td></tr></tbody></table>"
    const { container } = await mount({ initialHtml: html })
    const pm = container.querySelector(".tiptap")!
    expect(pm.querySelectorAll("table")).toHaveLength(1)
    expect([...pm.querySelectorAll("th")].map((c) => c.textContent)).toEqual([
      "Theme", "Accounts",
    ])
    expect([...pm.querySelectorAll("td")].map((c) => c.textContent)).toEqual([
      "Async exercises", "11",
    ])
    // And no paragraph is carrying the cells instead — the flattened shape.
    // (`textContent` is not the instrument for this: it concatenates cell text
    // with no separator for a REAL table too, so it reads the same either way.)
    expect([...pm.querySelectorAll("p")].map((n) => n.textContent)).not.toContain(
      "ThemeAccountsAsync exercises11",
    )
  })

  it("keeps the table when a real edit is saved back", async () => {
    // The display bug was the visible half. The other half is destructive: an
    // update serializes what the SCHEMA kept, so with no table node the first
    // genuine keystroke anywhere in the document wrote the flattened body over
    // the stored one — one edit from losing the grid for good.
    const { onChange, container, getEditor } = await mount({
      initialHtml:
        "<p>intro</p><table><tbody><tr><td>cell a</td><td>cell b</td></tr></tbody></table>",
    })
    const wrapper = container.querySelector("[data-doc-editor]") as HTMLElement
    wrapper.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true }))
    getEditor().commands.insertContentAt(1, "x")
    await waitFor(() => expect(onChange).toHaveBeenCalled())
    const saved = onChange.mock.calls.at(-1)![0] as string
    expect(saved).toContain("<table")
    expect(saved).toContain("cell a")
    expect(saved).toContain("cell b")
    expect(saved).not.toContain("cell acell b")
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

describe("opening a document is not editing it", () => {
  it("emits nothing for its own normalization of the stored HTML", async () => {
    // THE DEFECT, measured on staging: opening document 9 twice took it from
    // version 3 to version 5 with nobody typing. TipTap re-serializes the
    // stored HTML into its schema, and that round trip is not byte-identical
    // to what the sanitizer wrote — so the resulting update looked exactly
    // like a keystroke and the save layer wrote it.
    //
    // In a SHARED library that is three harms at once: the row says "Edited
    // just now" by whoever merely READ it, `updated_by` names them, and the
    // version bump makes the next save by the colleague who is genuinely
    // typing fail its compare-and-set.
    const { onChange } = await mount({
      // Deliberately in a shape TipTap will re-serialize differently.
      initialHtml: "<h1>Title</h1><p>body</p><hr>",
    })
    await new Promise((r) => setTimeout(r, 50))
    expect(onChange).not.toHaveBeenCalled()
  })

  it("still emits for a real edit that only changes formatting", async () => {
    // The other half: the gate must not swallow a genuine edit. A toolbar
    // click is one user path; the seven toolbar cases below are the same
    // property at more angles.
    const { onChange, container, selectAll } = await mount({
      initialHtml: "<p>hello</p>",
    })
    onChange.mockClear()
    selectAll()
    // Bold BY NAME, not "the first button in the bar" — the bar's first button
    // is Undo now that it carries the same controls as the panel's.
    fireEvent.click(container.querySelector('[data-testid="doc-bold"]')!)
    await waitFor(() => expect(onChange).toHaveBeenCalled())
    expect(onChange.mock.calls.at(-1)?.[0]).toContain("<strong>")
  })

  // ── The edit paths that fire NEITHER keydown NOR click ────────────────────
  //
  // Found by review, and they are the ones that make a too-narrow gate
  // dangerous: the user watches their edit appear, and it is never saved. The
  // save indicator stays `idle`, so nothing warns them — and because these
  // paths also leave `bodyDirtyRef` false in both consumers, a later "Keep
  // mine" on a conflict would discard the edits too.
  it.each([
    // Right-click a red-underlined word and pick the correction; dictation; an
    // Android suggestion-strip insertion. All arrive as beforeinput/input.
    ["a spellcheck correction or dictation", "beforeinput"],
    // Right-click -> Cut (or Delete). A secondary-button press fires
    // mousedown/contextmenu/mouseup — never `click`.
    ["a context-menu cut", "cut"],
    // IME composition, which fires no keydown for the composed text.
    ["an IME composition", "compositionstart"],
  ])("saves %s", async (_label, eventName) => {
    const { onChange, container, getEditor } = await mount({ initialHtml: "<p>hello</p>" })
    onChange.mockClear()
    const pm = container.querySelector(".tiptap") as HTMLElement

    // ONLY this event marks interaction — the edit itself is applied through
    // the editor's own command, exactly as the browser would after the event.
    // Driving it with a toolbar click instead would mark interaction via
    // `click` and the test would pass with the listener removed.
    pm.dispatchEvent(new Event(eventName, { bubbles: true }))
    getEditor().commands.insertContentAt(1, "X")

    await waitFor(() => expect(onChange).toHaveBeenCalled())
  })

  // ── The bar that is not inside this component ─────────────────────────────
  //
  // Both panel hosts pin `PrdToolbar` OUTSIDE the wrapper the listeners above
  // are attached to — the document tab renders it at the top of the panel, the
  // report portals it into a slot elsewhere in the tree — and drive this editor
  // through `execDocumentCommand`. So none of the events above ever fire for a
  // toolbar click, and the gate swallowed the edit: the text changed on screen,
  // the status pill went on reading "Saved", and the formatting was gone on
  // reopen with nothing anywhere saying so.
  //
  // `hideToolbar` and NO event on the editor, because that is the shape of the
  // defect. A click inside the document first would mark interaction and the
  // test would pass with the fix reverted.
  it.each([
    ["bold", undefined, "<strong>"],
    ["italic", undefined, "<em>"],
    ["formatBlock", "h2", "<h2>"],
    ["insertUnorderedList", undefined, "<ul>"],
  ])("saves a %s from a toolbar mounted outside this component", async (
    cmd, value, expected,
  ) => {
    const { onChange, getEditor } = await mount({
      initialHtml: "<p>hello</p>",
      hideToolbar: true,
    })
    onChange.mockClear()
    const editor = getEditor() as unknown as Parameters<typeof execDocumentCommand>[0]
    ;(editor as unknown as { commands: { setTextSelection: (r: unknown) => void } })
      .commands.setTextSelection({ from: 1, to: 6 })

    expect(execDocumentCommand(editor, cmd, value)).toBe(true)

    await waitFor(() => expect(onChange).toHaveBeenCalled())
    expect(onChange.mock.calls.at(-1)?.[0]).toContain(expected)
  })

  it("undo from that toolbar saves too", async () => {
    // Undo and redo dispatch their own transactions from the history plugin
    // rather than the chain's, which is why the marker is sticky per editor
    // and not carried on the transaction.
    const { onChange, getEditor } = await mount({
      initialHtml: "<p>hello</p>",
      hideToolbar: true,
    })
    const editor = getEditor() as unknown as Parameters<typeof execDocumentCommand>[0]
    ;(editor as unknown as { commands: { setTextSelection: (r: unknown) => void } })
      .commands.setTextSelection({ from: 1, to: 6 })
    execDocumentCommand(editor, "bold")
    onChange.mockClear()

    execDocumentCommand(editor, "undo")

    await waitFor(() => expect(onChange).toHaveBeenCalled())
    expect(onChange.mock.calls.at(-1)?.[0]).not.toContain("<strong>")
  })

  it("a document nobody touched is still not saved by another one's toolbar", async () => {
    // The marker is per editor, not per module: opening a second document must
    // not inherit the first one's "this has been edited" state, or merely
    // opening it would bump its version — the defect the gate exists to stop.
    const first = await mount({ initialHtml: "<p>one</p>", hideToolbar: true })
    execDocumentCommand(
      first.getEditor() as unknown as Parameters<typeof execDocumentCommand>[0],
      "bold",
    )
    const second = await mount({
      initialHtml: "<h1>Title</h1><p>two</p><hr>",
      hideToolbar: true,
    })
    await new Promise((r) => setTimeout(r, 50))
    expect(second.onChange).not.toHaveBeenCalled()
  })
})

describe("one bar, one set of controls, whatever the artifact is", () => {
  // The report this closes: "table is not in report and full page doc editor.
  // Indent, outdent, align x3 is also not in those. All the controls should be
  // in all of the toolbar." They were omitted because this editor's schema had
  // no extension behind them — so the omission was honest, and the fix is the
  // extensions, not the buttons.

  //: Every command `PrdToolbar` can emit, with what it must leave behind.
  const COMMANDS: [string, string | undefined, RegExp][] = [
    ["bold", undefined, /<strong>/],
    ["italic", undefined, /<em>/],
    ["underline", undefined, /<u>/],
    ["strikeThrough", undefined, /<s>/],
    ["insertUnorderedList", undefined, /<ul>/],
    ["insertOrderedList", undefined, /<ol>/],
    ["insertHorizontalRule", undefined, /<hr>/],
    ["formatBlock", "h2", /<h2>/],
    ["formatBlock", "blockquote", /<blockquote>/],
    ["formatBlock", "pre", /<pre>/],
    ["createLink", "https://sprntly.ai", /<a [^>]*href="https:\/\/sprntly\.ai"/],
    ["insertTable", undefined, /<table/],
    ["justifyCenter", undefined, /text-align: center/],
    ["justifyRight", undefined, /text-align: right/],
    ["indent", undefined, /margin-left: 24px/],
    ["fontName", "Georgia, 'Times New Roman', serif", /font-family: Georgia/],
    ["fontSize", "19px", /font-size: 19px/],
    ["foreColor", "#B42318", /color: rgb\(180, 35, 24\)|color: #B42318/],
    ["hiliteColor", "#FEF3C7", /background-color/],
  ]

  it.each(COMMANDS)("answers %s", async (cmd, value, expected) => {
    const { onChange, getEditor } = await mount({
      initialHtml: "<p>hello world</p>",
      hideToolbar: true,
    })
    const editor = getEditor() as unknown as Parameters<typeof execDocumentCommand>[0]
    ;(editor as unknown as { commands: { setTextSelection: (r: unknown) => void } })
      .commands.setTextSelection({ from: 1, to: 6 })
    onChange.mockClear()

    expect(execDocumentCommand(editor, cmd, value), `${cmd} was not handled`).toBe(true)

    await waitFor(() => expect(onChange).toHaveBeenCalled())
    expect(onChange.mock.calls.at(-1)?.[0]).toMatch(expected)
  })

  it("hides nothing from this host any more", () => {
    // The `omit` mechanism stays — a host that genuinely cannot run a command
    // should leave it out rather than render it inert — but this editor now
    // answers everything, so the set is empty.
    expect([...UNSUPPORTED_DOCUMENT_COMMANDS]).toEqual([])
  })

  it("indents a list by nesting the item, not by nudging a margin", async () => {
    // What every editor does, and what <ul><li><ul> is for. A margin on a list
    // item would look the same and mean nothing to anything reading the HTML.
    const { onChange, getEditor } = await mount({
      initialHtml: "<ul><li><p>one</p></li><li><p>two</p></li></ul>",
      hideToolbar: true,
    })
    const editor = getEditor() as unknown as Parameters<typeof execDocumentCommand>[0]
    ;(editor as unknown as { commands: { setTextSelection: (r: unknown) => void } })
      .commands.setTextSelection({ from: 9, to: 9 })

    expect(execDocumentCommand(editor, "indent")).toBe(true)

    await waitFor(() => expect(onChange).toHaveBeenCalled())
    const html = onChange.mock.calls.at(-1)?.[0] as string
    expect(html).toMatch(/<ul>[\s\S]*<ul>/)
    expect(html).not.toMatch(/margin-left/)
  })

  it("outdents back to flush, and no further", async () => {
    const { getEditor } = await mount({
      initialHtml: '<p style="margin-left: 24px">indented</p>',
      hideToolbar: true,
    })
    const editor = getEditor() as unknown as Parameters<typeof execDocumentCommand>[0]
    ;(editor as unknown as { commands: { setTextSelection: (r: unknown) => void } })
      .commands.setTextSelection({ from: 1, to: 1 })

    expect(execDocumentCommand(editor, "outdent"), "the stored indent was not read back").toBe(true)
    // Already flush: nothing to do, and it says so rather than reporting an
    // edit that would mark the document dirty for no reason.
    expect(execDocumentCommand(editor, "outdent")).toBe(false)
  })

  it("stops indenting before the text marches off the page", async () => {
    const { getEditor } = await mount({ initialHtml: "<p>x</p>", hideToolbar: true })
    const editor = getEditor() as unknown as Parameters<typeof execDocumentCommand>[0]
    ;(editor as unknown as { commands: { setTextSelection: (r: unknown) => void } })
      .commands.setTextSelection({ from: 1, to: 1 })
    for (let i = 0; i < 6; i++) expect(execDocumentCommand(editor, "indent")).toBe(true)
    // There is no horizontal scroll on a document page: past the ceiling the
    // text would be unreachable except by outdenting blind.
    expect(execDocumentCommand(editor, "indent")).toBe(false)
  })

  it("opens the same colour grid the panel's bar opens", async () => {
    const { container, getByTestId, onChange, selectAll } = await mount()
    selectAll()
    fireEvent.click(getByTestId("doc-color"))

    const swatches = container.querySelectorAll(".prd-colorgrid-swatch")
    expect(swatches.length).toBeGreaterThanOrEqual(40)

    fireEvent.click(getByTestId("doc-color-grid-ff0000"))

    await waitFor(() => expect(onChange).toHaveBeenCalled())
    expect(onChange.mock.calls.at(-1)?.[0]).toMatch(/color: rgb\(255, 0, 0\)|#FF0000/i)
    // Chosen means chosen: the popover closes rather than sitting over the text.
    expect(container.querySelector('[data-testid="doc-color-menu"]')).toBeNull()
  })

  it("offers the same controls on its own bar", async () => {
    // The full-page editor draws its own toolbar rather than PrdToolbar, so
    // parity has to be asserted here too — this is the half the report named.
    const { container } = await mount()
    for (const testId of [
      "doc-undo", "doc-redo", "doc-insertTable", "doc-insertHorizontalRule",
      "doc-justifyLeft", "doc-justifyCenter", "doc-justifyRight",
      "doc-indent", "doc-outdent",
    ]) {
      expect(
        container.querySelector(`[data-testid="${testId}"]`),
        `the full-page bar is missing ${testId}`,
      ).not.toBeNull()
    }
  })
})

describe("the schema registers each extension exactly once", () => {
  it("warns about no duplicate extension name", async () => {
    // `@tiptap/extension-underline` was registered alongside StarterKit v3,
    // which already ships one, so every mount logged "Duplicate extension
    // names found: ['underline']" — the state TipTap itself calls out as
    // leading to issues. Link was disabled in the StarterKit config for
    // exactly this reason; underline was the one that got missed.
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {})
    try {
      await mount({ initialHtml: "<p>hello</p>" })
      const messages = warn.mock.calls.map((c) => c.join(" "))
      expect(messages.filter((m) => m.includes("Duplicate extension names"))).toEqual([])
    } finally {
      warn.mockRestore()
    }
  })

  it("still underlines, on StarterKit's own mark", async () => {
    const { container, onChange, selectAll } = await mount({ initialHtml: "<p>hello</p>" })
    selectAll()
    fireEvent.click(container.querySelector('[data-testid="doc-underline"]')!)
    await waitFor(() => expect(onChange).toHaveBeenCalled())
    expect(onChange.mock.calls.at(-1)?.[0]).toContain("<u>")
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
