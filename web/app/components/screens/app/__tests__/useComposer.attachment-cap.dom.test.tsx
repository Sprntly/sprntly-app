// @vitest-environment jsdom
//
// A chat message carries at most MAX_CHAT_ATTACHMENTS files.
//
// Fourteen on one turn is what prompted the cap: every one was read,
// extracted and folded into a prompt, and the chips buried the question they
// belonged to. Five is enough to bring the evidence for a question and few
// enough to still see the question.
//
// THE COUNT IS AGAINST WHAT IS ALREADY STAGED, not against one pick. Three
// files chosen twice is six, and a per-pick check waves that through — which
// is the version of this cap that looks right in a demo and does nothing.
import * as React from "react"
import { act, cleanup, renderHook } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

vi.mock("../../../../lib/useSpeechInput", () => ({
  useSpeechInput: () => ({ supported: false, listening: false, toggle: vi.fn() }),
}))
vi.mock("../../../../lib/api", () => ({ askApi: { skills: () => Promise.resolve({ skills: [] }) } }))

import { useComposer } from "../useComposer"
import { MAX_CHAT_ATTACHMENTS } from "../../../shared/ChatComposer"

/**
 * A change event carrying `names`, shaped like the real input's.
 *
 * `.pdf` on purpose: a text extension takes the FileReader branch, which
 * resolves a tick later, and the assertions here are about the cap rather than
 * about reading. The cap runs before either branch.
 */
function pick(names: string[]) {
  return {
    target: {
      files: names.map((n) => new File(["x"], n, { type: "application/pdf" })),
      value: "",
    },
  } as unknown as React.ChangeEvent<HTMLInputElement>
}

const showToast = vi.fn()

afterEach(() => {
  cleanup()
  showToast.mockReset()
})

describe("the cap", () => {
  it("takes everything up to the limit", () => {
    const { result } = renderHook(() => useComposer({ showToast }))
    act(() => result.current.handleFileSelect(pick(["a.pdf", "b.pdf", "c.pdf"])))
    expect(result.current.attachments).toHaveLength(3)
    expect(showToast).not.toHaveBeenCalled()
  })

  it("stops at the limit within ONE pick, and says what was dropped", () => {
    const { result } = renderHook(() => useComposer({ showToast }))
    const names = Array.from({ length: 9 }, (_, i) => `f${i}.pdf`)
    act(() => result.current.handleFileSelect(pick(names)))

    expect(result.current.attachments).toHaveLength(MAX_CHAT_ATTACHMENTS)
    expect(showToast).toHaveBeenCalledTimes(1)
    // Named, not counted: a silent truncation at the boundary is how someone
    // sends a question missing the file it was about.
    const body = String(showToast.mock.calls[0][1])
    expect(body).toContain("f5.pdf")
    expect(body).not.toContain("f0.pdf")
  })

  it("counts ACROSS picks — the case a per-pick check misses", () => {
    const { result } = renderHook(() => useComposer({ showToast }))
    act(() => result.current.handleFileSelect(pick(["a.pdf", "b.pdf", "c.pdf"])))
    act(() => result.current.handleFileSelect(pick(["d.pdf", "e.pdf", "f.pdf"])))

    expect(result.current.attachments).toHaveLength(MAX_CHAT_ATTACHMENTS)
    expect(result.current.attachments.map((a) => a.name)).toEqual([
      "a.pdf", "b.pdf", "c.pdf", "d.pdf", "e.pdf",
    ])
    expect(showToast).toHaveBeenCalledTimes(1)
  })

  it("says so plainly when nothing more will fit", () => {
    const { result } = renderHook(() => useComposer({ showToast }))
    const names = Array.from({ length: MAX_CHAT_ATTACHMENTS }, (_, i) => `f${i}.pdf`)
    act(() => result.current.handleFileSelect(pick(names)))
    showToast.mockReset()

    act(() => result.current.handleFileSelect(pick(["one-too-many.pdf"])))

    expect(result.current.attachments).toHaveLength(MAX_CHAT_ATTACHMENTS)
    expect(String(showToast.mock.calls[0][0])).toContain(String(MAX_CHAT_ATTACHMENTS))
  })

  it("frees a slot when one is removed", () => {
    // The cap is a ceiling, not a lifetime budget.
    const { result } = renderHook(() => useComposer({ showToast }))
    const names = Array.from({ length: MAX_CHAT_ATTACHMENTS }, (_, i) => `f${i}.pdf`)
    act(() => result.current.handleFileSelect(pick(names)))
    act(() => result.current.setAttachments((prev) => prev.slice(1)))
    act(() => result.current.handleFileSelect(pick(["late.pdf"])))

    expect(result.current.attachments).toHaveLength(MAX_CHAT_ATTACHMENTS)
    expect(result.current.attachments.map((a) => a.name)).toContain("late.pdf")
  })
})
