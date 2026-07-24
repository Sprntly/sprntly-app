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
  profileDisplayName: () => null,
}))
vi.mock("../../../../../lib/onboarding/store", () => ({
  updateWorkspace: (...a: unknown[]) => updateWorkspaceMock(...a),
}))

// The pane now fetches the per-user Slack connection + can launch OAuth. Mock
// the API surface so these DOM tests stay focused on the schedule/email form.
const listMock = vi.fn(() => Promise.resolve({ connections: [] }))
vi.mock("../../../../../lib/api", () => ({
  connectorsApi: {
    list: () => listMock(),
    startOauth: vi.fn(() => Promise.resolve({ authorize_url: "" })),
    disconnectSlack: vi.fn(() => Promise.resolve({ deleted: true })),
  },
  ApiError: class ApiError extends Error {
    status = 0
    body: unknown = null
  },
  apiErrorMessage: (_s: number, _b: unknown) => "error",
}))
vi.mock("../../../../connectors/SlackChannelPicker", () => ({
  SlackChannelPicker: () => null,
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

/** A Schedule-card dropdown by id — stable as the card's layout changes. */
function sel(id: string): HTMLSelectElement {
  const el = document.getElementById(id) as HTMLSelectElement | null
  if (!el) throw new Error(`select #${id} not found`)
  return el
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

    // User turns the digest ON (arming the top bar's Save), then clicks Save.
    await act(async () => {
      fireEvent.click(toggle)
    })
    expect(emailToggle().getAttribute("aria-pressed")).toBe("true")

    const saveBtn = screen.getByRole("button", { name: /save changes/i })
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

describe("NotificationsSettings — schedule (when)", () => {
  it("loads saved day/hour/timezone and persists them + merges existing keys", async () => {
    mountWith({
      email_enabled: true, // schedule form only renders when the digest is ON
      brief_weekday: 2,
      brief_hour: 14,
      timezone: "America/New_York",
      email_recipients: ["a@co.com"], // must be preserved on save
    })

    // The Schedule card is always visible now (no longer gated behind the
    // email toggle) — selects reflect the saved values (Wednesday / 2 PM / NY).
    // Looked up by id, not by position: the card gained a Frequency dropdown
    // ahead of Day, and positional indexing silently retargets when that moves.
    const daySel = sel("comms-day")
    const hourSel = sel("comms-time")
    const tzSel = sel("comms-tz")
    expect(daySel.value).toBe("2")
    expect(hourSel.value).toBe("14")
    expect(tzSel.value).toBe("America/New_York")

    // Save is dirty-gated in the top bar, so change the day first (Wed → Thu).
    await act(async () => {
      fireEvent.change(daySel, { target: { value: "3" } })
    })
    const saveBtn = screen.getByRole("button", { name: /save changes/i })
    await act(async () => {
      fireEvent.click(saveBtn)
    })
    await waitFor(() => expect(updateWorkspaceMock).toHaveBeenCalledTimes(1))
    const [, patch] = updateWorkspaceMock.mock.calls[0] as [string, Notif]
    const ns = patch.notification_settings as Notif
    expect(ns.brief_weekday).toBe(3)
    expect(ns.brief_hour).toBe(14)
    expect(ns.brief_minute).toBe(0)
    expect(ns.timezone).toBe("America/New_York")
    // Untouched keys survive the merge.
    expect(ns.email_recipients).toEqual(["a@co.com"])
  })
})

describe("NotificationsSettings — brief frequency", () => {
  it("defaults to Weekly when nothing is stored, so existing schedules don't shift", () => {
    mountWith({ brief_weekday: 0, brief_hour: 6 })
    expect(sel("comms-frequency").value).toBe("weekly")
    // Day still applies and still shows the stored Monday.
    expect(sel("comms-day").value).toBe("0")
  })

  it("offers exactly the four supported cadences", () => {
    mountWith({})
    const opts = Array.from(sel("comms-frequency").options).map((o) => [o.value, o.text])
    expect(opts).toEqual([
      ["daily_weekdays", "Daily (weekdays)"],
      ["weekly", "Weekly"],
      ["biweekly", "Every other week"],
      ["monthly", "Monthly"],
    ])
  })

  it("loads a stored non-weekly cadence", () => {
    mountWith({ brief_frequency: "monthly" })
    expect(sel("comms-frequency").value).toBe("monthly")
  })

  it("falls back to Weekly for an unrecognised stored value", () => {
    mountWith({ brief_frequency: "fortnightly-ish" })
    expect(sel("comms-frequency").value).toBe("weekly")
  })

  it("HIDES the Day dropdown for Daily (weekdays) and restores it on switch back", async () => {
    mountWith({ brief_frequency: "weekly" })
    expect(document.getElementById("comms-day")).not.toBeNull()

    await act(async () => {
      fireEvent.change(sel("comms-frequency"), { target: { value: "daily_weekdays" } })
    })
    // Mon–Fri leaves the Day picker with nothing to say, so it's gone —
    // Time + Timezone stay.
    expect(document.getElementById("comms-day")).toBeNull()
    expect(document.getElementById("comms-time")).not.toBeNull()
    expect(document.getElementById("comms-tz")).not.toBeNull()

    await act(async () => {
      fireEvent.change(sel("comms-frequency"), { target: { value: "biweekly" } })
    })
    expect(document.getElementById("comms-day")).not.toBeNull()
  })

  it("relabels the Day options for Monthly (first <day> of the month)", async () => {
    mountWith({ brief_frequency: "weekly" })
    expect(sel("comms-day").options[0].text).toBe("Mondays")

    await act(async () => {
      fireEvent.change(sel("comms-frequency"), { target: { value: "monthly" } })
    })
    expect(sel("comms-day").options[0].text).toBe("First Monday")
  })

  it("persists the cadence + a biweekly anchor, merging existing keys", async () => {
    mountWith({ brief_weekday: 0, brief_hour: 6, timezone: "UTC", email_recipients: ["a@co.com"] })

    await act(async () => {
      fireEvent.change(sel("comms-frequency"), { target: { value: "biweekly" } })
    })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /save changes/i }))
    })

    await waitFor(() => expect(updateWorkspaceMock).toHaveBeenCalledTimes(1))
    const [, patch] = updateWorkspaceMock.mock.calls[0] as [string, Notif]
    const ns = patch.notification_settings as Notif
    expect(ns.brief_frequency).toBe("biweekly")
    // "Every other week" is only deterministic against a stored anchor.
    expect(ns.brief_anchor_date).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    expect(ns.brief_weekday).toBe(0)
    expect(ns.email_recipients).toEqual(["a@co.com"])
  })


  it("previews a SAVED biweekly schedule against its stored anchor, not a fresh one", () => {
    // Regression: recomputing the anchor on every render made a saved
    // biweekly schedule preview its OFF week as if it were an ON week. A
    // clean form must honour the persisted brief_anchor_date.
    // Anchor Mon 2026-07-20 + "now" is Mon 2026-07-27 (the OFF week) ⇒ the
    // next landing is Mon 2026-08-03, not today.
    vi.useFakeTimers()
    vi.setSystemTime(new Date("2026-07-27T00:00:00Z"))
    try {
      mountWith({
        brief_frequency: "biweekly",
        brief_anchor_date: "2026-07-20",
        brief_weekday: 0,
        brief_hour: 6,
        timezone: "UTC",
      })
      const line = document.querySelector(".pset-next-line")?.textContent ?? ""
      expect(line).toContain("Monday, August 3")
    } finally {
      vi.useRealTimers()
    }
  })

  it("offers Monday–Friday only — no Saturday or Sunday", () => {
    mountWith({})
    const opts = Array.from(sel("comms-day").options).map((o) => [o.value, o.text])
    expect(opts).toEqual([
      ["0", "Mondays"],
      ["1", "Tuesdays"],
      ["2", "Wednesdays"],
      ["3", "Thursdays"],
      ["4", "Fridays"],
    ])
    expect(sel("comms-day").textContent).not.toMatch(/Saturday|Sunday/)
  })

  it("coerces a legacy weekend day to Monday instead of rendering a missing option", () => {
    // brief_weekday 5 (Saturday) predates the weekday-only rule. Left as-is the
    // <select> would hold a value with no matching <option>.
    mountWith({ brief_weekday: 5, brief_hour: 6 })
    expect(sel("comms-day").value).toBe("0")
  })

  it("persists the coerced weekday once the user saves", async () => {
    mountWith({ brief_weekday: 6, brief_hour: 6, timezone: "UTC" })
    expect(sel("comms-day").value).toBe("0")
    // Save is dirty-gated, so make an unrelated change to arm it.
    await act(async () => {
      fireEvent.change(sel("comms-time"), { target: { value: "9" } })
    })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /save changes/i }))
    })
    await waitFor(() => expect(updateWorkspaceMock).toHaveBeenCalledTimes(1))
    const [, patch] = updateWorkspaceMock.mock.calls[0] as [string, Notif]
    expect((patch.notification_settings as Notif).brief_weekday).toBe(0)
  })

  it("arms Save when only the cadence changes", async () => {
    mountWith({ brief_frequency: "weekly" })
    // The bar always renders Save; it is dirty-gated via `disabled`.
    const save = () => screen.getByRole("button", { name: /save changes/i }) as HTMLButtonElement
    expect(save().disabled).toBe(true)
    await act(async () => {
      fireEvent.change(sel("comms-frequency"), { target: { value: "monthly" } })
    })
    expect(save().disabled).toBe(false)
  })
})

