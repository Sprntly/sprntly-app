// @vitest-environment jsdom
//
// Staff admin panel (/admin) — dedicated-credential login + org invites +
// per-company entitlements.
// Covers:
//   - the standalone login form: rendered when no staff token is stored
//     (never redirects to the normal app login), submits id + password
//     through staffAuth.login, and surfaces a generic error on 401,
//   - the invisible gate: a 401/404 from /v1/staff with a stored token
//     clears it (staffAuth.logout) and drops back to the login form,
//   - Sign out: clears the token and returns to the login form,
//   - the happy path: organizations render with member/seat counts, key-mode
//     and prototype chips, and enabled-module summary,
//   - the entitlement editor: saving PATCHes the staff API with seat_limit /
//     prototype_enabled / use_platform_key / feature_flags,
//   - the invite flow: submitting the form POSTs the entitlement snapshot and
//     the new invite appears in the pending list.
//
// staffApi/staffAuth are mocked at the lib/api boundary (the adjacent
// screens' mocking convention) so mounting hits no network or storage.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const {
  listCompanies,
  listInvites,
  updateCompany,
  createInvite,
  staffLogin,
  staffLogout,
  staffHasToken,
  FakeApiError,
} = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number
    body: unknown
    constructor(status: number) {
      super(`Request failed (${status})`)
      this.status = status
      this.body = null
    }
  }
  return {
    listCompanies: vi.fn(),
    listInvites: vi.fn(),
    updateCompany: vi.fn(),
    createInvite: vi.fn(),
    staffLogin: vi.fn(),
    staffLogout: vi.fn(),
    staffHasToken: vi.fn(),
    FakeApiError,
  }
})

vi.mock("../../../../lib/api", () => ({
  ApiError: FakeApiError,
  staffApi: {
    listCompanies,
    listInvites,
    updateCompany,
    createInvite,
    revokeInvite: vi.fn(),
    resendInvite: vi.fn(),
  },
  staffAuth: {
    login: staffLogin,
    logout: staffLogout,
    hasToken: staffHasToken,
  },
}))

import {
  StaffAdminScreen,
  MODULES,
  agentsEnabled,
  weeklyBriefEnabled,
  keyModeLabel,
} from "../StaffAdminScreen"

const ACME = {
  id: "co-1",
  slug: "acme",
  display_name: "Acme Corp",
  created_at: "2026-07-01T00:00:00Z",
  seat_limit: 5,
  prototype_enabled: true,
  use_platform_key: true,
  feature_flags: { agents: false, weekly_brief: true },
  llm_key_configured: false,
  member_count: 2,
  pending_invite_count: 1,
}

beforeEach(() => {
  // Most suites exercise the signed-in panel; the login suite overrides this.
  staffHasToken.mockReturnValue(true)
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

async function mount() {
  await act(async () => {
    render(<StaffAdminScreen />)
  })
}

describe("StaffAdminScreen login", () => {
  it("renders the standalone login form when no staff token is stored", async () => {
    staffHasToken.mockReturnValue(false)
    await mount()
    expect(screen.getByText("Sign in")).toBeTruthy()
    expect(screen.getByLabelText("ID")).toBeTruthy()
    expect(screen.getByLabelText("Password")).toBeTruthy()
    // No panel fetch happens while signed out.
    expect(listCompanies).not.toHaveBeenCalled()
    expect(listInvites).not.toHaveBeenCalled()
  })

  it("signs in through staffAuth.login and loads the panel", async () => {
    staffHasToken.mockReturnValue(false)
    staffLogin.mockResolvedValue({ token: "t", token_type: "bearer", expires_in: 43200 })
    listCompanies.mockResolvedValue({ companies: [] })
    listInvites.mockResolvedValue({ invites: [] })
    await mount()

    fireEvent.change(screen.getByLabelText("ID"), {
      target: { value: "sprntly-owner" },
    })
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "hunter2!" },
    })
    await act(async () => {
      fireEvent.click(screen.getByText("Sign in"))
    })

    expect(staffLogin).toHaveBeenCalledWith("sprntly-owner", "hunter2!")
    expect(screen.getByText("Sprntly Admin")).toBeTruthy()
    expect(screen.getByText("Sign out")).toBeTruthy()
  })

  it("shows a generic error on bad credentials (401)", async () => {
    staffHasToken.mockReturnValue(false)
    staffLogin.mockRejectedValue(new FakeApiError(401))
    await mount()

    fireEvent.change(screen.getByLabelText("ID"), { target: { value: "x" } })
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "y" },
    })
    await act(async () => {
      fireEvent.click(screen.getByText("Sign in"))
    })

    expect(screen.getByText("Invalid credentials.")).toBeTruthy()
    expect(listCompanies).not.toHaveBeenCalled()
  })
})

