// @vitest-environment node
//
// Unit tests for notAuthorizedContinuePath() — NotAuthorizedScreen's
// "Continue to your workspace" destination. Computed purely from the
// signed-in user's OWN account state (role + workspace_members row), never
// from the share token that just blocked them (see the function's own
// docstring for why that guarantees no loop back to /not-authorized).
//
// Mocking mirrors postLoginPath.test.ts's shape: createClient stubbed so
// getSupabase() resolves to our fake client, fetchWorkspaceForUser mocked
// for the "has-company-no-workspace" onboarding-step lookup.
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest"

const getUserMock = vi.fn()
const companyMembersMaybeSingleMock = vi.fn()
const workspaceMembersMaybeSingleMock = vi.fn()
const profileMaybeSingleMock = vi.fn()
const fetchWorkspaceMock = vi.fn()

beforeAll(() => {
  process.env.NEXT_PUBLIC_SUPABASE_URL = "https://example.supabase.co"
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY = "anon-key"
})

function maybeSingleFor(table: string) {
  return (...a: unknown[]) => {
    if (table === "company_members") return companyMembersMaybeSingleMock(...a)
    if (table === "workspace_members") return workspaceMembersMaybeSingleMock(...a)
    if (table === "profiles") return profileMaybeSingleMock(...a)
    throw new Error(`unexpected table: ${table}`)
  }
}

vi.mock("@supabase/supabase-js", () => ({
  createClient: () => ({
    auth: { getUser: (...a: unknown[]) => getUserMock(...a) },
    // Supports BOTH chain shapes used across this module:
    //   select().eq().maybeSingle()        (hasCompleteSignupProfile)
    //   select().eq().limit().maybeSingle() (notAuthorizedContinuePath's own
    //                                         company_members/workspace_members reads)
    from: (table: string) => ({
      select: () => ({
        eq: () => ({
          maybeSingle: maybeSingleFor(table),
          limit: () => ({ maybeSingle: maybeSingleFor(table) }),
        }),
      }),
    }),
  }),
}))

vi.mock("../../onboarding/store", () => ({
  fetchWorkspaceForUser: (...a: unknown[]) => fetchWorkspaceMock(...a),
}))

import { notAuthorizedContinuePath } from "../client"
import { ONBOARDING_STEP_SLUGS } from "../../onboarding/types"

const FIRST_STEP = `/onboarding/${ONBOARDING_STEP_SLUGS[0]}`

afterEach(() => {
  vi.resetAllMocks()
})

function signedInAs(userId = "user-1") {
  getUserMock.mockResolvedValue({ data: { user: { id: userId } } })
}

describe("notAuthorizedContinuePath", () => {
  it("test_no_company_membership_routes_to_zero_company_onboarding_entry", async () => {
    signedInAs()
    companyMembersMaybeSingleMock.mockResolvedValue({ data: null })
    profileMaybeSingleMock.mockResolvedValue({
      data: { first_name: "Ada", account_type: "personal" },
      error: null,
    })
    expect(await notAuthorizedContinuePath()).toBe(FIRST_STEP)
  })

  it("test_no_company_membership_and_incomplete_profile_routes_to_your_name_gate", async () => {
    signedInAs()
    companyMembersMaybeSingleMock.mockResolvedValue({ data: null })
    profileMaybeSingleMock.mockResolvedValue({ data: null, error: null })
    expect(await notAuthorizedContinuePath()).toBe("/onboarding/your-name")
  })

  it("test_owner_role_always_has_a_workspace_routes_home", async () => {
    signedInAs()
    companyMembersMaybeSingleMock.mockResolvedValue({ data: { role: "owner" } })
    expect(await notAuthorizedContinuePath()).toBe("/")
    // Never needed to check workspace_members for an owner/admin — implicit
    // access per _resolve_workspace's own role-based fast path.
    expect(workspaceMembersMaybeSingleMock).not.toHaveBeenCalled()
  })

  it("test_admin_role_always_has_a_workspace_routes_home", async () => {
    signedInAs()
    companyMembersMaybeSingleMock.mockResolvedValue({ data: { role: "admin" } })
    expect(await notAuthorizedContinuePath()).toBe("/")
  })

  it("test_member_role_with_a_real_workspace_members_row_routes_home", async () => {
    signedInAs()
    companyMembersMaybeSingleMock.mockResolvedValue({ data: { role: "member" } })
    workspaceMembersMaybeSingleMock.mockResolvedValue({ data: { id: "wm-1" } })
    expect(await notAuthorizedContinuePath()).toBe("/")
  })

  it("test_member_role_with_no_workspace_members_row_continues_their_own_companys_onboarding", async () => {
    signedInAs()
    companyMembersMaybeSingleMock.mockResolvedValue({ data: { role: "member" } })
    workspaceMembersMaybeSingleMock.mockResolvedValue({ data: null })
    fetchWorkspaceMock.mockResolvedValue({ onboarding_step: 3 })
    const path = await notAuthorizedContinuePath()
    expect(path).toMatch(/^\/onboarding\//)
    expect(path).not.toBe("/")
  })

  it("test_viewer_role_with_no_workspace_members_row_never_routes_home", async () => {
    signedInAs()
    companyMembersMaybeSingleMock.mockResolvedValue({ data: { role: "viewer" } })
    workspaceMembersMaybeSingleMock.mockResolvedValue({ data: null })
    fetchWorkspaceMock.mockResolvedValue(null)
    expect(await notAuthorizedContinuePath()).toBe(FIRST_STEP)
  })

  it("test_no_signed_in_user_routes_to_sign_in", async () => {
    getUserMock.mockResolvedValue({ data: { user: null } })
    expect(await notAuthorizedContinuePath()).toBe("/sign-in")
  })
})
