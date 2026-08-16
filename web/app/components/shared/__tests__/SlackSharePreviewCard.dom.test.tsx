// @vitest-environment jsdom
//
// Tests for SlackSharePreviewCard — the confirm step between "share this PRD
// on my slack channel" and a message actually appearing in a team channel.
//
// The invariant every test here circles: NOTHING IS POSTED FROM THIS CARD
// WITHOUT A CLICK. A post to a team channel is public and cannot be recalled,
// so the card must never send on mount, never send with no channel chosen, and
// must state plainly when it did NOT send.
import * as React from "react"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

import { SlackSharePreviewCard } from "../SlackSharePreviewCard"
import type { SlackSharePreview, SlackShareTarget } from "../../../lib/api"

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

const TARGET: SlackShareTarget = {
  type: "prd",
  id: 42,
  title: "Checkout Abandonment",
  kind_label: "PRD",
  url: "https://app.sprntly.test/brief?prd=42",
}

const CHANNELS = [
  { id: "C1", name: "product", is_private: false, is_member: true },
  { id: "C2", name: "product-leads", is_private: false, is_member: false },
]

function ready(overrides: Partial<SlackSharePreview> = {}): SlackSharePreview {
  return {
    status: "ready",
    target: TARGET,
    channel: CHANNELS[0],
    channels: [],
    message: {
      text: "Feedback welcome.\nPRD: Checkout Abandonment",
      blocks: [{ type: "section", text: { type: "mrkdwn", text: "Feedback welcome." } }],
      summary: "Carts are abandoned at the payment step.",
    },
    warning: null,
    ...overrides,
  }
}

function props(preview: SlackSharePreview, over: Record<string, unknown> = {}) {
  return {
    preview,
    onSend: vi.fn(),
    onCancel: vi.fn(),
    onPickTarget: vi.fn(),
    ...over,
  }
}

describe("SlackSharePreviewCard — ready to post", () => {
  it("shows the document, its teaser and the exact link that will be posted", () => {
    render(<SlackSharePreviewCard {...props(ready())} />)
    expect(screen.getByText("Checkout Abandonment")).toBeTruthy()
    expect(screen.getByText("PRD")).toBeTruthy()
    expect(screen.getByText("Carts are abandoned at the payment step.")).toBeTruthy()
    // The real URL, not a description of one — a reader can check where the
    // team is being sent before the team is sent there.
    expect(screen.getByText("https://app.sprntly.test/brief?prd=42")).toBeTruthy()
  })

  it("names the destination channel on the button", () => {
    render(<SlackSharePreviewCard {...props(ready())} />)
    expect(screen.getByTestId("slack-share-send").textContent).toContain("#product")
  })

  it("pre-fills the note from the composed message and keeps it editable", () => {
    render(<SlackSharePreviewCard {...props(ready())} />)
    const note = screen.getByTestId("slack-share-note") as HTMLTextAreaElement
    expect(note.value).toBe("Feedback welcome.")
    fireEvent.change(note, { target: { value: "Rewritten by hand." } })
    expect(note.value).toBe("Rewritten by hand.")
  })

  it("posts nothing until the button is clicked, then sends the edited note", () => {
    const p = props(ready())
    render(<SlackSharePreviewCard {...p} />)
    expect(p.onSend).not.toHaveBeenCalled()

    fireEvent.change(screen.getByTestId("slack-share-note"), {
      target: { value: "Rewritten by hand." },
    })
    fireEvent.click(screen.getByTestId("slack-share-send"))
    expect(p.onSend).toHaveBeenCalledWith("C1", "Rewritten by hand.")
  })

  it("cancelling sends nothing", () => {
    const p = props(ready())
    render(<SlackSharePreviewCard {...p} />)
    fireEvent.click(screen.getByTestId("slack-share-cancel"))
    expect(p.onCancel).toHaveBeenCalledTimes(1)
    expect(p.onSend).not.toHaveBeenCalled()
  })

  it("a note-less share leaves the box empty rather than pre-filling the document line", () => {
    // Block 0 is the DOCUMENT when no note was drafted; putting "*PRD:* <…>"
    // in the note box would be nonsense the user has to delete.
    const preview = ready({
      message: {
        text: "PRD: Checkout Abandonment",
        blocks: [{
          type: "section",
          text: { type: "mrkdwn", text: "*PRD:* <https://x|Checkout Abandonment>" },
        }],
        summary: "",
      },
    })
    render(<SlackSharePreviewCard {...props(preview)} />)
    expect((screen.getByTestId("slack-share-note") as HTMLTextAreaElement).value).toBe("")
  })

  it("warns about a self-join BEFORE the send, not after", () => {
    const preview = ready({
      channel: CHANNELS[1],
      warning: "Sprntly isn't in #product-leads yet — it will join the channel in order to post this.",
    })
    render(<SlackSharePreviewCard {...props(preview)} />)
    expect(screen.getByTestId("slack-share-warning").textContent).toContain("will join")
    // Still sendable — the auto-join recovers it.
    expect((screen.getByTestId("slack-share-send") as HTMLButtonElement).disabled).toBe(false)
  })

  it("goes inert while the send is in flight", () => {
    const p = props(ready(), { busy: true })
    render(<SlackSharePreviewCard {...p} />)
    const send = screen.getByTestId("slack-share-send") as HTMLButtonElement
    expect(send.disabled).toBe(true)
    expect(send.textContent).toContain("Posting…")
    fireEvent.click(send)
    expect(p.onSend).not.toHaveBeenCalled()
  })
})

