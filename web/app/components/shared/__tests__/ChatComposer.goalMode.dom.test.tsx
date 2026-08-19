// @vitest-environment jsdom
//
// Goal Analysis in the composer: the mode chip and the `+` menu entry.
//
// Two things carry real risk here. The entry must be INVISIBLE to a company
// that is not enrolled — this is an experimental engine that reads a tenant's
// whole corpus, and enrolment is explicit. And adding a third menu item must
// not break the two that were already there: their indices are the host's
// contract, and the arrow-key wrap was written as `% 2`.
import { cleanup, fireEvent, render } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

import { ChatComposer } from "../ChatComposer"

afterEach(cleanup)

function renderComposer(overrides: Record<string, unknown> = {}) {
  const composerRef = { current: null as HTMLTextAreaElement | null }
  const fileInputRef = { current: null as HTMLInputElement | null }
  const noop = () => {}
  return render(
    <ChatComposer
      busy={false}
      draft=""
      pinnedSkill={null}
      attachments={[]}
      hint={null}
      menuOpen={false}
      menuActiveIndex={0}
      slashMenu={null}
      composerRef={composerRef}
      fileInputRef={fileInputRef}
      onInput={noop}
      onKeyDown={noop}
      onSend={noop}
      onStop={noop}
      onToggleMenu={noop}
      onMenuActive={noop}
      onMenuSelect={noop}
      onCloseMenu={noop}
      onRemoveAttachment={noop}
      onRemoveSkill={noop}
      onFileSelect={noop}
      disableVoice
      {...overrides}
    />,
  )
}

describe("Goal Analysis mode chip", () => {
  it("renders nothing for a caller that never heard of it", () => {
    const view = renderComposer()
    expect(view.queryByTestId("goal-analysis-chip")).toBeNull()
    // The head row itself must not appear — an empty strip above the input is
    // a visible change to every existing surface.
    expect(document.querySelector(".cx-head")).toBeNull()
  })

  it("shows the chip while the mode is on", () => {
    const view = renderComposer({ goalMode: true })
    expect(view.getByTestId("goal-analysis-chip").textContent).toContain("Goal Analysis")
  })

  it("never says the word Crucible", () => {
    // The engine name is internal. This is the surface a customer reads.
    const view = renderComposer({ goalMode: true, goalModeAvailable: true, menuOpen: true })
    expect(view.container.textContent?.toLowerCase()).not.toContain("crucible")
  })

  it("leaving the mode is one click", () => {
    const onExit = vi.fn()
    const view = renderComposer({ goalMode: true, onExitGoalMode: onExit })
    fireEvent.click(view.getByLabelText("Leave Goal Analysis"))
    expect(onExit).toHaveBeenCalledTimes(1)
  })
})

describe("the + menu with a third entry", () => {
  it("offers Goal Analysis only to an enrolled company", () => {
    const off = renderComposer({ menuOpen: true })
    expect(off.queryByTestId("menu-goal-analysis")).toBeNull()
    cleanup()
    const on = renderComposer({ menuOpen: true, goalModeAvailable: true })
    expect(on.getByTestId("menu-goal-analysis")).toBeTruthy()
  })

  it("keeps Attach at 0 and Skills at 1 — the host's contract is positional", () => {
    const onSelect = vi.fn()
    const view = renderComposer({
      menuOpen: true, goalModeAvailable: true, onMenuSelect: onSelect,
    })
    fireEvent.click(view.getByTestId("menu-attach"))
    expect(onSelect).toHaveBeenLastCalledWith(0)
    fireEvent.click(view.getByTestId("menu-skills"))
    expect(onSelect).toHaveBeenLastCalledWith(1)
    fireEvent.click(view.getByTestId("menu-goal-analysis"))
    expect(onSelect).toHaveBeenLastCalledWith(2)
  })

  it("ArrowDown reaches the third item instead of wrapping past it", () => {
    // The wrap was hardcoded `% 2`, so a third entry was unreachable from the
    // keyboard — which is exactly who the menu exists for.
    const onActive = vi.fn()
    const view = renderComposer({
      menuOpen: true, goalModeAvailable: true, menuActiveIndex: 1,
      onMenuActive: onActive,
    })
    fireEvent.keyDown(view.container.querySelector(".cx-menu")!, { key: "ArrowDown" })
    expect(onActive).toHaveBeenLastCalledWith(2)
  })

  it("ArrowUp goes UP", () => {
    // It was a copy of the ArrowDown branch, so both went down. Invisible with
    // two items, since +1 and -1 are the same move in a 2-cycle.
    const onActive = vi.fn()
    const view = renderComposer({
      menuOpen: true, goalModeAvailable: true, menuActiveIndex: 1,
      onMenuActive: onActive,
    })
    fireEvent.keyDown(view.container.querySelector(".cx-menu")!, { key: "ArrowUp" })
    expect(onActive).toHaveBeenLastCalledWith(0)
  })

  it("ArrowUp from the first item wraps to the last, not to -1", () => {
    // `(i - 1) % n` is negative in JS, which indexes nothing.
    const onActive = vi.fn()
    const view = renderComposer({
      menuOpen: true, goalModeAvailable: true, menuActiveIndex: 0,
      onMenuActive: onActive,
    })
    fireEvent.keyDown(view.container.querySelector(".cx-menu")!, { key: "ArrowUp" })
    expect(onActive).toHaveBeenLastCalledWith(2)
  })

  it("still wraps correctly with the entry absent", () => {
    const onActive = vi.fn()
    const view = renderComposer({ menuOpen: true, menuActiveIndex: 1, onMenuActive: onActive })
    fireEvent.keyDown(view.container.querySelector(".cx-menu")!, { key: "ArrowDown" })
    expect(onActive).toHaveBeenLastCalledWith(0)
  })
})
