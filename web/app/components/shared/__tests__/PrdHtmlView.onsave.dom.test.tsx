// @vitest-environment jsdom
//
// AD-P13b (one editor, two consumers): `onSave` is an OPTIONAL, purely
// additive prop on `PrdHtmlView`. With it OMITTED — every main-chat caller —
// `persist` must keep calling `prdApi.update` byte-for-byte as before (kept
// as a NEW file so the pre-existing `PrdHtmlView.draft/readonly` suites stay
// UNCHANGED — main-chat byte-identical is a shipping guarantee, AC8).
import { createRef } from "react"
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

const updateMock = vi.fn()
vi.mock("../../../lib/api", () => ({
  prdApi: { update: (...a: unknown[]) => updateMock(...a) },
}))

import { PrdHtmlView, type PrdHtmlHandle } from "../PrdHtmlView"

const HTML = '<html><body><div id="doc" contenteditable="true">Edit me</div></body></html>'

beforeEach(() => {
  localStorage.clear()
  updateMock.mockReset()
  updateMock.mockResolvedValue({})
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function mount(onSave?: (fullHtml: string, title: string) => Promise<void>) {
  const ref = createRef<PrdHtmlHandle>()
  const { container } = render(
    <PrdHtmlView ref={ref} html={HTML} prdId={7} title="Doc" onSave={onSave} />,
  )
  const iframe = container.querySelector("iframe") as HTMLIFrameElement
  const idoc = iframe.contentDocument as Document
  return { ref, iframe, idoc }
}

describe("PrdHtmlView — onSave injection (AD-P13b)", () => {
  it("onSave INJECTED: persist calls onSave(doc, title) and does NOT call prdApi.update", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined)
    const { ref, idoc, iframe } = mount(onSave)
    idoc.body.innerHTML = '<div id="doc" contenteditable="true">edited</div>'
    fireEvent.load(iframe)
    await waitFor(() => expect(ref.current).not.toBeNull())

    await act(async () => {
      await ref.current!.save()
    })

    expect(onSave).toHaveBeenCalledTimes(1)
    const [doc, title] = onSave.mock.calls[0]
    expect(doc).toContain("edited")
    expect(title).toBe("Doc")
    expect(updateMock).not.toHaveBeenCalled()
  })

  it("onSave OMITTED: persist calls prdApi.update(prdId, {title, payload_md}) unchanged", async () => {
    const { ref, idoc, iframe } = mount(undefined)
    idoc.body.innerHTML = '<div id="doc" contenteditable="true">edited</div>'
    fireEvent.load(iframe)
    await waitFor(() => expect(ref.current).not.toBeNull())

    await act(async () => {
      await ref.current!.save()
    })

    expect(updateMock).toHaveBeenCalledTimes(1)
    const [prdId, body] = updateMock.mock.calls[0]
    expect(prdId).toBe(7)
    expect(body).toEqual(expect.objectContaining({ title: "Doc" }))
    expect(String(body.payload_md)).toContain("edited")
  })
})
