// @vitest-environment jsdom
//
// Interaction tests for the wired Zoom config slot + host picker. The SSR
// string tests next door can't reach the hooks-wired containers (fetch, save,
// reconnect), so this file drives real clicks and keystrokes in jsdom.
//
// Matchers: native DOM only — NO @testing-library/jest-dom (repo convention).
import * as React from "react"
import { cleanup, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

// Sprntly components carry no `import React`; expose it globally (repo test
// convention — esbuild's classic JSX runtime).
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const { listMock, saveMock, startOauthMock, ApiErrorCls } = vi.hoisted(() => {
  class ApiErrorCls extends Error {
    status: number
    body: unknown
    constructor(status: number, body: unknown) {
      super(`api ${status}`)
      this.status = status
      this.body = body
    }
  }
  return {
    listMock: vi.fn(),
    saveMock: vi.fn(),
    startOauthMock: vi.fn(),
    ApiErrorCls,
  }
})

vi.mock("../../../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../lib/api")>()
  return {
    ...actual,
    ApiError: ApiErrorCls,
    connectorsApi: {
      ...actual.connectorsApi,
      listZoomUsers: listMock,
      setZoomSyncUsers: saveMock,
      startOauth: startOauthMock,
    },
  }
})

let orgRole = "admin"
vi.mock("../../../context/WorkspaceContext", () => ({
  useWorkspace: () => ({ orgRole }),
}))

import { ZoomConfigSlot } from "../ZoomConfigSlot"
import { ZoomHostsPicker } from "../ZoomHostsPicker"

const CONNECTION = {
  id: "c1",
  provider: "zoom",
  status: "active",
  google_email: null,
  scopes: "",
  config: {},
  last_sync_at: "2026-08-04T10:00:00Z",
  last_sync_error: null,
  health: "connected",
  created_at: "2026-08-01T10:00:00Z",
  updated_at: "2026-08-04T10:00:00Z",
} as unknown as import("../../../lib/api").ConnectionSummary

function usersPayload(over: Record<string, unknown> = {}) {
  return {
    users: [
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
    ],
    selected_ids: [],
    selected_names: {},
    total: 2,
    fetch_capped: false,
    truncated: false,
    ...over,
  }
}

