// @vitest-environment jsdom
//
// The two turn-level affordances added for highlight-to-reply and
// edit-and-resend: the quote block above a user bubble, and the in-place
// editor that replaces the bubble on a question that never got an answer.
//
// Both are caller-DRIVEN (ChatBubble owns no state about which turn is being
// edited), so the load-bearing assertions are that an unwired caller renders
// exactly what it rendered before, and that a wired one gets the text back.
import { cleanup, fireEvent, render } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
  if (typeof window !== "undefined" && !window.matchMedia) {
    window.matchMedia = ((q: string) => ({
      matches: false, media: q, onchange: null,
      addEventListener() {}, removeEventListener() {},
      addListener() {}, removeListener() {}, dispatchEvent() { return false },
    })) as unknown as typeof window.matchMedia
  }
})

import { ChatBubble } from "../ChatBubble"

afterEach(cleanup)

describe("ChatBubble quoted passage", () => {
  it("renders the quote above the message when the caller supplies one", () => {
    const view = render(
      <ChatBubble
        turnId="t1"
        agentName="Sprntly"
        user={{ query: "Which manual is that?", quote: "findings must be documented" }}
      />,
    )
    const quote = view.getByTestId("turn-quote")
    expect(quote.textContent).toBe("findings must be documented")
    // Order matters: the excerpt reads before the words that answer it.
    expect(
      quote.compareDocumentPosition(view.container.querySelector(".bc-user-bubble")!) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
  })

  it("renders no quote node at all when unset", () => {
    const view = render(
      <ChatBubble turnId="t2" agentName="Sprntly" user={{ query: "plain question" }} />,
    )
    expect(view.queryByTestId("turn-quote")).toBeNull()
  })

  it("never shows blockquote markers — the excerpt renders as prose", () => {
    // The wire form is `body\n\n> excerpt`; callers split it. If a marker ever
    // reaches this component it is a caller that skipped `splitQuotedSuffix`,
    // and the user sees syntax they never typed.
    const view = render(
      <ChatBubble
        turnId="t3"
        agentName="Sprntly"
        user={{ query: "Which manual?", quote: "findings must be documented" }}
      />,
    )
    expect(view.getByTestId("turn-quote").textContent).not.toContain(">")
  })

  it("is a static blockquote when the caller cannot open a viewer", () => {
    const view = render(
      <ChatBubble turnId="t4" agentName="Sprntly" user={{ query: "q", quote: "excerpt" }} />,
    )
    expect(view.getByTestId("turn-quote").tagName).toBe("BLOCKQUOTE")
  })

  it("becomes a button that opens the full passage when onOpenQuote is wired", () => {
    // The block is clamped to four lines, so without this the tail of a long
    // highlight is unreachable.
    const onOpenQuote = vi.fn()
    const view = render(
      <ChatBubble
        turnId="t5"
        agentName="Sprntly"
        user={{ query: "q", quote: "a very long excerpt", onOpenQuote }}
      />,
    )
    const quote = view.getByTestId("turn-quote")
    expect(quote.tagName).toBe("BUTTON")
    fireEvent.click(quote)
    expect(onOpenQuote).toHaveBeenCalledTimes(1)
  })
})

describe("ChatBubble past-prompt actions", () => {
  const user = { query: "waht is the audit findings form?" }

  it("renders no action row at all when nothing is wired", () => {
    const view = render(<ChatBubble turnId="a1" agentName="Sprntly" user={user} />)
    expect(view.container.querySelector(".bc-user-actions")).toBeNull()
    expect(view.queryByTestId("user-turn-copy")).toBeNull()
    expect(view.queryByTestId("user-turn-retry")).toBeNull()
    expect(view.queryByTestId("user-turn-edit")).toBeNull()
  })

  it("renders each action independently of the others", () => {
    // The mapper decides eligibility per action — copy is offered on turns
    // that can't be re-asked — so the row must not be all-or-nothing.
    const view = render(
      <ChatBubble turnId="a2" agentName="Sprntly" user={user} onCopyUserTurn={() => {}} />,
    )
    expect(view.queryByTestId("user-turn-copy")).not.toBeNull()
    expect(view.queryByTestId("user-turn-retry")).toBeNull()
    expect(view.queryByTestId("user-turn-edit")).toBeNull()
  })

  it("reports copy and retry clicks", () => {
    const onCopyUserTurn = vi.fn()
    const onRetryUserTurn = vi.fn()
    const view = render(
      <ChatBubble
        turnId="a3"
        agentName="Sprntly"
        user={user}
        onCopyUserTurn={onCopyUserTurn}
        onRetryUserTurn={onRetryUserTurn}
      />,
    )
    fireEvent.click(view.getByTestId("user-turn-copy"))
    fireEvent.click(view.getByTestId("user-turn-retry"))
    expect(onCopyUserTurn).toHaveBeenCalledTimes(1)
    expect(onRetryUserTurn).toHaveBeenCalledTimes(1)
  })

  it("announces the copied state without resizing the row", () => {
    const view = render(
      <ChatBubble
        turnId="a4"
        agentName="Sprntly"
        user={user}
        onCopyUserTurn={() => {}}
        copied
      />,
    )
    // Label changes, no text node appears — the row must not reflow under the
    // cursor that just clicked it.
    expect(view.getByTestId("user-turn-copy").getAttribute("aria-label")).toBe("Copied")
    expect(view.getByTestId("user-turn-copy").textContent).toBe("")
  })

  it("orders the row copy → edit → retry", () => {
    // Least- to most-consequential: a mis-aimed click should not land on the
    // one that re-runs a generation.
    const view = render(
      <ChatBubble
        turnId="a5"
        agentName="Sprntly"
        user={user}
        onCopyUserTurn={() => {}}
        onRetryUserTurn={() => {}}
        onEditUserTurn={() => {}}
      />,
    )
    const ids = Array.from(
      view.container.querySelectorAll(".bc-user-actions button"),
    ).map((b) => b.getAttribute("data-testid"))
    expect(ids).toEqual(["user-turn-copy", "user-turn-edit", "user-turn-retry"])
  })
})

describe("ChatBubble edit-and-resend", () => {
  const user = { query: "waht is the audit findings form?" }

  it("offers no edit affordance unless the caller wires one", () => {
    const view = render(<ChatBubble turnId="t3" agentName="Sprntly" user={user} />)
    expect(view.queryByTestId("user-turn-edit")).toBeNull()
  })

  it("shows the edit affordance when wired, and reports the click", () => {
    const onEditUserTurn = vi.fn()
    const view = render(
      <ChatBubble turnId="t4" agentName="Sprntly" user={user} onEditUserTurn={onEditUserTurn} />,
    )
    fireEvent.click(view.getByTestId("user-turn-edit"))
    expect(onEditUserTurn).toHaveBeenCalledTimes(1)
  })

  it("replaces the bubble with an editor seeded from the message", () => {
    const view = render(
      <ChatBubble
        turnId="t5"
        agentName="Sprntly"
        user={user}
        editing
        onSubmitEdit={() => {}}
        onCancelEdit={() => {}}
      />,
    )
    // One question on screen, not two — the stale bubble is gone.
    expect(view.container.querySelector(".bc-user-bubble")).toBeNull()
    const textarea = view.getByLabelText("Edit your message") as HTMLTextAreaElement
    expect(textarea.value).toBe(user.query)
  })

  it("hides the quote while editing and hands back only the edited words", () => {
    const onSubmitEdit = vi.fn()
    const view = render(
      <ChatBubble
        turnId="t6"
        agentName="Sprntly"
        user={{ ...user, quote: "findings must be documented" }}
        editing
        onSubmitEdit={onSubmitEdit}
        onCancelEdit={() => {}}
      />,
    )
    expect(view.queryByTestId("turn-quote")).toBeNull()
    const textarea = view.getByLabelText("Edit your message")
    fireEvent.change(textarea, { target: { value: "what is the audit findings form?" } })
    fireEvent.click(view.getByTestId("user-turn-edit-save"))
    expect(onSubmitEdit).toHaveBeenCalledWith("what is the audit findings form?")
  })

  it("saves on Enter and cancels on Escape", () => {
    const onSubmitEdit = vi.fn()
    const onCancelEdit = vi.fn()
    const view = render(
      <ChatBubble
        turnId="t7"
        agentName="Sprntly"
        user={user}
        editing
        onSubmitEdit={onSubmitEdit}
        onCancelEdit={onCancelEdit}
      />,
    )
    const textarea = view.getByLabelText("Edit your message")
    fireEvent.change(textarea, { target: { value: "corrected question" } })
    fireEvent.keyDown(textarea, { key: "Enter" })
    expect(onSubmitEdit).toHaveBeenCalledWith("corrected question")

    fireEvent.keyDown(textarea, { key: "Escape" })
    expect(onCancelEdit).toHaveBeenCalledTimes(1)
  })

  it("adds a newline on Shift+Enter instead of sending", () => {
    const onSubmitEdit = vi.fn()
    const view = render(
      <ChatBubble
        turnId="t8"
        agentName="Sprntly"
        user={user}
        editing
        onSubmitEdit={onSubmitEdit}
        onCancelEdit={() => {}}
      />,
    )
    fireEvent.keyDown(view.getByLabelText("Edit your message"), { key: "Enter", shiftKey: true })
    expect(onSubmitEdit).not.toHaveBeenCalled()
  })

  it("refuses to send an emptied message", () => {
    const onSubmitEdit = vi.fn()
    const view = render(
      <ChatBubble
        turnId="t9"
        agentName="Sprntly"
        user={user}
        editing
        onSubmitEdit={onSubmitEdit}
        onCancelEdit={() => {}}
      />,
    )
    fireEvent.change(view.getByLabelText("Edit your message"), { target: { value: "   " } })
    fireEvent.click(view.getByTestId("user-turn-edit-save"))
    expect(onSubmitEdit).not.toHaveBeenCalled()
  })
})
