// @vitest-environment jsdom
//
// The composer's parked quote — the passage you pressed Reply on, waiting
// above the input. The composer only DISPLAYS it: folding it into the outgoing
// message belongs to the caller, so a send must not silently consume it here.
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

describe("ChatComposer quoted passage", () => {
  it("renders nothing when no quote is parked (existing callers unchanged)", () => {
    const view = renderComposer()
    expect(view.queryByTestId("composer-quote")).toBeNull()
  })

  it("shows the parked excerpt above the input", () => {
    const view = renderComposer({ quote: "findings must be documented" })
    expect(view.getByTestId("composer-quote").textContent).toContain(
      "findings must be documented",
    )
  })

  it("dismisses through onRemoveQuote", () => {
    const onRemoveQuote = vi.fn()
    const view = renderComposer({ quote: "some excerpt", onRemoveQuote })
    fireEvent.click(view.getByLabelText("Remove the quoted text"))
    expect(onRemoveQuote).toHaveBeenCalledTimes(1)
  })

  it("keeps the excerpt out of the draft — it is context, not typed text", () => {
    const view = renderComposer({ quote: "some excerpt", draft: "what about this?" })
    const textarea = view.container.querySelector<HTMLTextAreaElement>(".cx-input")!
    expect(textarea.value).toBe("what about this?")
  })
})