describe("SlackSharePreviewCard — while the channel is still open", () => {
  function needsChannel(over: Partial<SlackSharePreview> = {}): SlackSharePreview {
    return ready({
      status: "needs_channel",
      channel: null,
      channels: CHANNELS,
      channel_status: "needs_channel",
      ...over,
    })
  }

  it("renders NO picker of its own — the popup asks", () => {
    // Owner's directive, 2026-08-16: one question surface. A row of channel
    // chips inside the card competed with the QuestionPopup that every other
    // choice in this product already uses.
    render(<SlackSharePreviewCard {...props(needsChannel(), { questionInPopup: true })} />)
    expect(screen.queryByTestId("slack-share-channel")).toBeNull()
    expect(screen.queryByTestId("slack-share-picker")).toBeNull()
    expect(screen.queryByTestId("slack-share-filter")).toBeNull()
  })

  it("offers no Send at all until a channel is resolved", () => {
    const p = props(needsChannel(), { questionInPopup: true })
    render(<SlackSharePreviewCard {...p} />)
    expect(screen.queryByTestId("slack-share-send")).toBeNull()
    expect(p.onSend).not.toHaveBeenCalled()
  })

  it("still shows the message, and the note stays editable", () => {
    // Choosing where to send something you cannot see is not a choice — and a
    // user can write what they want to say while deciding where it goes.
    render(<SlackSharePreviewCard {...props(needsChannel(), { questionInPopup: true })} />)
    expect(screen.getByTestId("slack-share-doc")).toBeTruthy()
    const note = screen.getByTestId("slack-share-note") as HTMLTextAreaElement
    fireEvent.change(note, { target: { value: "Typed while deciding." } })
    expect(note.value).toBe("Typed while deciding.")
  })

  it("says which name failed to match, rather than a bare error", () => {
    render(<SlackSharePreviewCard {...props(needsChannel({
      channel_query: "prodcut", channel_status: "not_found",
    }), { questionInPopup: true })} />)
    expect(screen.getByTestId("slack-share-preview").textContent).toContain("#prodcut")
  })

  it("says so plainly when there are no channels at all", () => {
    render(<SlackSharePreviewCard {...props(needsChannel({ channels: [] }),
      { questionInPopup: true })} />)
    expect(screen.getByTestId("slack-share-preview").textContent)
      .toContain("can't see any Slack channels")
  })

  it("once the channel resolves, the Send names it", () => {
    render(<SlackSharePreviewCard {...props(ready(), { questionInPopup: true })} />)
    expect(screen.getByTestId("slack-share-send").textContent).toContain("#product")
  })
})

