// @vitest-environment jsdom
//
// The v3 HTML PRD is edited inside a sandboxed iframe, and the formatting
// toolbar lives outside it in the panel. That split is the whole reason this
// seam exists: `execCommand` only ever acts on the document that owns the
// current selection, so a toolbar button in the parent has to invoke it on
// `contentDocument` — invoking it on the parent document does nothing at all,
// silently.
//
// The defect these pin: the HTML PRD was editable but had NO toolbar. The
// disabled placeholder shown while generating vanished the moment a document
// arrived, so the controls appeared when there was nothing to format and were
// gone by the time there was.
import { createRef } from "react"
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

const updateMock = vi.fn().mockResolvedValue({})
vi.mock("../../../lib/api", () => ({
  prdApi: { update: (...a: unknown[]) => updateMock(...a) },
}))

import { PrdHtmlView, type PrdHtmlHandle } from "../PrdHtmlView"

const HTML = '<html><body><div id="doc" contenteditable="true">Edit me</div></body></html>'

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

async function mount(props: Partial<React.ComponentProps<typeof PrdHtmlView>> = {}) {
  const ref = createRef<PrdHtmlHandle>()
  const onStatus = vi.fn()
  const view = render(
    <PrdHtmlView ref={ref} html={HTML} prdId={7} title="A PRD" onStatus={onStatus} {...props} />,
  )
  const iframe = view.container.querySelector("iframe") as HTMLIFrameElement
  const idoc = iframe.contentDocument as Document
  // jsdom parses `srcdoc` best-effort only, so the sibling suites seed the body
  // and fire the load event by hand. Same approach here — the point of these
  // tests is where the command is dispatched, not jsdom's HTML parser.
  idoc.body.innerHTML = '<div id="doc" contenteditable="true">Edit me</div>'
  fireEvent.load(iframe)
  // Wait on the ELEMENT, not on `contenteditable="true"` — readOnly mode
  // force-disables exactly that attribute, so keying the wait on it would hang
  // on the one case worth testing hardest.
  await waitFor(() => {
    if (!idoc.getElementById("doc")) throw new Error("doc not ready")
  })
  return { ref, onStatus, view, iframe, idoc }
}

/** Record execCommand on the IFRAME's document, which is the only one that
 *  should ever receive it. jsdom does not implement it. */
function spyOnFrameExec(iframe: HTMLIFrameElement) {
  const spy = vi.fn().mockReturnValue(true)
  Object.defineProperty(iframe.contentDocument!, "execCommand", {
    configurable: true,
    value: spy,
  })
  return spy
}

describe("PrdHtmlView — the toolbar seam", () => {
  it("runs the command on the IFRAME's document, not the panel's", async () => {
    const { ref, iframe } = await mount()
    const frameExec = spyOnFrameExec(iframe)
    const parentExec = vi.fn()
    Object.defineProperty(document, "execCommand", { configurable: true, value: parentExec })

    let ok = false
    await act(async () => { ok = ref.current!.exec("bold") })

    expect(ok).toBe(true)
    expect(frameExec).toHaveBeenCalledWith("bold", false, undefined)
    // The failure mode this rules out: the toolbar appears to work, the parent
    // document takes the command, and nothing in the PRD changes.
    expect(parentExec).not.toHaveBeenCalled()
  })

  it("passes a command's value through (headings, links)", async () => {
    const { ref, iframe } = await mount()
    const frameExec = spyOnFrameExec(iframe)

    await act(async () => { ref.current!.exec("formatBlock", "h2") })
    expect(frameExec).toHaveBeenCalledWith("formatBlock", false, "h2")
  })

  it("focuses the iframe first, or there is no selection to act on", async () => {
    // Clicking a toolbar button in the parent moves focus OUT of the iframe;
    // an unfocused document has no selection and execCommand is a no-op.
    const { ref, iframe } = await mount()
    spyOnFrameExec(iframe)
    const target = iframe.contentDocument!.querySelector<HTMLElement>("[contenteditable='true']")!
    const focus = vi.spyOn(target, "focus")

    await act(async () => { ref.current!.exec("italic") })
    expect(focus).toHaveBeenCalled()
  })

  it("marks the document unsaved so the existing autosave picks it up", async () => {
    // A toolbar edit is an edit: it must land on the SAME unsaved→debounce
    // path native typing uses, not a second save mechanism.
    const { ref, iframe, onStatus } = await mount()
    spyOnFrameExec(iframe)

    await act(async () => { ref.current!.exec("bold") })
    expect(onStatus).toHaveBeenCalledWith("unsaved")
  })

  it("does nothing at all in read-only mode", async () => {
    // Guest mode. The three existing readOnly guards stop persistence; this is
    // the fourth surface that could otherwise write.
    const { ref, iframe, onStatus } = await mount({ readOnly: true })
    const frameExec = spyOnFrameExec(iframe)

    let ok = true
    await act(async () => { ok = ref.current!.exec("bold") })

    expect(ok).toBe(false)
    expect(frameExec).not.toHaveBeenCalled()
    expect(onStatus).not.toHaveBeenCalledWith("unsaved")
  })

  it("reports false rather than throwing when the command is refused", async () => {
    const { ref, iframe } = await mount()
    Object.defineProperty(iframe.contentDocument!, "execCommand", {
      configurable: true,
      value: () => { throw new Error("unsupported") },
    })

    let ok = true
    await act(async () => { ok = ref.current!.exec("bold") })
    expect(ok).toBe(false)
  })
})
