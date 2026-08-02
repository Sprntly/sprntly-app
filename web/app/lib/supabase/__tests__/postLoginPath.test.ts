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
const resolveArtifactShareMock = vi.fn()
const tryAutoJoinCompanyMock = vi.fn()

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

// postLoginPath lazily imports artifactShareApi's resolveArtifactShare +
// tryAutoJoinCompanyOnDomainMatch, same pattern as tryAcceptInvite's lazy
// teamApi import above.
vi.mock("../../artifactShareApi", () => ({
  resolveArtifactShare: (...a: unknown[]) => resolveArtifactShareMock(...a),
  tryAutoJoinCompanyOnDomainMatch: (...a: unknown[]) => tryAutoJoinCompanyMock(...a),
}))

import { postLoginPath } from "../client"
import { ONBOARDING_STEP_SLUGS } from "../../onboarding/types"
import { ApiError } from "../../api"

const FIRST_STEP = `/onboarding/${ONBOARDING_STEP_SLUGS[0]}`

afterEach(() => {
  vi.resetAllMocks()
})

function newConfirmedUser(userMetadata?: Record<string, unknown>) {
  getUserMock.mockResolvedValue({
    data: {
      user: {
        id: "user-1",
        email_confirmed_at: "2026-01-01T00:00:00Z",
        user_metadata: userMetadata,
      },
    },
  })
  fetchWorkspaceMock.mockResolvedValue(null) // no workspace
  acceptInviteMock.mockRejectedValue(new Error("no invite")) // no auto-accept
}

