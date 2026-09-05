// @vitest-environment jsdom
//
// While a plan gate is open, the composer talks to the card.
//
// WHY THIS FILE EXISTS. Asked "ok let's go with the plan", the chat replied
// "Got it — the plan is locked" and wrote out a complete four-item revenue
// plan. Nothing was locked and nothing ran. The first fix guarded the send
// BUTTON and shipped, and it did not work: Enter is a separate route that falls
// through to the engine's own keydown handler, so a revision typed and sent
// with Enter still reached `/v1/ask` and came back as another invented plan
// with the chip visible above the composer the whole time.
//
// A guard that covers one way of sending is not a guard. These assert all three
// routes agree — Enter, the button, and the dismissed chip — because the one
// that was broken was the one nobody had checked.
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // The composer's tree uses JSX without importing React, and vitest compiles
  // .tsx with the classic transform — so `React` has to be global before the
  // module loads. Same idiom, same reason, as `ChatComposer.goalMode.dom`.
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

import { ChatComposer } from "../../../shared/ChatComposer"

/** The composer as the thread renders it while a gate is open. `onSend` and
 *  `onKeyDown` are the two routes a message can leave by; the third — the
 *  shell's `send.onSubmit` — receives the same handler as the button, so it is
 *  covered by covering that. */
type ComposerProps = Parameters<typeof ChatComposer>[0]

const renderComposer = (over: Partial<ComposerProps> = {}) => {
  const onSend = vi.fn()
  const onKeyDown = vi.fn()
  const onExitReplyTarget = vi.fn()
  // BUILT, THEN SPREAD. Spreading a `Partial` straight into JSX makes every
  // required prop look possibly-absent to the compiler even though the base
  // supplies them all; merging first says what is actually true.
  const props = {
    draft: "actually drop the app store reviews",
    busy: false,
    attachments: [],
    pinnedSkill: null,
    hint: null,
    composerRef: { current: null },
    fileInputRef: { current: null },
    onInput: () => {},
    onKeyDown,
    onSend,
    onStop: () => {},
    onToggleMenu: () => {},
    onMenuActive: () => {},
    onMenuSelect: () => {},
    onCloseMenu: () => {},
    onRemoveAttachment: () => {},
    onRemoveSkill: () => {},
    onFileSelect: () => {},
    menuOpen: false,
    menuActiveIndex: 0,
    replyTarget: "Replying to the plan for \u201cdrive revenue\u201d",
    onExitReplyTarget,
    ...over,
  } as unknown as ComposerProps
  render(<ChatComposer {...props} />)
  return { onSend, onKeyDown, onExitReplyTarget }
}

afterEach(cleanup)

describe("the chip says what is being replied to", () => {
  it("names the plan, not just that a gate is open", () => {
    renderComposer()
    expect(screen.getByTestId("reply-target-chip").textContent)
      .toContain("drive revenue")
  })

  it("offers a way out, because a gate can be abandoned", () => {
    // Several runs sit at `awaiting_approval` right now. A forgotten gate must
    // never leave a conversation unable to talk to Sprntly, which is why the
    // composer is redirected and not disabled.
    const { onExitReplyTarget } = renderComposer()
    fireEvent.click(screen.getByRole("button", { name: /talk to sprntly instead/i }))
    expect(onExitReplyTarget).toHaveBeenCalled()
  })

  it("is absent when no gate is open", () => {
    renderComposer({ replyTarget: undefined })
    expect(screen.queryByTestId("reply-target-chip")).toBeNull()
  })
})

describe("both routes out of the composer are the same route", () => {
  it("the send button goes through the host's handler", () => {
    const { onSend } = renderComposer()
    fireEvent.click(screen.getByRole("button", { name: /send/i }))
    expect(onSend).toHaveBeenCalledTimes(1)
  })

  it("Enter goes through the host's keydown, which is where the guard sits", () => {
    // THE ONE THAT WAS BROKEN. The composer does not send on Enter itself — it
    // hands the event to the host, and the host decides. This asserts the
    // wiring that made the guard reachable at all.
    const { onKeyDown } = renderComposer()
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" })
    expect(onKeyDown).toHaveBeenCalledTimes(1)
  })
})

// ─── The approval regex ───────────────────────────────────────────────────
//
// Mirrors the pattern in `ChatScreen`. THE TWO MISTAKES ARE NOT EQUAL: reading
// a revision as an approval starts an analysis the reader did not ask for, over
// evidence they were about to change; reading an approval as a revision costs
// them one sentence and the reply says what to do. So this matches only what is
// unmistakable, and everything else is a revision.
const APPROVAL = /^\s*(ok(ay)?[, ]*)?(let'?s |lets |please |just )?(go|go ahead|run it|run this|start( it)?|approve( it| this| the plan)?|proceed|do it|yes|yep|yeah|sounds good|looks good|lgtm|ship it|go with (the|this) plan|let'?s go with (the|this) plan)[.! ]*$/i

describe("what counts as approving", () => {
  it.each([
    "ok let's go with the plan",
    "go ahead",
    "approve",
    "approve the plan",
    "run it",
    "yes",
    "looks good",
    "LGTM",
    "Okay, proceed.",
  ])("treats %j as approval", (said) => {
    expect(APPROVAL.test(said)).toBe(true)
  })

  it.each([
    "actually drop the app store reviews, they are useless for B2B",
    "can you also read the support tickets",
    "go with the analytics source only",
    "approve it but drop the tracker",
    "what does this actually measure?",
    "no",
    "not yet",
  ])("treats %j as a revision, not approval", (said) => {
    // Each of these would start a real run over evidence the reader was about
    // to change. The cost of the other mistake is one more sentence.
    expect(APPROVAL.test(said)).toBe(false)
  })
})
