// @vitest-environment jsdom
//
// useMentionPicker — the @-mention picker engine. Covers caret-aware
// interception (opens on a real mid-string `@` token, not `value.length`) and
// chip insertion through the LAZILY-read `ComposerDraftApi` ref (reads the LIVE
// draft/caret at select time, writes via `setValue`). Failure draft-restore is
// NOT tested here — the ENGINE owns it (useProjectGroupThread.test.tsx).
import * as React from "react"
import { act, cleanup, render } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const candidateSearchMock = vi.fn()
vi.mock("../../../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../../../lib/api")>("../../../../../lib/api")
  return {
    ...actual,
    projectsApi: {
      ...actual.projectsApi,
      candidateSearch: (...a: unknown[]) => candidateSearchMock(...a),
    },
  }
})

import { useMentionPicker, type UseMentionPicker } from "../useMentionPicker"
import type { ComposerDraftApi } from "../../../../shared/chat-shell/types"

let latest: UseMentionPicker | null = null
let draftValue = ""
let caretValue = 0
const setValueSpy = vi.fn()
const draftApiRef: { current: ComposerDraftApi | null } = {
  current: {
    getValue: () => draftValue,
    getCaret: () => caretValue,
    setValue: setValueSpy,
  },
}

function Harness() {
  const picker = useMentionPicker({ projectId: 7, draftApiRef })
  latest = picker
  return React.createElement("div", null, picker.open ? "open" : "closed")
}
const flush = async () => {
  await act(async () => {
    await Promise.resolve()
  })
}
const key = (k: string) => ({ key: k, preventDefault: vi.fn() }) as unknown as KeyboardEvent

beforeEach(() => {
  candidateSearchMock.mockReset()
  candidateSearchMock.mockResolvedValue([])
  latest = null
  draftValue = ""
  caretValue = 0
  setValueSpy.mockReset()
})
afterEach(() => cleanup())

describe("useMentionPicker", () => {
  it("test_mention_picker_intercepts_caret_and_opens (AC4)", async () => {
    render(React.createElement(Harness))
    await flush()
    expect(latest!.open).toBe(false)
    // A mid-string `@Bo` token with the REAL caret sitting inside it (index 3),
    // NOT value.length — the trailing " world" must not defeat detection.
    act(() => latest!.handleComposerInput("@Bo world", 3))
    expect(latest!.open).toBe(true)
    expect(latest!.pickerNode).toBeTruthy()
  })

  it("test_mention_picker_does_not_open_for_agent_token (AC4)", async () => {
    render(React.createElement(Harness))
    await flush()
    // `@sprntly` is the agent path — never a people picker.
    act(() => latest!.handleComposerInput("@sprntly", 8))
    expect(latest!.open).toBe(false)
  })

  it("test_mention_picker_inserts_chip_at_current_caret_over_current_text (AC4)", async () => {
    render(React.createElement(Harness))
    await flush()
    // Open on a partial agent-name token so the @Sprntly row is present without
    // a candidate fetch.
    draftValue = "hey @Spr more"
    caretValue = 8
    act(() => latest!.handleComposerInput("hey @Spr more", 8))
    expect(latest!.open).toBe(true)
    // Enter selects the active row (the agent row) — the picker reads the LIVE
    // draft + caret via the lazily-read ref and writes the chip via setValue.
    act(() => {
      latest!.handleKeys(key("Enter"))
    })
    expect(setValueSpy).toHaveBeenCalledTimes(1)
    const [text, caret] = setValueSpy.mock.calls[0]
    expect(text).toContain("@Sprntly")
    expect(typeof caret).toBe("number")
    // The picker closed after the select.
    expect(latest!.open).toBe(false)
  })

  it("test_mention_picker_escape_closes_picker (AC9)", async () => {
    render(React.createElement(Harness))
    await flush()
    act(() => latest!.handleComposerInput("@Spr", 4))
    expect(latest!.open).toBe(true)
    let consumed = false
    act(() => {
      consumed = latest!.handleKeys(key("Escape"))
    })
    expect(consumed).toBe(true)
    expect(latest!.open).toBe(false)
  })
})
