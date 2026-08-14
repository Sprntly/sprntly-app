// @vitest-environment jsdom
//
// The markdown PRD editor extracted from PrdPanelContent (AD-P13b) — a
// contenteditable body + execCommand toolbar + draft/autosave, as ONE shared
// primitive consumed by the main-chat panel AND (a follow-up ticket) the
// project artifact drawer. Pins: flatten-to-innerText autosave, the `onSave`
// injection seam (omitted → `prdApi.update` byte-for-byte), `readOnly`'s
// three independent write-stops, and `draftScope`'s distinct localStorage key
// so a second consumer never collides with the main-chat draft for the same
// prd_id.
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

import { PrdMarkdownEditor, type PrdMarkdownHandle } from "../PrdMarkdownEditor"

beforeEach(() => {
  localStorage.clear()
  updateMock.mockReset()
  updateMock.mockResolvedValue({})
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

async function pastDebounce() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 2100))
  })
}

describe("PrdMarkdownEditor", () => {
  it("edit flattens to innerText and autosaves via prdApi.update (onSave omitted)", async () => {
    const ref = createRef<PrdMarkdownHandle>()
    const { container } = render(
      <PrdMarkdownEditor ref={ref} prdId={11} title="Doc">
        <p>original</p>
      </PrdMarkdownEditor>,
    )
    const body = container.querySelector(".prd-body") as HTMLElement
    body.innerText = "edited body text"
    fireEvent.input(body)
    await pastDebounce()

    expect(updateMock).toHaveBeenCalledTimes(1)
    const [prdId, payload] = updateMock.mock.calls[0]
    expect(prdId).toBe(11)
    expect(payload).toEqual(
      expect.objectContaining({ title: "Doc", payload_md: "edited body text" }),
    )
  })

  it("onSave injection takes precedence over prdApi.update", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined)
    const { container } = render(
      <PrdMarkdownEditor prdId={11} title="Doc" onSave={onSave}>
        <p>original</p>
      </PrdMarkdownEditor>,
    )
    const body = container.querySelector(".prd-body") as HTMLElement
    body.innerText = "gated edit"
    fireEvent.input(body)
    await pastDebounce()

    expect(onSave).toHaveBeenCalledTimes(1)
    expect(onSave).toHaveBeenCalledWith("gated edit", "Doc")
    expect(updateMock).not.toHaveBeenCalled()
  })

  it("omitted onSave calls prdApi.update(prdId, {title, payload_md}) — the byte-for-byte main-chat path", async () => {
    const ref = createRef<PrdMarkdownHandle>()
    const { container } = render(
      <PrdMarkdownEditor ref={ref} prdId={11} title="Doc">
        <p>original</p>
      </PrdMarkdownEditor>,
    )
    const body = container.querySelector(".prd-body") as HTMLElement
    body.innerText = "main chat edit"
    fireEvent.input(body)
    await pastDebounce()

    expect(updateMock).toHaveBeenCalledWith(11, { title: "Doc", payload_md: "main chat edit" })
  })

  it("readOnly: no toolbar rendered and an input event reaches no write path", async () => {
    const { container } = render(
      <PrdMarkdownEditor prdId={11} title="Doc" readOnly>
        <p>original</p>
      </PrdMarkdownEditor>,
    )
    expect(container.querySelector(".prd-toolbar")).toBeNull()
    const body = container.querySelector(".prd-body") as HTMLElement
    expect(body.getAttribute("contenteditable")).toBe("false")

    fireEvent.input(body)
    await pastDebounce()
    expect(updateMock).not.toHaveBeenCalled()
  })

  it("readOnly: the imperative save() handle also refuses to write", async () => {
    const ref = createRef<PrdMarkdownHandle>()
    render(
      <PrdMarkdownEditor ref={ref} prdId={11} title="Doc" readOnly>
        <p>original</p>
      </PrdMarkdownEditor>,
    )
    await waitFor(() => expect(ref.current).not.toBeNull())
    await act(async () => {
      await ref.current!.save()
    })
    expect(updateMock).not.toHaveBeenCalled()
  })

  it("draftScope produces a distinct localStorage key from the omitted-scope (main-chat) key", async () => {
    const { container, unmount } = render(
      <PrdMarkdownEditor prdId={11} title="Doc" draftScope="drawer">
        <p>original</p>
      </PrdMarkdownEditor>,
    )
    const body = container.querySelector(".prd-body") as HTMLElement
    body.innerHTML = "<p>scoped draft</p>"
    fireEvent.input(body)
    await pastDebounce()
    unmount()

    expect(localStorage.getItem("sprntly_prd_draft_11")).toBeNull()
    expect(localStorage.getItem("sprntly_prd_draft_11_drawer")).not.toBeNull()
    expect(localStorage.getItem("sprntly_prd_draft_11_drawer")).toContain("scoped draft")
  })
})
