// Same node-env SSR pattern as the other connector component tests.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import type { SlackChannel } from "../../../lib/api"
import { SlackChannelPickerView } from "../SlackChannelPicker"

const CHANNELS: SlackChannel[] = [
  { id: "C1", name: "general", is_private: false, is_member: true, is_archived: false },
  { id: "C2", name: "design", is_private: true, is_member: true, is_archived: false },
]

const noop = () => {}

type Props = React.ComponentProps<typeof SlackChannelPickerView>

function render(override: Partial<Props> = {}): string {
  const defaults: Props = {
    channels: CHANNELS,
    loading: false,
    error: null,
    selectedChannelId: null,
    savedChannelName: null,
    isSaving: false,
    onSelect: noop,
    onSave: noop,
  }
  return renderToStaticMarkup(
    React.createElement(SlackChannelPickerView, { ...defaults, ...override }),
  )
}

describe("SlackChannelPickerView", () => {
  it("renders each non-archived channel with a # or 🔒 prefix", () => {
    const html = render()
    expect(html).toMatch(/#\s*general/)
    expect(html).toMatch(/🔒\s*design/) // private channel marked
  })

  it("disables Save until a channel is selected", () => {
    const html = render({ selectedChannelId: null })
    expect(html).toMatch(/<button[^>]*disabled[^>]*>Save channel<\/button>/)
  })

  it("enables Save when a channel is selected", () => {
    const html = render({ selectedChannelId: "C1" })
    // Negative: no `disabled` attr on the Save button
    expect(html).not.toMatch(/<button[^>]*disabled[^>]*>Save channel<\/button>/)
  })

  it("shows 'Saving…' and disables the button while a save is in flight", () => {
    const html = render({ selectedChannelId: "C1", isSaving: true })
    expect(html).toContain("Saving…")
    expect(html).toMatch(/<button[^>]*disabled[^>]*>Saving…<\/button>/)
  })

  it("shows the currently-saved channel name when one is set", () => {
    const html = render({ savedChannelName: "product-launches" })
    // The channel name is wrapped in <strong>, so the literal string
    // doesn't appear contiguous — match the structure instead.
    expect(html).toMatch(/Posting to\s*<strong>#product-launches<\/strong>/)
  })

  it("renders an empty-state hint when channels is empty and not loading", () => {
    const html = render({ channels: [] })
    expect(html).toContain("No channels visible")
    // Hint mentions inviting the bot — that's the typical fix
    expect(html.toLowerCase()).toContain("invite")
  })

  it("renders a loading hint while channels are being fetched", () => {
    const html = render({ channels: [], loading: true })
    expect(html).toContain("Loading channels…")
  })

  it("surfaces an error message when one is set", () => {
    const html = render({
      channels: [],
      error: "Bot token rejected — reconnect Slack.",
    })
    expect(html).toContain("Bot token rejected")
  })
})
