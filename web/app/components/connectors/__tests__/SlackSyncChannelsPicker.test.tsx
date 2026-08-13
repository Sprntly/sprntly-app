// Same node-env SSR pattern as the other connector component tests.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import type { SlackChannel } from "../../../lib/api"
import {
  orderBySavedFirst,
  SlackSyncChannelsPickerView,
} from "../SlackSyncChannelsPicker"

const CHANNELS: SlackChannel[] = [
  { id: "C1", name: "general", is_private: false, is_member: true, is_archived: false },
  { id: "C2", name: "customer-vips", is_private: true, is_member: true, is_archived: false },
]

const noop = () => {}

type Props = React.ComponentProps<typeof SlackSyncChannelsPickerView>

function render(override: Partial<Props> = {}): string {
  const defaults: Props = {
    channels: CHANNELS,
    loading: false,
    error: null,
    selectedIds: new Set<string>(),
    savedCount: 0,
    isSaving: false,
    onToggle: noop,
    onSave: noop,
  }
  return renderToStaticMarkup(
    React.createElement(SlackSyncChannelsPickerView, { ...defaults, ...override }),
  )
}

describe("SlackSyncChannelsPickerView", () => {
  it("renders a checkbox per channel with a # or 🔒 prefix", () => {
    const html = render()
    expect(html).toMatch(/#\s*general/)
    expect(html).toMatch(/🔒\s*customer-vips/) // private channel marked
    expect(html.match(/type="checkbox"/g)).toHaveLength(2)
  })

  it("explains the no-selection default (nothing is read until channels are picked)", () => {
    const html = render()
    expect(html).toContain("Channels to pull from")
    // The 2026-08-13 scope rule: with nothing ticked the sync reads nothing.
    // The old "every channel the bot has been invited to" promise must be gone.
    expect(html).toContain("no channels are read")
    expect(html).not.toContain("every channel the bot has been invited to")
  })

  it("says what ticking a channel actually does, and keeps both warnings", () => {
    const html = render()
    // What gets pulled, in the user's words — not just "reads messages".
    expect(html).toContain("customer feedback and conversations")
    expect(html).toContain("knowledge base")
    // Unticking is destructive on the backend; the hint must still say so.
    expect(html).toContain("deletes the messages already pulled")
    // Workspace-wide and admin-gated.
    expect(html).toContain("only admins can change")
  })

  it("ticks the checkboxes for selected channel ids", () => {
    const html = render({ selectedIds: new Set(["C2"]) })
    // Exactly one checked box — the selected private channel.
    expect(html.match(/checked/g)).toHaveLength(1)
  })

  it("shows how many channels the persisted selection has", () => {
    const html = render({ savedCount: 3 })
    expect(html).toMatch(/Pulling from\s*<strong>3<\/strong>\s*channels/)
  })

  it("uses the singular for a one-channel selection", () => {
    const html = render({ savedCount: 1 })
    expect(html).toMatch(/Pulling from\s*<strong>1<\/strong>\s*channel</)
  })

  it("hides the saved line when nothing is persisted (nothing-synced state)", () => {
    const html = render({ savedCount: 0 })
    expect(html).not.toContain("Pulling from")
  })

  it("shows 'Saving…' and disables the button while a save is in flight", () => {
    const html = render({ isSaving: true })
    expect(html).toMatch(/<button[^>]*disabled[^>]*>Saving…<\/button>/)
  })

  it("renders an empty-state hint when channels is empty and not loading", () => {
    const html = render({ channels: [] })
    expect(html).toContain("No channels visible")
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

describe("orderBySavedFirst", () => {
  // The checklist is a 240px scroll box (~5 rows). These pin the ordering that
  // keeps the persisted selection above the fold.
  const ch = (id: string): SlackChannel => ({
    id,
    name: id.toLowerCase(),
    is_private: false,
    is_member: true,
    is_archived: false,
  })
  const ids = (cs: SlackChannel[]) => cs.map((c) => c.id)

  it("lifts the saved channels to the top", () => {
    const channels = [ch("A"), ch("B"), ch("C"), ch("D"), ch("E"), ch("F"), ch("G")]
    // The real shape of the bug: the only ticked channel was last of seven.
    expect(ids(orderBySavedFirst(channels, new Set(["G"])))).toEqual([
      "G", "A", "B", "C", "D", "E", "F",
    ])
  })

  it("keeps source order within the saved group and within the rest", () => {
    const channels = [ch("A"), ch("B"), ch("C"), ch("D")]
    expect(ids(orderBySavedFirst(channels, new Set(["D", "B"])))).toEqual([
      "B", "D", "A", "C",
    ])
  })

  it("returns the list untouched when nothing is saved", () => {
    // Nothing saved means nothing is synced yet — there is no selection to
    // surface, so the list must not be reordered for its own sake.
    const channels = [ch("A"), ch("B")]
    expect(orderBySavedFirst(channels, new Set())).toBe(channels)
  })

  it("ignores saved ids the channel list does not contain", () => {
    // A previously saved private channel the bot can no longer see. Dropping
    // or duplicating it here would misreport the selection.
    const channels = [ch("A"), ch("B")]
    expect(ids(orderBySavedFirst(channels, new Set(["ZZ", "B"])))).toEqual(["B", "A"])
  })

  it("does not mutate the array it was given", () => {
    // The caller passes component state straight in.
    const channels = [ch("A"), ch("B"), ch("C")]
    orderBySavedFirst(channels, new Set(["C"]))
    expect(ids(channels)).toEqual(["A", "B", "C"])
  })
})