describe("StaffAdminScreen gate", () => {
  it("clears a rejected token (404) and drops to the login form", async () => {
    listCompanies.mockRejectedValue(new FakeApiError(404))
    listInvites.mockRejectedValue(new FakeApiError(404))
    await mount()
    expect(staffLogout).toHaveBeenCalled()
    expect(screen.getByText("Sign in")).toBeTruthy()
    // The signed-in panel (with its Sign out button) is gone.
    expect(screen.queryByText("Sign out")).toBeNull()
  })

  it("clears an expired token (401) and drops to the login form", async () => {
    listCompanies.mockRejectedValue(new FakeApiError(401))
    listInvites.mockRejectedValue(new FakeApiError(401))
    await mount()
    expect(staffLogout).toHaveBeenCalled()
    expect(screen.getByText("Sign in")).toBeTruthy()
  })

  it("offers a retry on non-auth errors", async () => {
    listCompanies.mockRejectedValue(new FakeApiError(500))
    listInvites.mockRejectedValue(new FakeApiError(500))
    await mount()
    expect(screen.getByText("Retry")).toBeTruthy()
    expect(staffLogout).not.toHaveBeenCalled()
  })

  it("Sign out clears the token and shows the login form", async () => {
    listCompanies.mockResolvedValue({ companies: [] })
    listInvites.mockResolvedValue({ invites: [] })
    await mount()

    fireEvent.click(screen.getByText("Sign out"))

    expect(staffLogout).toHaveBeenCalled()
    expect(screen.getByText("Sign in")).toBeTruthy()
    expect(screen.queryByText("Sign out")).toBeNull()
  })
})

