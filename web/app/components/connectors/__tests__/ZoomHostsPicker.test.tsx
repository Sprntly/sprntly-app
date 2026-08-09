// Same node-env SSR pattern as the other connector component tests.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import type { ZoomUser } from "../../../lib/api"
import { ZoomHostsPickerView } from "../ZoomHostsPicker"

const HOSTS: ZoomUser[] = [
  {
    id: "u1",
    email: "sam@acme.co",
    display_name: "Sam Lee",
    licensed: true,
    recording_count: null,
  },
  {
    id: "u2",
    email: "kim@acme.co",
    display_name: "Kim Patel",
    licensed: true,
    recording_count: null,
  },
]

const noop = () => {}

type Props = React.ComponentProps<typeof ZoomHostsPickerView>

function render(override: Partial<Props> = {}): string {
  const defaults: Props = {
    hosts: HOSTS,
    ghosts: [],
    loading: false,
    error: null,
    selectedIds: new Set<string>(),
    savedCount: 0,
    isSaving: false,
    fetchCapped: false,
    canManage: true,
    authExpired: false,
    filter: "",
    onFilterChange: noop,
    onToggle: noop,
    onSave: noop,
    onClear: noop,
  }
  return renderToStaticMarkup(
    React.createElement(ZoomHostsPickerView, { ...defaults, ...override }),
  )
}