describe("NotificationsSettings — workspace Top Insights filter", () => {
  /** An insight-type chip button by its visible label. */
  function insightChip(label: string): HTMLButtonElement {
    const btn = Array.from(
      document.querySelectorAll('[data-field="insight-types"] button'),
    ).find((b) => (b.textContent ?? "").includes(label))
    if (!btn) throw new Error(`insight chip "${label}" not found`)
    return btn as HTMLButtonElement
  }

  it("loads a saved brief_insight_types selection as pressed chips", () => {
    mountWith({ brief_insight_types: ["wins", "user_feedback"] })
    expect(insightChip("Wins to celebrate").getAttribute("aria-pressed")).toBe("true")
    expect(insightChip("User feedback & complaints").getAttribute("aria-pressed")).toBe("true")
    expect(insightChip("Competitor & market moves").getAttribute("aria-pressed")).toBe("false")
  })

  it("persists the workspace selection + note under brief_insight_types/brief_insight_note, merging existing keys", async () => {
    mountWith({ email_recipients: ["a@co.com"] })
    // Empty by default — pick one type to arm Save.
    await act(async () => {
      fireEvent.click(insightChip("Wins to celebrate"))
    })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /save changes/i }))
    })
    await waitFor(() => expect(updateWorkspaceMock).toHaveBeenCalledTimes(1))
    const [, patch] = updateWorkspaceMock.mock.calls[0] as [string, Notif]
    const ns = patch.notification_settings as Notif
    expect(ns.brief_insight_types).toEqual(["wins"])
    expect(ns.brief_insight_note).toBeNull()
    // Untouched sibling key survives the merge.
    expect(ns.email_recipients).toEqual(["a@co.com"])
  })

  it("drops unknown stored slugs rather than rendering a phantom chip", () => {
    mountWith({ brief_insight_types: ["wins", "not_a_real_type"] })
    // Only the six canonical chips render; the bogus slug is filtered on load.
    const chips = document.querySelectorAll('[data-field="insight-types"] button')
    expect(chips.length).toBe(6)
    expect(insightChip("Wins to celebrate").getAttribute("aria-pressed")).toBe("true")
  })
})

describe("NotificationsSettings — copy", () => {
  it("renders the Top Product Insights heading and cadence subtitle", () => {
    mountWith({})
    expect(
      screen.getByText("Communications to you on the Top Product Insights"),
    ).toBeTruthy()
    expect(
      screen.getByText(
        /We send you notifications when we find insights about your business/,
      ),
    ).toBeTruthy()
    // The Brief concept is NOT renamed — only this page's wording changed.
    expect(document.body.textContent).toContain("Next Brief will land")
  })
})