describe("StaffAdminScreen organizations", () => {
  it("lists companies with counts, chips, and module summary", async () => {
    listCompanies.mockResolvedValue({ companies: [ACME] })
    listInvites.mockResolvedValue({ invites: [] })
    await mount()

    expect(screen.getByText("Sprntly Admin")).toBeTruthy()
    expect(screen.getByText("Acme Corp")).toBeTruthy()
    // 2 members of a 5-seat limit + 1 pending.
    expect(screen.getByText(/2 \/ 5 members/)).toBeTruthy()
    expect(screen.getByText(/1 pending/)).toBeTruthy()
    expect(screen.getByText("Prototype on")).toBeTruthy()
    expect(screen.getByText("Platform key")).toBeTruthy()
    // Only enabled modules are summarized — the three-module scheme in order
    // (agents is explicitly off; Prototype comes from prototype_enabled).
    expect(screen.getByText("Prototype, Weekly Brief")).toBeTruthy()
    expect(screen.queryByText(/Agents/)).toBeNull()
  })

  it("maps legacy-only flag rows onto the Agents chip at display time", async () => {
    // A pre-rework row: no `agents` key, but one of the old default-on agent
    // keys is on ⇒ the summary shows Agents (display-level mapping only).
    const legacy = {
      ...ACME,
      id: "co-2",
      display_name: "Legacy Corp",
      prototype_enabled: false,
      feature_flags: { weekly_brief: true, on_demand_analysis: true },
    }
    listCompanies.mockResolvedValue({ companies: [legacy] })
    listInvites.mockResolvedValue({ invites: [] })
    await mount()

    expect(screen.getByText("Agents, Weekly Brief")).toBeTruthy()
  })

  it("offers exactly the three modules — Agents, Prototype, Weekly Brief — in the editor", async () => {
    listCompanies.mockResolvedValue({ companies: [ACME] })
    listInvites.mockResolvedValue({ invites: [] })
    await mount()

    fireEvent.click(screen.getByText("Edit"))

    const modules = screen.getByRole("group", { name: "Modules" })
    const labels = Array.from(modules.querySelectorAll("label")).map(
      (l) => l.textContent ?? "",
    )
    expect(labels).toHaveLength(3)
    expect(labels[0]).toBe("Agents")
    expect(labels[1]).toMatch(/^Prototype/)
    expect(labels[2]).toBe("Weekly Brief")
    // The retired modules leave no dead UI behind.
    expect(screen.queryByText("On-call Agent")).toBeNull()
    expect(screen.queryByText("Claude Code Handoff")).toBeNull()
    expect(screen.queryByText("On-demand Analysis")).toBeNull()
    expect(screen.queryByText("Auto PRD Generation")).toBeNull()
    expect(screen.queryByText("Engineer Agent")).toBeNull()
    expect(screen.queryByText("Research Agent")).toBeNull()
  })

  it("saves entitlement edits through staffApi.updateCompany", async () => {
    listCompanies.mockResolvedValue({ companies: [ACME] })
    listInvites.mockResolvedValue({ invites: [] })
    updateCompany.mockResolvedValue({ ...ACME, prototype_enabled: false })
    await mount()

    fireEvent.click(screen.getByText("Edit"))
    // Toggle the Prototype module off — it still writes prototype_enabled
    // (the column), not feature_flags.
    fireEvent.click(screen.getByLabelText(/^Prototype/))
    await act(async () => {
      fireEvent.click(screen.getByText("Save changes"))
    })

    expect(updateCompany).toHaveBeenCalledWith(
      "co-1",
      expect.objectContaining({
        seat_limit: 5,
        prototype_enabled: false,
        use_platform_key: true,
        feature_flags: expect.objectContaining({ weekly_brief: true }),
      }),
    )
    const patch = updateCompany.mock.calls[0][1] as {
      feature_flags: Record<string, boolean>
    }
    expect("prototype_enabled" in patch.feature_flags).toBe(false)
    expect(screen.getByText("Prototype off")).toBeTruthy()
  })

  // ── Editor prefill = the chips' effective-state mapping ──
  // Regression: the editor used to read the RAW stored keys, so a
  // grandfathered row whose chip said "Agents" opened with the checkbox
  // unchecked.

  it("prefills the editor's Agents checkbox via the legacy mapping (chip parity)", async () => {
    const legacy = {
      ...ACME,
      id: "co-2",
      display_name: "Legacy Corp",
      // No `agents` key — only the old default-on capability keys.
      feature_flags: { on_demand_analysis: true, auto_prd_generation: true },
    }
    listCompanies.mockResolvedValue({ companies: [legacy] })
    listInvites.mockResolvedValue({ invites: [] })
    await mount()

    // The chip summary shows Agents on…
    expect(screen.getByText(/Agents/)).toBeTruthy()
    fireEvent.click(screen.getByText("Edit"))

    // …and the editor now agrees.
    const agents = screen.getByLabelText("Agents") as HTMLInputElement
    expect(agents.checked).toBe(true)
    // weekly_brief is missing too ⇒ ON per backend grandfathering.
    const brief = screen.getByLabelText("Weekly Brief") as HTMLInputElement
    expect(brief.checked).toBe(true)
  })

  it("keeps an explicit agents:false unchecked in the editor", async () => {
    // ACME stores agents:false, weekly_brief:true explicitly.
    listCompanies.mockResolvedValue({ companies: [ACME] })
    listInvites.mockResolvedValue({ invites: [] })
    await mount()

    fireEvent.click(screen.getByText("Edit"))

    expect((screen.getByLabelText("Agents") as HTMLInputElement).checked).toBe(
      false,
    )
    expect(
      (screen.getByLabelText("Weekly Brief") as HTMLInputElement).checked,
    ).toBe(true)
  })

  it("shows a missing weekly_brief key as ON — chip and editor alike", async () => {
    const grandfathered = {
      ...ACME,
      id: "co-3",
      display_name: "Old Corp",
      // Explicit agents, but no weekly_brief key at all.
      feature_flags: { agents: true },
    }
    listCompanies.mockResolvedValue({ companies: [grandfathered] })
    listInvites.mockResolvedValue({ invites: [] })
    await mount()

    expect(screen.getByText("Agents, Prototype, Weekly Brief")).toBeTruthy()
    fireEvent.click(screen.getByText("Edit"))
    expect(
      (screen.getByLabelText("Weekly Brief") as HTMLInputElement).checked,
    ).toBe(true)
  })

  it("saving without touching the modules sends the stored dict unchanged", async () => {
    const legacy = {
      ...ACME,
      id: "co-2",
      display_name: "Legacy Corp",
      feature_flags: { on_demand_analysis: true },
    }
    listCompanies.mockResolvedValue({ companies: [legacy] })
    listInvites.mockResolvedValue({ invites: [] })
    updateCompany.mockResolvedValue(legacy)
    await mount()

    fireEvent.click(screen.getByText("Edit"))
    await act(async () => {
      fireEvent.click(screen.getByText("Save changes"))
    })

    // Prefill is display-level only — no `agents`/`weekly_brief` keys get
    // injected into an untouched dict.
    const patch = updateCompany.mock.calls[0][1] as {
      feature_flags: Record<string, boolean>
    }
    expect(patch.feature_flags).toEqual({ on_demand_analysis: true })
  })

  it("toggling a prefilled-on legacy checkbox writes an explicit agents:false", async () => {
    const legacy = {
      ...ACME,
      id: "co-2",
      display_name: "Legacy Corp",
      feature_flags: { on_demand_analysis: true },
    }
    listCompanies.mockResolvedValue({ companies: [legacy] })
    listInvites.mockResolvedValue({ invites: [] })
    updateCompany.mockResolvedValue(legacy)
    await mount()

    fireEvent.click(screen.getByText("Edit"))
    // Checked via the legacy mapping; one click turns it explicitly OFF.
    fireEvent.click(screen.getByLabelText("Agents"))
    expect((screen.getByLabelText("Agents") as HTMLInputElement).checked).toBe(
      false,
    )
    await act(async () => {
      fireEvent.click(screen.getByText("Save changes"))
    })

    const patch = updateCompany.mock.calls[0][1] as {
      feature_flags: Record<string, boolean>
    }
    expect(patch.feature_flags).toEqual({
      on_demand_analysis: true,
      agents: false,
    })
  })
})

