// @vitest-environment jsdom
//
// Settings → Top Insights (per-user). The pane reads/writes the member's
// insight-type selection through user_insight_prefs (fetch/saveInsightPrefs),
// NOT the workspace — the brief stays workspace-wide, each member filters their
// own view. These prove: saved prefs seed the chips on mount; toggling arms
// Save; and Save persists the selection per-user keyed by (company id, user id).
import * as React from "react"
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const useWorkspaceMock = vi.fn()
const authMock = vi.fn()
const refreshMock = vi.fn(() => Promise.resolve())
const fetchInsightPrefsMock = vi.fn()
const saveInsightPrefsMock = vi.fn()

vi.mock("../../../../../context/WorkspaceContext", () => ({
  useWorkspace: () => useWorkspaceMock(),
  profileDisplayName: () => null,
}))
vi.mock("../../../../../lib/auth", () => ({ useAuth: () => authMock() }))
vi.mock("../../../../../lib/onboarding/insightPrefs", () => ({
  fetchInsightPrefs: (...a: unknown[]) => fetchInsightPrefsMock(...a),
  saveInsightPrefs: (...a: unknown[]) => saveInsightPrefsMock(...a),
}))

import { TopInsightsSettings } from "../TopInsightsSettings"

function mount(prefs: { insightTypes: string[]; note: string | null } = { insightTypes: [], note: null }) {
  useWorkspaceMock.mockReturnValue({
    workspace: { id: "co-1", notification_settings: {} },
    profile: { email: "pm@acme.com" },
    loading: false,
    refresh: refreshMock,
  })
  authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
  fetchInsightPrefsMock.mockResolvedValue(prefs)
  saveInsightPrefsMock.mockResolvedValue({ insightTypes: [], note: null })
  return render(React.createElement(TopInsightsSettings))
}

function chip(label: string): HTMLButtonElement {
  const btn = Array.from(document.querySelectorAll(".metric-chips button")).find((b) =>
    (b.textContent ?? "").includes(label),
  )
  if (!btn) throw new Error(`chip "${label}" not found`)
  return btn as HTMLButtonElement
}

function saveBtn(): HTMLButtonElement {
  const el = document.querySelector("button.pset-save") as HTMLButtonElement | null
  if (!el) throw new Error("Save button not found")
  return el
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("TopInsightsSettings (Settings → Top Insights)", () => {
  it("renders all six canonical insight-type chips", async () => {
    mount()
    await waitFor(() => expect(fetchInsightPrefsMock).toHaveBeenCalledWith("co-1", "u-1"))
    expect(chip("Top user problems & opportunities")).toBeTruthy()
    expect(chip("Most important to build")).toBeTruthy()
    expect(chip("User feedback & complaints")).toBeTruthy()
    expect(chip("Competitor & market moves")).toBeTruthy()
    expect(chip("Reliability & incident signals")).toBeTruthy()
    expect(chip("Wins to celebrate")).toBeTruthy()
  })

  it("seeds saved prefs as selected chips on mount", async () => {
    mount({ insightTypes: ["competitor_moves"], note: null })
    await waitFor(() =>
      expect(chip("Competitor & market moves").getAttribute("aria-pressed")).toBe("true"),
    )
    expect(chip("Wins to celebrate").getAttribute("aria-pressed")).toBe("false")
  })

  it("toggling a chip and Save persists the selection per-user", async () => {
    mount({ insightTypes: [], note: null })
    await waitFor(() => expect(fetchInsightPrefsMock).toHaveBeenCalled())

    fireEvent.click(chip("Competitor & market moves"))
    fireEvent.click(chip("Wins to celebrate"))

    await act(async () => {
      saveBtn().click()
    })

    await waitFor(() => expect(saveInsightPrefsMock).toHaveBeenCalled())
    const [companyId, userId, payload] = saveInsightPrefsMock.mock.calls[0]
    expect(companyId).toBe("co-1")
    expect(userId).toBe("u-1")
    expect(payload.insightTypes).toEqual(["competitor_moves", "wins"])
  })
})
