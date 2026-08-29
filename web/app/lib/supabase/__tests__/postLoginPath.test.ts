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
const resolvePrdAccessMock = vi.fn()
const tryAutoJoinCompanyForPrdMock = vi.fn()

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

// Bare-link ("full parity") sibling — lazily imported by postLoginPath's
// pending_prd_public_id branch, same pattern as artifactShareApi above.
vi.mock("../../prdAccessApi", () => ({
  resolvePrdAccess: (...a: unknown[]) => resolvePrdAccessMock(...a),
  tryAutoJoinCompanyOnDomainMatchForPrd: (...a: unknown[]) => tryAutoJoinCompanyForPrdMock(...a),
}))

import { postLoginPath } from "../client"
import { ONBOARDING_STEP_SLUGS, slugForStep } from "../../onboarding/types"
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

describe("postLoginPath — project invite lands on the project's private chat (AD-TNM3)", () => {
  it("existing member accepting a project-carrying invite → /projects?id=…&chat=individual", async () => {
    existingMemberUser()
    acceptInviteMock.mockResolvedValue({
      company_id: "co-1",
      role: "member",
      project_id: 42,
    })
    expect(await postLoginPath()).toBe("/projects?id=42&chat=individual")
  })

  it("existing member accepting a PLAIN org invite (no project_id) → unchanged '/' landing", async () => {
    existingMemberUser()
    acceptInviteMock.mockResolvedValue({
      company_id: "co-1",
      role: "member",
      project_id: null,
    })
    expect(await postLoginPath()).toBe("/")
  })

  it("existing member: a non-numeric project_id is ignored → unchanged '/' landing", async () => {
    existingMemberUser()
    acceptInviteMock.mockResolvedValue({
      company_id: "co-1",
      role: "member",
      project_id: "42" as unknown as number,
    })
    expect(await postLoginPath()).toBe("/")
  })

  it("invite-conflict is still guarded even when acceptance would carry a project (409 → /invite-conflict)", async () => {
    existingMemberUser()
    acceptInviteMock.mockRejectedValue(
      new ApiError(409, { detail: "already in another company" }),
    )
    expect(await postLoginPath()).toBe("/invite-conflict")
  })

  it("brand-new invitee (no workspace yet) accepting a project invite → project's private chat", async () => {
    // No workspace on the FIRST fetch; the accept materialises membership, so
    // the SECOND fetch resolves to the (already-onboarded) inviter's company.
    getUserMock.mockResolvedValue({
      data: {
        user: { id: "user-1", email_confirmed_at: "2026-01-01T00:00:00Z", user_metadata: {} },
      },
    })
    fetchWorkspaceMock
      .mockResolvedValueOnce(null)
      .mockResolvedValueOnce({
        id: "ws-1",
        onboarding_completed_at: "2026-01-02T00:00:00Z",
        onboarding_step: 0,
      })
    acceptInviteMock.mockResolvedValue({
      company_id: "co-1",
      role: "member",
      project_id: 7,
    })
    expect(await postLoginPath()).toBe("/projects?id=7&chat=individual")
  })

  it("brand-new invitee whose fresh company onboarding is UNFINISHED still goes to onboarding (project redirect deferred)", async () => {
    getUserMock.mockResolvedValue({
      data: {
        user: { id: "user-1", email_confirmed_at: "2026-01-01T00:00:00Z", user_metadata: {} },
      },
    })
    fetchWorkspaceMock
      .mockResolvedValueOnce(null)
      .mockResolvedValueOnce({
        id: "ws-1",
        onboarding_completed_at: null,
        onboarding_step: 2,
      })
    acceptInviteMock.mockResolvedValue({
      company_id: "co-1",
      role: "member",
      project_id: 7,
    })
    expect(await postLoginPath()).toBe(`/onboarding/${slugForStep(2)}`)
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

  it("routes to the artifact via public_id (never the raw artifact_id), auto-joining company FIRST", async () => {
    newConfirmedUser({ pending_share_token: "abc123" })
    resolveArtifactShareMock.mockResolvedValue({
      outcome: "guest_view",
      artifact_type: "prd",
      artifact_id: 42,
      public_id: "042494cd-22c0-4c20-9967-cc761d192ae0",
      owning_company_name: "Acme",
      sharer_name: "Ada",
    })
    expect(await postLoginPath()).toBe(
      "/?prd=042494cd-22c0-4c20-9967-cc761d192ae0&share=abc123",
    )
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

  it("falls back to the raw artifact_id only when public_id is absent (defensive, not the normal path)", async () => {
    newConfirmedUser({ pending_share_token: "abc123" })
    resolveArtifactShareMock.mockResolvedValue({
      outcome: "guest_view",
      artifact_type: "prd",
      artifact_id: 42,
      public_id: null,
      owning_company_name: "Acme",
      sharer_name: "Ada",
    })
    expect(await postLoginPath()).toBe("/?prd=42&share=abc123")
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

describe("postLoginPath — bare-link guest account state (pending_prd_public_id, no token)", () => {
  it("routes a new user with an EMPTY first_name to the your-name gate (unchanged when no pending_prd_public_id)", async () => {
    newConfirmedUser()
    profileMaybeSingleMock.mockResolvedValue({
      data: { first_name: "", account_type: "company" },
      error: null,
    })
    expect(await postLoginPath()).toBe("/onboarding/your-name")
    expect(resolvePrdAccessMock).not.toHaveBeenCalled()
  })

  it("routes to the PRD via public_id (never the real int) on a guest_view outcome, auto-joining company FIRST", async () => {
    newConfirmedUser({ pending_prd_public_id: "042494cd-22c0-4c20-9967-cc761d192ae0" })
    resolvePrdAccessMock.mockResolvedValue({
      outcome: "guest_view",
      artifact_type: "prd",
      artifact_id: 42, // the real internal id — must NEVER appear in the redirect
      owning_company_name: "Acme",
    })
    expect(await postLoginPath()).toBe(
      "/?prd=042494cd-22c0-4c20-9967-cc761d192ae0&access=guest",
    )
    expect(resolvePrdAccessMock).toHaveBeenCalledWith(
      "042494cd-22c0-4c20-9967-cc761d192ae0",
    )
    // Mutation-proof: the one-shot auto-join call actually fires, and fires
    // BEFORE /resolve — same ordering guarantee as the token-keyed sibling.
    expect(tryAutoJoinCompanyForPrdMock).toHaveBeenCalledWith(
      "042494cd-22c0-4c20-9967-cc761d192ae0",
    )
    const autoJoinOrder = tryAutoJoinCompanyForPrdMock.mock.invocationCallOrder[0]
    const resolveOrder = resolvePrdAccessMock.mock.invocationCallOrder[0]
    expect(autoJoinOrder).toBeLessThan(resolveOrder)
  })

  it("routes to /not-authorized (with the reason, no prd/public_id disclosed) on a blocked resolve outcome", async () => {
    newConfirmedUser({ pending_prd_public_id: "042494cd-22c0-4c20-9967-cc761d192ae0" })
    resolvePrdAccessMock.mockResolvedValue({
      outcome: "blocked",
      reason: "different_company",
    })
    expect(await postLoginPath()).toBe("/not-authorized?reason=different_company")
  })

  it("falls open to onboarding when resolve fails (network/other error)", async () => {
    newConfirmedUser({ pending_prd_public_id: "042494cd-22c0-4c20-9967-cc761d192ae0" })
    resolvePrdAccessMock.mockResolvedValue(undefined)
    profileMaybeSingleMock.mockResolvedValue({
      data: { first_name: "Ada", account_type: "personal" },
      error: null,
    })
    expect(await postLoginPath()).toBe(FIRST_STEP)
  })

  it("skips the bare-link check entirely when the user already has a workspace", async () => {
    existingMemberUser({}, { pending_prd_public_id: "042494cd-22c0-4c20-9967-cc761d192ae0" })
    acceptInviteMock.mockRejectedValue(new ApiError(404, { detail: "no invite" }))
    expect(await postLoginPath()).toBe("/")
    expect(resolvePrdAccessMock).not.toHaveBeenCalled()
  })

  it("ignores a non-string pending_prd_public_id, falling through to onboarding", async () => {
    newConfirmedUser({ pending_prd_public_id: 12345 })
    profileMaybeSingleMock.mockResolvedValue({
      data: { first_name: "Ada", account_type: "personal" },
      error: null,
    })
    expect(await postLoginPath()).toBe(FIRST_STEP)
    expect(resolvePrdAccessMock).not.toHaveBeenCalled()
  })

  it("ignores an empty-string pending_prd_public_id, falling through to onboarding", async () => {
    newConfirmedUser({ pending_prd_public_id: "" })
    profileMaybeSingleMock.mockResolvedValue({
      data: { first_name: "Ada", account_type: "personal" },
      error: null,
    })
    expect(await postLoginPath()).toBe(FIRST_STEP)
    expect(resolvePrdAccessMock).not.toHaveBeenCalled()
  })

  it("never calls the bare-link auto-join mechanism when there's no pending public_id to begin with", async () => {
    newConfirmedUser()
    profileMaybeSingleMock.mockResolvedValue({
      data: { first_name: "Ada", account_type: "personal" },
      error: null,
    })
    expect(await postLoginPath()).toBe(FIRST_STEP)
    expect(tryAutoJoinCompanyForPrdMock).not.toHaveBeenCalled()
  })
})

describe("postLoginPath — the onboarding payment gate", () => {
  // Payment sits between creating a company and the rest of onboarding. This
  // is the ROUTING half of that gate: `enforce.bill` on the backend is what
  // actually refuses work, and nothing here can grant access the server will
  // not honour.
  //
  // The gate is COMPANY-level, not user-level, which is what stops an invited
  // teammate being charged for a company that already pays.

  it("sends an unpaid company's unfinished onboarding to the plan gate", async () => {
    existingMemberUser({
      onboarding_completed_at: null,
      onboarding_step: 2,
      plan: "starter",
      subscription_status: null,
    })
    acceptInviteMock.mockRejectedValue(new Error("no invite"))
    expect(await postLoginPath()).toBe("/onboarding/plan")
  })

  it("keeps sending them back there until they finish — the step is NOT rewound", async () => {
    // Someone who abandons at Stripe still has a company row, a verified
    // email and a persisted step naming where they carry on. That is the
    // whole reason payment sits this early: an abandoned signup is a lead.
    existingMemberUser({
      onboarding_completed_at: null,
      onboarding_step: 6,
      subscription_status: "canceled",
    })
    acceptInviteMock.mockRejectedValue(new Error("no invite"))
    expect(await postLoginPath()).toBe("/onboarding/plan")

    // …and once paid, they resume on the step they left, not at the start.
    vi.resetAllMocks()
    existingMemberUser({
      onboarding_completed_at: null,
      onboarding_step: 6,
      subscription_status: "active",
    })
    acceptInviteMock.mockRejectedValue(new Error("no invite"))
    expect(await postLoginPath()).toBe(`/onboarding/${slugForStep(6)}`)
  })

  it("lets a trialling company straight through — the card is on file", async () => {
    existingMemberUser({
      onboarding_completed_at: null,
      onboarding_step: 3,
      subscription_status: "trialing",
    })
    acceptInviteMock.mockRejectedValue(new Error("no invite"))
    expect(await postLoginPath()).toBe(`/onboarding/${slugForStep(3)}`)
  })

  it("does not gate a company whose plan was never sold through Stripe", async () => {
    // LEGACY and ENTERPRISE carry a null subscription_status by design.
    // Gating them on one would lock every pre-billing tenant out of their own
    // unfinished onboarding the day this ships.
    for (const plan of ["legacy", "enterprise"]) {
      vi.resetAllMocks()
      existingMemberUser({
        onboarding_completed_at: null,
        onboarding_step: 4,
        plan,
        subscription_status: null,
      })
      acceptInviteMock.mockRejectedValue(new Error("no invite"))
      expect(await postLoginPath(), plan).toBe(`/onboarding/${slugForStep(4)}`)
    }
  })

  it("never gates a company that has FINISHED onboarding", async () => {
    // The gate is an onboarding step, not a paywall on the whole app. Someone
    // already inside whose subscription lapses is the enforcement layer's
    // problem (402 on generation), not a reason to throw them back into
    // signup.
    existingMemberUser({
      onboarding_completed_at: "2026-01-02T00:00:00Z",
      subscription_status: null,
    })
    acceptInviteMock.mockRejectedValue(new Error("no invite"))
    expect(await postLoginPath()).toBe("/")
  })

  it("still surfaces an invite conflict ahead of the gate", async () => {
    // A user who cannot join this company at all should be told that, not
    // asked to pay for it.
    existingMemberUser({
      onboarding_completed_at: null,
      onboarding_step: 2,
      subscription_status: null,
    })
    acceptInviteMock.mockRejectedValue(
      new ApiError(409, { detail: "already in another company" }),
    )
    expect(await postLoginPath()).toBe("/invite-conflict")
  })
})
