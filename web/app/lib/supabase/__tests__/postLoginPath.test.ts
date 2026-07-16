// @vitest-environment node
//
// Unit tests for the pre-onboarding profile gate inside postLoginPath: a NEW
// user (no workspace, no pending invite) whose profile is missing a first
// name OR the company-vs-personal account_type is routed to the unnumbered
// /onboarding/your-name gate; one whose profile has BOTH (e.g. email/password
// sign-up) skips straight to the first numbered step. A missing profile row
// is treated as missing both → gate.
//
// We mock the supabase client (auth.getUser + the minimal profiles select),
// the workspace fetch (no workspace), and the lazily-imported TeamSettings so
// auto-accept-invite is a no-op.
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest"

const getUserMock = vi.fn()
const profileMaybeSingleMock = vi.fn()
const fetchWorkspaceMock = vi.fn()
const acceptInviteMock = vi.fn()

// postLoginPath calls the module-local getSupabase(), so we can't intercept it
// by mocking the re-export. Instead satisfy getSupabasePublicConfig() with env
// and mock createClient so the "real" client is our stub.
beforeAll(() => {
  process.env.NEXT_PUBLIC_SUPABASE_URL = "https://example.supabase.co"
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY = "anon-key"
})

vi.mock("@supabase/supabase-js", () => ({
  createClient: () => ({
    auth: { getUser: (...a: unknown[]) => getUserMock(...a) },
    from: () => ({
      select: () => ({
        eq: () => ({
          maybeSingle: (...a: unknown[]) => profileMaybeSingleMock(...a),
        }),
      }),
    }),
  }),
}))

vi.mock("../../onboarding/store", () => ({
  fetchWorkspaceForUser: (...a: unknown[]) => fetchWorkspaceMock(...a),
}))

// lib/teamApi is imported lazily by tryAutoAcceptInvite; make acceptInvite
// reject so the auto-accept path is a clean no-op (falls through to the gate).
vi.mock("../../teamApi", () => ({
  teamApi: { acceptInvite: (...a: unknown[]) => acceptInviteMock(...a) },
}))

import { postLoginPath } from "../client"
import { ONBOARDING_STEP_SLUGS } from "../../onboarding/types"

const FIRST_STEP = `/onboarding/${ONBOARDING_STEP_SLUGS[0]}`

afterEach(() => {
  vi.resetAllMocks()
})

function newConfirmedUser() {
  getUserMock.mockResolvedValue({
    data: { user: { id: "user-1", email_confirmed_at: "2026-01-01T00:00:00Z" } },
  })
  fetchWorkspaceMock.mockResolvedValue(null) // no workspace
  acceptInviteMock.mockRejectedValue(new Error("no invite")) // no auto-accept
}

describe("postLoginPath — pre-onboarding profile gate", () => {
  it("routes a new user with an EMPTY first_name to the your-name gate", async () => {
    newConfirmedUser()
    profileMaybeSingleMock.mockResolvedValue({
      data: { first_name: "", account_type: "company" },
      error: null,
    })
    expect(await postLoginPath()).toBe("/onboarding/your-name")
  })

  it("routes a user MISSING an account_type to the your-name gate (Google SSO)", async () => {
    newConfirmedUser()
    profileMaybeSingleMock.mockResolvedValue({
      data: { first_name: "Ada", account_type: null },
      error: null,
    })
    expect(await postLoginPath()).toBe("/onboarding/your-name")
  })

  it("treats a MISSING profile row as incomplete → your-name gate", async () => {
    newConfirmedUser()
    profileMaybeSingleMock.mockResolvedValue({ data: null, error: null })
    expect(await postLoginPath()).toBe("/onboarding/your-name")
  })

  it("skips the gate to the first step when name AND account type are present", async () => {
    newConfirmedUser()
    profileMaybeSingleMock.mockResolvedValue({
      data: { first_name: "Ada", account_type: "personal" },
      error: null,
    })
    expect(await postLoginPath()).toBe(FIRST_STEP)
  })
})