/** A signed-in user who already belongs to a company (workspace resolves). */
function existingMemberUser(
  workspace: Record<string, unknown> = {},
  userMetadata?: Record<string, unknown>,
) {
  getUserMock.mockResolvedValue({
    data: {
      user: {
        id: "user-1",
        email_confirmed_at: "2026-01-01T00:00:00Z",
        user_metadata: userMetadata,
      },
    },
  })
  fetchWorkspaceMock.mockResolvedValue({
    id: "ws-1",
    onboarding_completed_at: "2026-01-02T00:00:00Z",
    onboarding_step: 0,
    ...workspace,
  })
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

describe("postLoginPath — pending-invite resolution for existing members", () => {
  it("routes to /invite-conflict when the pending invite is from ANOTHER company (409)", async () => {
    existingMemberUser()
    acceptInviteMock.mockRejectedValue(
      new ApiError(409, { detail: "already in another company" }),
    )
    expect(await postLoginPath()).toBe("/invite-conflict")
  })

  it("continues into the app when there is no pending invite (404)", async () => {
    existingMemberUser()
    acceptInviteMock.mockRejectedValue(new ApiError(404, { detail: "no invite" }))
    expect(await postLoginPath()).toBe("/")
  })

  it("continues into the app after a same-company accept succeeds (extra workspaces granted)", async () => {
    existingMemberUser()
    acceptInviteMock.mockResolvedValue({ company_id: "co-1", role: "member" })
    expect(await postLoginPath()).toBe("/")
  })

  it("treats a network failure as best-effort and continues into the app", async () => {
    existingMemberUser()
    acceptInviteMock.mockRejectedValue(new Error("network down"))
    expect(await postLoginPath()).toBe("/")
  })

  it("still surfaces the conflict for a member whose onboarding is unfinished", async () => {
    existingMemberUser({ onboarding_completed_at: null, onboarding_step: 2 })
    acceptInviteMock.mockRejectedValue(
      new ApiError(409, { detail: "already in another company" }),
    )
    expect(await postLoginPath()).toBe("/invite-conflict")
  })
})

describe("postLoginPath — guest account state (pending share token)", () => {
  it("routes a new user with an EMPTY first_name to the your-name gate (unchanged when no pending_share_token)", async () => {
    newConfirmedUser()
    profileMaybeSingleMock.mockResolvedValue({
      data: { first_name: "", account_type: "company" },
      error: null,
    })
    expect(await postLoginPath()).toBe("/onboarding/your-name")
    expect(resolveArtifactShareMock).not.toHaveBeenCalled()
  })

  it("routes to the artifact on a guest_view resolve outcome, auto-joining company FIRST", async () => {
    newConfirmedUser({ pending_share_token: "abc123" })
    resolveArtifactShareMock.mockResolvedValue({
      outcome: "guest_view",
      artifact_type: "prd",
      artifact_id: 42,
      owning_company_name: "Acme",
      sharer_name: "Ada",
    })
    expect(await postLoginPath()).toBe("/?prd=42&share=abc123")
    expect(resolveArtifactShareMock).toHaveBeenCalledWith("abc123")
    // Mutation-proof: the one-shot auto-join call actually fires, and fires
    // BEFORE /resolve (so a fresh domain-matched signup's brand-new company
    // membership is already in place by the time resolve_share_access runs
    // server-side).
    expect(tryAutoJoinCompanyMock).toHaveBeenCalledWith("abc123")
    const autoJoinOrder = tryAutoJoinCompanyMock.mock.invocationCallOrder[0]
    const resolveOrder = resolveArtifactShareMock.mock.invocationCallOrder[0]
    expect(autoJoinOrder).toBeLessThan(resolveOrder)
  })

  it("routes to /not-authorized (with the reason) on a blocked resolve outcome", async () => {
    newConfirmedUser({ pending_share_token: "abc123" })
    resolveArtifactShareMock.mockResolvedValue({
      outcome: "blocked",
      reason: "different_company",
    })
    expect(await postLoginPath()).toBe("/not-authorized?share=abc123&reason=different_company")
  })

  it("falls open to onboarding when resolve fails (network/other error)", async () => {
    newConfirmedUser({ pending_share_token: "abc123" })
    resolveArtifactShareMock.mockResolvedValue(undefined)
    profileMaybeSingleMock.mockResolvedValue({
      data: { first_name: "Ada", account_type: "personal" },
      error: null,
    })
    expect(await postLoginPath()).toBe(FIRST_STEP)
  })

  it("skips the share check entirely when the user already has a workspace", async () => {
    existingMemberUser({}, { pending_share_token: "abc123" })
    acceptInviteMock.mockRejectedValue(new ApiError(404, { detail: "no invite" }))
    expect(await postLoginPath()).toBe("/")
    expect(resolveArtifactShareMock).not.toHaveBeenCalled()
  })

  it("ignores a non-string pending_share_token, falling through to onboarding", async () => {
    newConfirmedUser({ pending_share_token: 12345 })
    profileMaybeSingleMock.mockResolvedValue({
      data: { first_name: "Ada", account_type: "personal" },
      error: null,
    })
    expect(await postLoginPath()).toBe(FIRST_STEP)
    expect(resolveArtifactShareMock).not.toHaveBeenCalled()
  })

  it("ignores an empty-string pending_share_token, falling through to onboarding", async () => {
    newConfirmedUser({ pending_share_token: "" })
    profileMaybeSingleMock.mockResolvedValue({
      data: { first_name: "Ada", account_type: "personal" },
      error: null,
    })
    expect(await postLoginPath()).toBe(FIRST_STEP)
    expect(resolveArtifactShareMock).not.toHaveBeenCalled()
  })

  it("still checks the pending share after tryAcceptInvite resolves to no-op", async () => {
    newConfirmedUser({ pending_share_token: "abc123" })
    acceptInviteMock.mockRejectedValue(new ApiError(404, { detail: "no invite" }))
    resolveArtifactShareMock.mockResolvedValue({
      outcome: "guest_view",
      artifact_type: "prd",
      artifact_id: 7,
      owning_company_name: "Acme",
      sharer_name: "Ada",
    })
    expect(await postLoginPath()).toBe("/?prd=7&share=abc123")
  })

  it("never calls the auto-join mechanism when there's no pending token to begin with", async () => {
    newConfirmedUser()
    profileMaybeSingleMock.mockResolvedValue({
      data: { first_name: "Ada", account_type: "personal" },
      error: null,
    })
    expect(await postLoginPath()).toBe(FIRST_STEP)
    expect(tryAutoJoinCompanyMock).not.toHaveBeenCalled()
  })
})
