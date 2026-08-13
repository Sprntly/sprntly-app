// @vitest-environment jsdom
//
// FormatPreviewModal: the two-pane diagnostic. Three rules it exists to keep —
//   * all three mapping blocks render their EMPTY copy rather than vanishing;
//     an omitted block reads as "nothing to report" when it means "no data";
//   * a 404 is not-found, never "you don't have access";
//   * it traps focus, returns it to the opener, and closes on Escape. No other
//     modal in the repo traps; this one is a large two-pane surface where
//     tabbing out strands the user behind the overlay.
//
// Matchers: native DOM only — NO @testing-library/jest-dom (repo convention).
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const { ApiErrorCls, previewMock } = vi.hoisted(() => {
  class ApiErrorCls extends Error {
    status: number
    body: unknown
    constructor(status: number, body: unknown) {
      super(`api ${status}`)
      this.status = status
      this.body = body
    }
  }
  return { ApiErrorCls, previewMock: vi.fn() }
})

vi.mock("../../../lib/api", () => ({
  ApiError: ApiErrorCls,
  artifactTemplatesApi: { preview: previewMock },
}))

import {
  FormatPreviewModal,
  FormatPreviewModalView,
  ghostHtmlPlaceholders,
  previewSrcDoc,
} from "../FormatPreviewModal"

const EMPTY_MAP = { sections: [], unmapped_house: [], extra_sections: [] }

function payload(over: Record<string, unknown> = {}) {
  return {
    id: "t1",
    name: "Acme PRD v3",
    artifact_type: "prd",
    compile_status: "ready",
    compile_notes: [],
    format: "html",
    body: "<h1>{{title}}</h1>",
    section_map: EMPTY_MAP,
    ...over,
  }
}

function mount(over: Partial<React.ComponentProps<typeof FormatPreviewModal>> = {}) {
  const onClose = vi.fn()
  const onActivate = vi.fn()
  const onNotFound = vi.fn()
  const r = render(
    React.createElement(FormatPreviewModal, {
      templateId: "t1",
      name: "Acme PRD v3",
      canActivate: true,
      activateBlockedReason: null,
      activating: false,
      onActivate,
      onNotFound,
      onClose,
      ...over,
    }),
  )
  return { ...r, onClose, onActivate, onNotFound }
}