describe("StaffAdminScreen invites", () => {
  it("submits an org invite with its entitlement snapshot", async () => {
    listCompanies.mockResolvedValue({ companies: [] })
    listInvites.mockResolvedValue({ invites: [] })
    createInvite.mockResolvedValue({
      id: "inv-1",
      email: "admin@customer.com",
      company_name: "Customer Inc",
      seat_limit: null,
      prototype_enabled: true,
      use_platform_key: false,
      feature_flags: {},
      status: "pending",
      company_id: null,
      created_at: null,
      accepted_at: null,
      email_sent: true,
    })
    await mount()

    fireEvent.click(screen.getByText("+ Invite organization"))
    fireEvent.change(screen.getByPlaceholderText("admin@customer.com"), {
      target: { value: "admin@customer.com" },
    })
    fireEvent.change(screen.getByPlaceholderText("Acme Corp"), {
      target: { value: "Customer Inc" },
    })
    fireEvent.click(screen.getByLabelText(/^Prototype/))
    await act(async () => {
      fireEvent.click(screen.getByText("Send invite"))
    })

    // Both flag-backed modules default ON for new invites.
    expect(createInvite).toHaveBeenCalledWith(
      expect.objectContaining({
        email: "admin@customer.com",
        company_name: "Customer Inc",
        seat_limit: null,
        prototype_enabled: true,
        use_platform_key: false,
        feature_flags: { agents: true, weekly_brief: true },
      }),
    )
    expect(screen.getByText(/Invite sent to admin@customer.com/)).toBeTruthy()
    // The new invite lands in the pending list.
    expect(screen.getByText("Customer Inc")).toBeTruthy()
    expect(screen.getByText("Pending")).toBeTruthy()
  })
})

describe("MODULES + agentsEnabled", () => {
  it("keeps exactly the two flag-backed modules (Prototype is column-backed)", () => {
    expect(MODULES.map((m) => m.key)).toEqual(["agents", "weekly_brief"])
  })

  it("prefers an explicit agents key over the legacy fallback", () => {
    expect(agentsEnabled({ agents: true })).toBe(true)
    // Explicitly off wins even when legacy keys are on.
    expect(agentsEnabled({ agents: false, on_demand_analysis: true })).toBe(false)
  })

  it("falls back to the old default-on keys for legacy rows", () => {
    expect(agentsEnabled({ on_demand_analysis: true })).toBe(true)
    expect(agentsEnabled({ auto_prd_generation: true })).toBe(true)
    // The old default-OFF agent keys alone do not light Agents up.
    expect(agentsEnabled({ engineer_agent: true, research_agent: true })).toBe(false)
    expect(agentsEnabled({})).toBe(false)
  })
})

describe("weeklyBriefEnabled", () => {
  it("honors an explicit key and defaults a missing key to ON", () => {
    expect(weeklyBriefEnabled({ weekly_brief: true })).toBe(true)
    expect(weeklyBriefEnabled({ weekly_brief: false })).toBe(false)
    // Missing key = grandfathered ON (backend app/entitlements.py parity).
    expect(weeklyBriefEnabled({})).toBe(true)
    expect(weeklyBriefEnabled({ agents: false })).toBe(true)
  })
})

describe("keyModeLabel", () => {
  it("names the three key postures", () => {
    expect(
      keyModeLabel({ use_platform_key: true, llm_key_configured: false }),
    ).toBe("Platform key")
    expect(
      keyModeLabel({ use_platform_key: false, llm_key_configured: true }),
    ).toBe("Own key (set)")
    expect(
      keyModeLabel({ use_platform_key: false, llm_key_configured: false }),
    ).toBe("Own key (not set yet)")
  })
})
