// @vitest-environment jsdom
//
// Round-trip tests for Settings → Notifications.
//
// Regression context: the "Email digest" toggle used to read/write the
// `notification_settings.email_digest` key, but the backend brief-delivery
// path (app/synthesis/email_delivery.py → deliver_brief_to_email) gates on
// `notification_settings.email_enabled`. The two never met, so flipping the
// toggle had ZERO effect on whether brief emails were sent — the reported
// "set notifications in settings doesn't work" bug. The pane now reads/writes
// `email_enabled` (with legacy `email_digest` honored on load).
//
// These tests prove:
//   (a) a saved `email_enabled: true` populates the toggle as pressed on mount;
//   (b) toggling + Save persists under the `email_enabled` key (NOT email_digest);
//   (c) the legacy `email_digest: true` key still populates the toggle on load;
//   (d) absent settings default the toggle OFF (matches backend default-OFF).
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const useWorkspaceMock = vi.fn()
const updateWorkspaceMock = vi.fn()
const refreshMock = vi.fn(() => Promise.resolve())

vi.mock("../../../../../context/WorkspaceContext", () => ({
  useWorkspace: () => useWorkspaceMock(),
}))
vi.mock("../../../../../lib/onboarding/store", () => ({
  updateWorkspace: (...a: unknown[]) => updateWorkspaceMock(...a),
}))

import { NotificationsSettings } from "../NotificationsSettings"

type Notif = Record<string, unknown>

function mountWith(notif: Notif | undefined) {
  useWorkspaceMock.mockReturnValue({
    workspace: { id: "co-1", notification_settings: notif },
    loading: false,
    refreshing: false,
    refresh: refreshMock,
  })
  return render(React.createElement(NotificationsSettings))
}

function emailToggle(): HTMLButtonElement {
  // The email-digest toggle is the first .toggle button in the pane.
  const el = document.querySelector("button.toggle") as HTMLButtonElement | null
  if (!el) throw new Error("email toggle not found")
  return el
}

beforeEach(() => {
  updateWorkspaceMock.mockResolvedValue({ id: "co-1" })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("NotificationsSettings — email toggle round-trip", () => {
  it("populates the toggle as ON when saved email_enabled is true", () => {
    mountWith({ email_enabled: true })
    expect(emailToggle().getAttribute("aria-pressed")).toBe("true")
  })

  it("defaults the toggle OFF when no notification settings are saved", () => {
    mountWith({})
    expect(emailToggle().getAttribute("aria-pressed")).toBe("false")
  })

  it("still honors the legacy email_digest key on load", () => {
    mountWith({ email_digest: true })
    expect(emailToggle().getAttribute("aria-pressed")).toBe("true")
  })

  it("saves under email_enabled (the key the backend delivery path reads), not email_digest", async () => {
    mountWith({ email_enabled: false })
    const toggle = emailToggle()
    expect(toggle.getAttribute("aria-pressed")).toBe("false")

    // User turns the digest ON, then clicks Save.
    await act(async () => {
      fireEvent.click(toggle)
    })
    expect(emailToggle().getAttribute("aria-pressed")).toBe("true")

    const saveBtn = screen.getByRole("button", { name: /save notifications/i })
    await act(async () => {
      fireEvent.click(saveBtn)
    })

    await waitFor(() => expect(updateWorkspaceMock).toHaveBeenCalledTimes(1))
    const [companyId, patch] = updateWorkspaceMock.mock.calls[0] as [string, Notif]
    expect(companyId).toBe("co-1")
    const ns = patch.notification_settings as Notif
    expect(ns.email_enabled).toBe(true)
    // The dead `email_digest` key must NOT be the thing we persist.
    expect(ns).not.toHaveProperty("email_digest")
    // refresh() is awaited after save so the toggle reflects persisted state.
    expect(refreshMock).toHaveBeenCalled()
  })
})