beforeEach(() => {
  previewMock.mockResolvedValue(payload())
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("pure helpers", () => {
  it("ghosts {{placeholders}} without touching markup inside tags", () => {
    // Rewriting blind would turn alt="{{title}}" into mangled markup.
    expect(ghostHtmlPlaceholders("<p>Hello {{name}}</p>")).toBe(
      '<p>Hello <span class="afmt-ph">{{name}}</span></p>',
    )
    expect(ghostHtmlPlaceholders('<img alt="{{title}}">')).toBe('<img alt="{{title}}">')
  })

  it("appends its stylesheet rather than filling the skeleton's empty <style> marker", () => {
    // That element is a MARKER the backend splices prd.css into at generation
    // time; a preview must not teach anyone it is already populated.
    const doc = previewSrcDoc("<style></style><h1>{{title}}</h1>")
    expect(doc).toMatch(/<style><\/style>/)
    expect(doc.trimEnd().endsWith("</style>")).toBe(true)
    expect(doc).toMatch(/afmt-ph/)
  })
})

describe("the dialog", () => {
  it("titles itself from the row before the fetch lands", () => {
    previewMock.mockReturnValue(new Promise(() => {}))
    mount()
    const dialog = screen.getByRole("dialog", { name: /Preview — Acme PRD v3/ })
    expect(dialog).toBeTruthy()
    expect(
      screen.getByText(/The text is placeholder; the structure is real\./),
    ).toBeTruthy()
  })

  it("renders the compiled skeleton in a sandbox='' iframe — no scripts, no same-origin", async () => {
    mount()
    await waitFor(() =>
      expect(screen.getByTitle("Preview of Acme PRD v3")).toBeTruthy(),
    )
    const frame = screen.getByTitle("Preview of Acme PRD v3") as HTMLIFrameElement
    // A read-only preview needs neither scripts nor same-origin, so it takes
    // the stricter posture than PrdHtmlView's editable iframe.
    expect(frame.getAttribute("sandbox")).toBe("")
    expect(frame.getAttribute("srcdoc")).toMatch(/afmt-ph/)
  })

  it("says so plainly when there is no skeleton to show yet", async () => {
    previewMock.mockResolvedValue(
      payload({ body: "", compile_status: "failed" }),
    )
    mount()
    await waitFor(() =>
      expect(
        screen.getByText(/We couldn't build a preview from this format yet\./),
      ).toBeTruthy(),
    )
    // The mapping panel is still there — it IS the diagnostic.
    expect(screen.getByText("How we mapped your format")).toBeTruthy()
  })
})

describe("the mapping panel", () => {
  it("says so plainly when there is no section map, and shows nothing else", async () => {
    mount()
    await waitFor(() =>
      expect(
        screen.getByText(/We don't have a section-by-section map for this format\./),
      ).toBeTruthy(),
    )
    // And never an empty table under a confident heading.
    expect(document.querySelector(".afmt-map")).toBeNull()
    // The panel ENDS at the section table. The "Added by Sprntly" and
    // "Yours, kept" blocks that used to follow it are gone: the first
    // described grafting that the compiler no longer does, and the second
    // re-stated the table above it in prose.
    expect(screen.queryByText(/Added by Sprntly/)).toBeNull()
    expect(screen.queryByText(/Yours, kept/)).toBeNull()
    expect(screen.queryByText(/Sections that are yours alone/)).toBeNull()
  })

  it("orders the section map by `order` and labels the form in plain words", async () => {
    previewMock.mockResolvedValue(
      payload({
        section_map: {
          sections: [
            { id: "s2", house: "Requirements", customer: "What we'll build", order: 2, form: "table" },
            { id: "s1", house: "Context", customer: "Background", order: 1, form: "prose" },
            { id: "s3", house: "Users", customer: "Who it's for", order: 3, form: "stories" },
          ],
          unmapped_house: ["Riskiest assumption"],
          extra_sections: ["Rollout plan"],
        },
      }),
    )
    mount()
    await waitFor(() => expect(document.querySelector(".afmt-map")).toBeTruthy())
    const rows = Array.from(document.querySelectorAll(".afmt-map tbody tr")).map(
      (tr) => Array.from(tr.querySelectorAll("td")).map((td) => td.textContent),
    )
    expect(rows).toEqual([
      ["Background", "Context", "Prose"],
      ["What we'll build", "Requirements", "Table"],
      ["Who it's for", "Users", "User stories"],
    ])
    // The horizontal scroller is keyboard-reachable.
    const region = screen.getByRole("region", { name: "Section mapping" })
    expect(region.getAttribute("tabindex")).toBe("0")
    // Nothing renders below the table, even when the payload HAS the arrays
    // those blocks used to read — `section_map` still carries
    // `unmapped_house` / `extra_sections`, and this panel deliberately says
    // nothing about them.
    expect(screen.queryByText(/Added by Sprntly/)).toBeNull()
    expect(screen.queryByText(/Yours, kept/)).toBeNull()
  })

  it("shows EVERY note, translated, never truncated and never raw", async () => {
    previewMock.mockResolvedValue(
      payload({
        compile_status: "needs_review",
        compile_notes: [
          { code: "missing_evidence_list", message: "no `ul.ev`" },
          { code: "missing_input_questions", message: "`ul.inputs` not in `.appendix`" },
          { code: "missing_title", message: "expected one <h1>" },
        ],
      }),
    )
    mount()
    await waitFor(() =>
      expect(document.querySelectorAll(".afmt-preview-notes li").length).toBe(3),
    )
    const text = document.querySelector(".afmt-preview-notes")!.textContent!
    expect(text).toMatch(/bulleted evidence list/)
    expect(text).toMatch(/collects open questions/)
    expect(text).toMatch(/no single document title/)
    for (const jargon of ["ul.ev", "ul.inputs", ".appendix", "<h1>"]) {
      expect(text).not.toContain(jargon)
    }
  })
})

describe("errors", () => {
  it("a 404 says the format isn't here anymore and NEVER the word access", async () => {
    previewMock.mockRejectedValue(new ApiErrorCls(404, { detail: "Format not found." }))
    const { onNotFound } = mount()
    await waitFor(() =>
      expect(screen.getByText("That format isn't here anymore.")).toBeTruthy(),
    )
    expect(document.body.textContent!.toLowerCase()).not.toContain("access")
    // The row leaves the list with it.
    expect(onNotFound).toHaveBeenCalledWith("t1")
    // Retrying cannot help, so it is not offered.
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull()
  })

  it("any other failure offers Try again, which refetches", async () => {
    previewMock.mockRejectedValueOnce(new Error("boom"))
    mount()
    await waitFor(() =>
      expect(screen.getByText("We couldn't load this preview.")).toBeTruthy(),
    )
    previewMock.mockResolvedValue(payload())
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Try again" }))
    })
    await waitFor(() => expect(previewMock).toHaveBeenCalledTimes(2))
    await waitFor(() =>
      expect(screen.getByTitle("Preview of Acme PRD v3")).toBeTruthy(),
    )
  })
})

describe("the footer", () => {
  it("replaces the activate button with the reason rather than disabling it", () => {
    render(
      React.createElement(FormatPreviewModalView, {
        open: true,
        name: "Acme PRD v3",
        detail: null,
        preview: null,
        loading: false,
        error: null,
        notFound: false,
        canActivate: false,
        activateBlockedReason: "Only an admin can change your team's format.",
        activating: false,
        onActivate: vi.fn(),
        onRetry: vi.fn(),
        onClose: vi.fn(),
      }),
    )
    expect(
      screen.getByText("Only an admin can change your team's format."),
    ).toBeTruthy()
    expect(screen.queryByRole("button", { name: "Use this format" })).toBeNull()
  })

  it("hands activation back to the caller, which owns the confirm", async () => {
    const { onActivate } = mount()
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Use this format" })).toBeTruthy(),
    )
    fireEvent.click(screen.getByRole("button", { name: "Use this format" }))
    expect(onActivate).toHaveBeenCalledTimes(1)
  })
})

describe("keyboard", () => {
  it("focus lands inside on open and returns to the opener on close", async () => {
    const opener = document.createElement("button")
    opener.id = "afmt-preview-t1"
    document.body.appendChild(opener)
    opener.focus()
    expect(document.activeElement).toBe(opener)

    const { unmount } = mount()
    await waitFor(() =>
      expect(screen.getByRole("dialog").contains(document.activeElement)).toBe(true),
    )
    unmount()
    expect(document.activeElement).toBe(opener)
    opener.remove()
  })

  it("Escape closes", async () => {
    const { onClose } = mount()
    await waitFor(() => expect(screen.getByRole("dialog")).toBeTruthy())
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it("Tab from the last focusable cycles back to the first", async () => {
    mount()
    const dialog = await screen.findByRole("dialog")
    const focusables = Array.from(
      dialog.querySelectorAll<HTMLElement>(
        "button:not([disabled]), [href], input:not([disabled]), select, textarea, [tabindex]:not([tabindex='-1'])",
      ),
    )
    expect(focusables.length).toBeGreaterThan(1)
    const first = focusables[0]
    const last = focusables[focusables.length - 1]

    last.focus()
    fireEvent.keyDown(dialog, { key: "Tab" })
    expect(document.activeElement).toBe(first)

    first.focus()
    fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true })
    expect(document.activeElement).toBe(last)
  })
})

describe("markdown formats", () => {
  it("renders markdown rather than raw HTML, ghosting its placeholders", async () => {
    previewMock.mockResolvedValue(
      payload({
        artifact_type: "impl_spec",
        format: "markdown",
        body: "## Scope\n\nOwner: {{owner}}\n",
      }),
    )
    mount({ name: "Acme spec" })
    await waitFor(() => expect(document.querySelector(".afmt-preview-md")).toBeTruthy())
    const pane = document.querySelector(".afmt-preview-md") as HTMLElement
    // The discriminator is EXPLICIT — never sniffed from a leading '<'.
    expect(pane.querySelector("iframe")).toBeNull()
    expect(within(pane).getByText("Scope").tagName).toBe("H2")
    expect(pane.querySelector(".afmt-ph")!.textContent).toBe("{{owner}}")
  })
})