describe("ZoomHostsPickerView", () => {
  it("renders a row per host, labelled with the name AND the email", () => {
    // A Zoom account routinely holds two people with the same display name;
    // the email is the only thing that tells them apart, and a screen-reader
    // user has nothing else to go on.
    const html = render()
    expect(html).toContain("Sam Lee (sam@acme.co)")
    expect(html).toContain("Kim Patel (kim@acme.co)")
    expect(html.match(/type="checkbox"/g)).toHaveLength(2)
  })

  it("renders the degraded recording-count line rather than hiding the host", () => {
    // recording_count is null in this version. A host we cannot count is not
    // a host to hide — the row stays rendered and selectable.
    const html = render()
    expect(html).toContain("sam@acme.co · Recording count unavailable")
    expect(html).not.toContain("undefined")
    expect(html).not.toContain("null")
  })

  it("shows a real count once one exists", () => {
    const html = render({
      hosts: [{ ...HOSTS[0], recording_count: 1 }],
    })
    expect(html).toContain("sam@acme.co · 1 recording")
  })

  it("explains the no-selection default (every licensed host)", () => {
    const html = render()
    expect(html).toContain("Hosts to sync")
    expect(html).toContain("every licensed host on the Zoom account syncs")
  })

  it("says what Sprntly reads — and does NOT claim Confluence's permission model", () => {
    // Confluence's picker says "Sprntly sees exactly what the person who
    // connected can see". That sentence is FALSE for Zoom: the scopes are all
    // :admin, so one connection reaches every host regardless of who clicked
    // Connect. Shipping it here would be a lie about data access.
    const html = render()
    expect(html).toContain(
      "Sprntly reads transcripts and meeting details only — never the recording video or audio.",
    )
    expect(html).not.toContain("sees exactly what the person who connected")
  })

  it("hides the saved line at zero and matches singular/plural", () => {
    expect(render({ savedCount: 0 })).not.toContain("Syncing <strong>")
    expect(render({ savedCount: 1 })).toContain("host</div>")
    expect(render({ savedCount: 1 })).not.toContain("hosts</div>")
    expect(render({ savedCount: 3 })).toContain("hosts</div>")
  })

  it("ticks the selected ids", () => {
    const html = render({ selectedIds: new Set(["u2"]) })
    const rows = html.split("<label")
    const sam = rows.find((r) => r.includes("Sam Lee")) ?? ""
    const kim = rows.find((r) => r.includes("Kim Patel")) ?? ""
    expect(sam).not.toContain("checked")
    expect(kim).toContain("checked")
  })

  it("disables and relabels Save while saving, and marks it busy", () => {
    const html = render({ isSaving: true })
    expect(html).toContain("Saving…")
    expect(html).toContain("aria-busy=\"true\"")
    expect(html).toMatch(/Saving…[\s\S]*?<\/button>/)
    expect(html).toContain("disabled")
  })

  it("disables Save while the connection needs reconnecting", () => {
    // Saving a selection against a dead token just fails — better to not
    // offer it than to offer it and error.
    const html = render({ authExpired: true })
    const saveBtn = html.slice(html.indexOf("Save hosts") - 200)
    expect(saveBtn).toContain("disabled")
  })

  it("offers a filter input with an accessible name", () => {
    const html = render()
    expect(html).toContain('type="search"')
    expect(html).toContain('aria-label="Filter hosts"')
    expect(html).toContain("Filter hosts by name or email")
  })

  it("narrows the list to the filter query, on name or email", () => {
    const byName = render({ filter: "kim" })
    expect(byName).toContain("Kim Patel")
    expect(byName).not.toContain("Sam Lee")

    const byEmail = render({ filter: "sam@" })
    expect(byEmail).toContain("Sam Lee")
    expect(byEmail).not.toContain("Kim Patel")
  })

  it("says so when the filter matches nothing", () => {
    const html = render({ filter: "nobody" })
    expect(html).toContain("No hosts match")
    expect(html).toContain("nobody")
    expect(html).toContain("conn-slack-empty")
  })

  it("renders a saved host missing from the live list as a ghost row", () => {
    // The puller still syncs this host, so dropping the row would misreport
    // what Sprntly is doing — and there would be no way to deselect them.
    const html = render({
      ghosts: [{ id: "gone-1", name: "left@acme.co" }],
      selectedIds: new Set(["gone-1"]),
    })
    expect(html).toContain("left@acme.co — no longer a licensed Zoom user")
    expect(html).toContain("conn-zoom-check--ghost")
    const ghostRow = html.slice(html.indexOf("conn-zoom-check--ghost"))
    expect(ghostRow).toContain("checked")
    // Still removable — the checkbox is not disabled for an admin.
    expect(ghostRow.slice(0, ghostRow.indexOf("</label>"))).not.toContain(
      "disabled",
    )
  })

  it("falls back to the id when a ghost host has no stored name", () => {
    const html = render({
      ghosts: [{ id: "gone-2", name: "gone-2" }],
      selectedIds: new Set(["gone-2"]),
    })
    expect(html).toContain("gone-2 — no longer a licensed Zoom user")
  })

  it("is read-only for a non-admin, with a real disabled attribute", () => {
    const html = render({ canManage: false, selectedIds: new Set(["u1"]) })
    expect(html.match(/disabled=""/g)?.length).toBe(2)
    expect(html).toContain("Only a workspace admin can change which hosts sync.")
    expect(html).not.toContain("Save hosts")
    expect(html).not.toContain("Clear selection")
    // Not aria-disabled theatre — a real disabled input.
    expect(html).not.toContain("aria-disabled")
  })

  it("offers Clear only when something is ticked", () => {
    expect(render({ selectedIds: new Set<string>() })).not.toContain(
      "Clear selection",
    )
    expect(render({ selectedIds: new Set(["u1"]) })).toContain(
      "Clear selection — sync all hosts",
    )
  })

  it("says the list is partial when Zoom had more hosts than one pass", () => {
    const html = render({ fetchCapped: true })
    expect(html).toContain("Showing the first 2 licensed hosts")
    expect(html).toContain("Use the filter to find a specific host")
  })

  it("explains the empty state in terms of Zoom licensing", () => {
    const html = render({ hosts: [], ghosts: [] })
    expect(html).toContain("No licensed Zoom users found")
    expect(html).toContain("Cloud recording requires a paid Zoom licence")
    // No filter to offer over an empty list.
    expect(html).not.toContain('aria-label="Filter hosts"')
  })

  it("renders an inline error as an alert", () => {
    const html = render({
      error: "Only a workspace admin can change which hosts sync.",
    })
    expect(html).toContain('role="alert"')
    expect(html).toContain("Only a workspace admin can change which hosts sync.")
  })

  it("shows a loading line rather than an empty state while fetching", () => {
    const html = render({ loading: true, hosts: [] })
    expect(html).toContain("Loading hosts…")
    expect(html).not.toContain("No licensed Zoom users found")
  })
})
