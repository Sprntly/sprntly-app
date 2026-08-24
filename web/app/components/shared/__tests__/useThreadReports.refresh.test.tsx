// @vitest-environment jsdom
//
// useThreadReportsSync — a report answer refetches the thread's list.
//
// Capture is a SERVER-side step that runs after the answer completes
// (backend/app/report_capture.py), so the list this hook fetched when the
// thread opened is one row short the moment the user watches a report arrive —
// and nothing else in content changes to say so. The chat bumps
// `reportsRefreshKey` when the settled answer carries `_report`, which is what
// makes the panel able to open on the document that was just written instead of
// only learning about it on the next visit to the thread.
import * as React from "react"
import { act, cleanup, render, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const listForConversation = vi.fn((..._a: unknown[]) => Promise.resolve<unknown[]>([]))

vi.mock("../../../lib/api", () => ({
  reportsApi: { listForConversation: (...a: unknown[]) => listForConversation(...a) },
}))

let content: Record<string, unknown> = {}
const setContent = vi.fn((patch: Record<string, unknown>) => {
  content = { ...content, ...patch }
})

vi.mock("../../../context/ContentContext", () => ({
  useContent: () => ({ content, setContent }),
}))
vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({ contentPanelTab: null }),
}))

import { useThreadReportsSync } from "../useThreadReports"

function Harness() {
  useThreadReportsSync()
  return null
}

afterEach(() => {
  cleanup()
  listForConversation.mockClear()
  setContent.mockClear()
  content = {}
})

describe("useThreadReportsSync — refetch on a report answer", () => {
  it("re-reads the thread's reports when reportsRefreshKey changes", async () => {
    content = { conversationId: 7 }
    const { rerender } = render(<Harness />)
    await waitFor(() => expect(listForConversation).toHaveBeenCalledTimes(1))
    expect(listForConversation).toHaveBeenCalledWith(7)

    // The answer landed and it was a report: the chat stamps a new key.
    await act(async () => {
      content = { conversationId: 7, reportsRefreshKey: 1234 }
      rerender(<Harness />)
    })
    await waitFor(() => expect(listForConversation).toHaveBeenCalledTimes(2))
  })

  it("does not refetch when nothing changed", async () => {
    content = { conversationId: 7, reportsRefreshKey: 1234 }
    const { rerender } = render(<Harness />)
    await waitFor(() => expect(listForConversation).toHaveBeenCalledTimes(1))

    await act(async () => {
      rerender(<Harness />)
    })
    expect(listForConversation).toHaveBeenCalledTimes(1)
  })
})

describe("useThreadReportsSync — the capture race", () => {
  it("re-asks an empty list after a report answer until the row lands", async () => {
    vi.useFakeTimers()
    // Capture is a post-terminal server step: the poll that delivered the answer
    // beat the insert by 2.2s in the live trace this guards.
    listForConversation
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([{ id: 125, title: "Voice-of-Customer Report" }])

    content = { conversationId: 998, reportsRefreshKey: 1 }
    render(<Harness />)
    await vi.advanceTimersByTimeAsync(0)
    expect(listForConversation).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(1500)
    expect(listForConversation).toHaveBeenCalledTimes(2)

    await vi.advanceTimersByTimeAsync(1500)
    expect(listForConversation).toHaveBeenCalledTimes(3)
    expect(content.threadReports).toHaveLength(1)

    // The row landed — no fourth ask.
    await vi.advanceTimersByTimeAsync(5000)
    expect(listForConversation).toHaveBeenCalledTimes(3)
    vi.useRealTimers()
  })

  it("does not retry an ordinary empty thread", async () => {
    vi.useFakeTimers()
    listForConversation.mockResolvedValue([])

    content = { conversationId: 998 }
    render(<Harness />)
    await vi.advanceTimersByTimeAsync(0)
    await vi.advanceTimersByTimeAsync(6000)
    expect(listForConversation).toHaveBeenCalledTimes(1)
    vi.useRealTimers()
  })
})