describe("SlackSharePreviewCard — the cases that cannot proceed", () => {
  it("a blocked private channel offers no send", () => {
    const preview = ready({
      status: "blocked",
      channel: { id: "C3", name: "founders", is_private: true, is_member: false },
      warning: "Sprntly isn't in #founders, and it can't add itself to a private channel.",
    })
    render(<SlackSharePreviewCard {...props(preview)} />)
    expect(screen.getByTestId("slack-share-blocked")).toBeTruthy()
    expect(screen.queryByTestId("slack-share-send")).toBeNull()
  })

  it("an unshareable kind names the kind instead of substituting a document", () => {
    const preview = ready({
      status: "unsupported_type", target: null, named_type: "prototype",
    })
    render(<SlackSharePreviewCard {...props(preview)} />)
    expect(screen.getByTestId("slack-share-unsupported").textContent).toContain("prototype")
    expect(screen.queryByTestId("slack-share-send")).toBeNull()
  })

  it("an ambiguous match defers to the popup rather than listing chips", () => {
    const other: SlackShareTarget = { ...TARGET, id: 43, title: "Checkout Redesign" }
    render(<SlackSharePreviewCard {...props(ready({
      status: "ambiguous_target", target: null, candidates: [TARGET, other],
    }), { questionInPopup: true })} />)
    expect(screen.getByTestId("slack-share-popup-note").textContent).toContain("2 documents")
    expect(screen.queryByTestId("slack-share-target-option")).toBeNull()
  })

  it("a host that renders the choice itself still gets clickable candidates", () => {
    const other: SlackShareTarget = { ...TARGET, id: 43, title: "Checkout Redesign" }
    const p = props(ready({
      status: "ambiguous_target", target: null, candidates: [TARGET, other],
    }))
    render(<SlackSharePreviewCard {...p} />)
    const options = screen.getAllByTestId("slack-share-target-option")
    expect(options).toHaveLength(2)
    fireEvent.click(options[1])
    expect(p.onPickTarget).toHaveBeenCalledWith(other)
    expect(p.onSend).not.toHaveBeenCalled()
  })

  it("a document that matched nothing says so without offering a send", () => {
    const p = props(ready({ status: "needs_target", target: null, candidates: [] }))
    render(<SlackSharePreviewCard {...p} />)
    expect(screen.getByTestId("slack-share-pick-target").textContent)
      .toContain("couldn't find that document")
    expect(screen.queryByTestId("slack-share-send")).toBeNull()
  })
})

describe("SlackSharePreviewCard — the settled record", () => {
  it("a sent share records where it went", () => {
    render(<SlackSharePreviewCard
      {...props(ready(), { resolved: { outcome: "sent", channelName: "product" } })} />)
    expect(screen.getByTestId("slack-share-resolved").textContent).toContain("#product")
    expect(screen.queryByTestId("slack-share-send")).toBeNull()
  })

  it("a cancelled share says explicitly that nothing was posted", () => {
    // The one thing the thread must never leave ambiguous.
    render(<SlackSharePreviewCard
      {...props(ready(), { resolved: { outcome: "cancelled" } })} />)
    expect(screen.getByTestId("slack-share-resolved").textContent)
      .toContain("nothing was posted")
  })

  it("a failed share reports Slack's own reason", () => {
    render(<SlackSharePreviewCard {...props(ready(), {
      resolved: { outcome: "failed", error: "channel_not_found" },
    })} />)
    expect(screen.getByTestId("slack-share-resolved").textContent)
      .toContain("channel_not_found")
  })
})