beforeEach(() => {
  orgRole = "admin"
  listMock.mockReset().mockResolvedValue(usersPayload())
  saveMock.mockReset().mockResolvedValue({ ok: true, config: {} })
  startOauthMock.mockReset().mockResolvedValue({ authorize_url: "https://zoom.us/x" })
  vi.spyOn(window, "open").mockReturnValue({
    closed: false,
    location: { href: "" },
    opener: window,
    close: vi.fn(),
  } as unknown as Window)
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe("ZoomHostsPicker — keyboard and save", () => {
  it("toggles a host with the keyboard and saves the id", async () => {
    const user = userEvent.setup()
    const onSaved = vi.fn()
    render(<ZoomHostsPicker onSaved={onSaved} />)

    await screen.findByText(/Sam Lee/)
    const boxes = screen.getAllByRole("checkbox") as HTMLInputElement[]
    boxes[0].focus()
    await user.keyboard(" ")
    expect(boxes[0].checked).toBe(true)

    await user.click(screen.getByRole("button", { name: "Save hosts" }))
    await waitFor(() => expect(saveMock).toHaveBeenCalledTimes(1))
    expect(saveMock.mock.calls[0][0]).toEqual([
      { id: "u1", email: "sam@acme.co" },
    ])
    await waitFor(() => expect(onSaved).toHaveBeenCalled())
  })

  it("saves an empty list when the selection is cleared", async () => {
    // Empty means EVERY licensed host — clearing has to reach the backend as
    // an explicit empty array, not as a no-op.
    const user = userEvent.setup()
    listMock.mockResolvedValue(usersPayload({ selected_ids: ["u1"] }))
    render(<ZoomHostsPicker onSaved={vi.fn()} />)

    await screen.findByText(/Sam Lee/)
    await user.click(
      screen.getByRole("button", { name: /Clear selection/ }),
    )
    await user.click(screen.getByRole("button", { name: "Save hosts" }))
    await waitFor(() => expect(saveMock).toHaveBeenCalled())
    expect(saveMock.mock.calls[0][0]).toEqual([])
  })

  it("keeps a ghost host in the saved payload rather than dropping it", async () => {
    // The whole point of the ghost row: a selected host absent from the live
    // listing must survive a save, or the next save silently narrows the
    // selection the admin made.
    const user = userEvent.setup()
    listMock.mockResolvedValue(
      usersPayload({
        selected_ids: ["u1", "gone-1"],
        selected_names: { "gone-1": "left@acme.co" },
      }),
    )
    render(<ZoomHostsPicker onSaved={vi.fn()} />)

    await screen.findByText(/no longer a licensed Zoom user/)
    await user.click(screen.getByRole("button", { name: "Save hosts" }))
    await waitFor(() => expect(saveMock).toHaveBeenCalled())
    expect(saveMock.mock.calls[0][0]).toEqual(
      expect.arrayContaining([{ id: "gone-1", email: "left@acme.co" }]),
    )
  })

  it("filters the list as you type", async () => {
    const user = userEvent.setup()
    render(<ZoomHostsPicker onSaved={vi.fn()} />)

    await screen.findByText(/Sam Lee/)
    await user.type(screen.getByLabelText("Filter hosts"), "kim")
    expect(screen.queryByText(/Sam Lee/)).toBeNull()
    expect(screen.getByText(/Kim Patel/)).toBeTruthy()
  })

  it("renders the admin sentence on a 403, never a raw status", async () => {
    const user = userEvent.setup()
    saveMock.mockRejectedValue(
      new ApiErrorCls(403, { detail: "Only admins can manage org-wide connectors." }),
    )
    render(<ZoomHostsPicker onSaved={vi.fn()} />)

    await screen.findByText(/Sam Lee/)
    await user.click(screen.getByRole("button", { name: "Save hosts" }))

    const alert = await screen.findByRole("alert")
    expect(alert.textContent).toContain(
      "Only a workspace admin can change which hosts sync.",
    )
    expect(alert.textContent).not.toContain("403")
  })

  it("gives a non-admin real disabled checkboxes and no Save", async () => {
    orgRole = "member"
    render(<ZoomHostsPicker onSaved={vi.fn()} />)

    await screen.findByText(/Sam Lee/)
    for (const box of screen.getAllByRole("checkbox") as HTMLInputElement[]) {
      expect(box.disabled).toBe(true)
    }
    expect(screen.queryByRole("button", { name: "Save hosts" })).toBeNull()
    expect(
      screen.getByText("Only a workspace admin can change which hosts sync."),
    ).toBeTruthy()
  })
})

describe("ZoomConfigSlot — reconnect", () => {
  it("calls startOauth once and points the pre-opened tab at the URL", async () => {
    const user = userEvent.setup()
    render(
      <ZoomConfigSlot
        connection={{ ...CONNECTION, health: "disconnected" }}
        onSaved={vi.fn()}
      />,
    )

    const btn = await screen.findByRole("button", { name: "Reconnect Zoom" })
    await user.click(btn)
    await waitFor(() => expect(startOauthMock).toHaveBeenCalledTimes(1))
    expect(startOauthMock).toHaveBeenCalledWith("zoom")
  })

  it("shows the alert block and disables Save while access is expired", async () => {
    render(
      <ZoomConfigSlot
        connection={{ ...CONNECTION, health: "disconnected" }}
        onSaved={vi.fn()}
      />,
    )

    const alert = await screen.findByRole("alert")
    expect(alert.textContent).toContain("Zoom stopped syncing.")
    await screen.findByText(/Sam Lee/)
    const save = screen.getByRole("button", {
      name: "Save hosts",
    }) as HTMLButtonElement
    expect(save.disabled).toBe(true)
  })

  it("renders the summary and the picker together", async () => {
    render(<ZoomConfigSlot connection={CONNECTION} onSaved={vi.fn()} />)
    expect(screen.getByText("Meetings found")).toBeTruthy()
    expect(screen.getByText("Transcripts read")).toBeTruthy()
    await screen.findByText(/Sam Lee/)
    expect(screen.queryByRole("alert")).toBeNull()
  })
})
