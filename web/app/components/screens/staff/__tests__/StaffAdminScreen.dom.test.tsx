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

import { StaffAdminScreen, keyModeLabel } from "../StaffAdminScreen"

const ACME = {
  id: "co-1",
  slug: "acme",
  display_name: "Acme Corp",
  created_at: "2026-07-01T00:00:00Z",
  seat_limit: 5,
  prototype_enabled: true,
  use_platform_key: true,
  feature_flags: { weekly_brief: true, research_agent: false },
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
    // Only enabled modules are summarized.
    expect(screen.getByText("Weekly Brief")).toBeTruthy()
    expect(screen.queryByText(/Research Agent/)).toBeNull()
  })

  it("saves entitlement edits through staffApi.updateCompany", async () => {
    listCompanies.mockResolvedValue({ companies: [ACME] })
    listInvites.mockResolvedValue({ invites: [] })
    updateCompany.mockResolvedValue({ ...ACME, prototype_enabled: false })
    await mount()

    fireEvent.click(screen.getByText("Edit"))
    // Toggle the prototype feature off.
    fireEvent.click(screen.getByLabelText(/Prototype feature/))
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
    expect(screen.getByText("Prototype off")).toBeTruthy()
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
    fireEvent.click(screen.getByLabelText(/Prototype feature/))
    await act(async () => {
      fireEvent.click(screen.getByText("Send invite"))
    })

    expect(createInvite).toHaveBeenCalledWith(
      expect.objectContaining({
        email: "admin@customer.com",
        company_name: "Customer Inc",
        seat_limit: null,
        prototype_enabled: true,
        use_platform_key: false,
        feature_flags: expect.objectContaining({ weekly_brief: true }),
      }),
    )
    expect(screen.getByText(/Invite sent to admin@customer.com/)).toBeTruthy()
    // The new invite lands in the pending list.
    expect(screen.getByText("Customer Inc")).toBeTruthy()
    expect(screen.getByText("Pending")).toBeTruthy()
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
