// @vitest-environment jsdom
//
// AuthProvider's onAuthStateChange handling.
//
// The bug: the handler discarded the event and re-derived state from the
// session alone, so ANY re-emit carrying a transient null dropped the app to
// `anonymous`. AuthGate renders a full-viewport white "Loading…" for every
// non-authed state and sits above every provider in the (app) layout, so that
// blink unmounted and remounted the entire tree — reported as "the page
// refreshes after a PRD is generated", a multi-second white screen.
//
// These tests hold BOTH halves, because the fix is only correct if it stays
// strict where it matters:
//   • the flash is gone — a transient null while authed changes nothing
//   • sign-out still signs out, immediately (the one branch that must never
//     be softened)
//   • a DIFFERENT user still replaces the identity (never a stale session)
//   • the token is updated on every event regardless of what state does
import * as React from "react"
import { renderHook, act, waitFor } from "@testing-library/react"
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

beforeAll(() => {
  process.env.NEXT_PUBLIC_SUPABASE_URL = "https://example.supabase.co"
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY = "anon-key"
})

type Session = { user: { id: string } | null; access_token: string; expires_at: number }

/** The handler supabase-js was given, so tests can fire events by hand. */
let emit: ((event: string, session: Session | null) => void) | null = null
let initialSession: Session | null = null
const getSessionMock = vi.fn()

vi.mock("@supabase/supabase-js", () => ({
  createClient: () => ({
    auth: {
      getSession: (...a: unknown[]) => getSessionMock(...a),
      onAuthStateChange: (cb: (e: string, s: Session | null) => void) => {
        emit = cb
        return { data: { subscription: { unsubscribe: () => {} } } }
      },
    },
  }),
}))

import { AuthProvider, useAuth } from "../auth"
import { getAccessToken } from "../api"

function wrapper({ children }: { children: React.ReactNode }) {
  return <AuthProvider>{children}</AuthProvider>
}

/** A live session for `id`, expiring far enough out that the provider's
 *  cached-token fast path serves it without calling getSession(). */
function session(id: string, token: string): Session {
  return { user: { id }, access_token: token, expires_at: Math.floor(Date.now() / 1000) + 3600 }
}

const ALICE = session("user-alice", "token-alice")

/** Mount the provider already signed in as Alice — the state every test below
 *  starts from. */
async function renderAuthed() {
  initialSession = ALICE
  getSessionMock.mockResolvedValue({ data: { session: initialSession } })
  const rendered = renderHook(() => useAuth(), { wrapper })
  await waitFor(() => expect(rendered.result.current.kind).toBe("authed"))
  return rendered
}

beforeEach(() => {
  emit = null
  initialSession = null
  getSessionMock.mockReset()
})
afterEach(() => {
  vi.clearAllMocks()
})

describe("a transient null session never blanks the app", () => {
  // The exact reported symptom. TOKEN_REFRESHED is the event most likely to
  // land during a long PRD poll.
  it("stays authed when TOKEN_REFRESHED arrives with no session", async () => {
    const { result } = await renderAuthed()
    const before = result.current

    await act(async () => emit!("TOKEN_REFRESHED", null))

    expect(result.current.kind).toBe("authed")
    // The very same state object — nothing downstream even re-renders, so
    // WorkspaceContext's own guard is never disarmed either.
    expect(result.current).toBe(before)
  })

  it("stays authed through a null INITIAL_SESSION re-emit", async () => {
    const { result } = await renderAuthed()
    await act(async () => emit!("INITIAL_SESSION", null))
    expect(result.current.kind).toBe("authed")
  })

  // A refresh that re-delivers the same user must not churn the identity.
  it("keeps the user across a TOKEN_REFRESHED carrying a fresh token", async () => {
    const { result } = await renderAuthed()
    await act(async () => emit!("TOKEN_REFRESHED", session("user-alice", "token-alice-2")))
    expect(result.current.kind).toBe("authed")
    expect(result.current.kind === "authed" && result.current.user.id).toBe("user-alice")
  })
})

describe("the strict paths the fix must not soften", () => {
  // The one branch that can never be conditional. Signing out — here or in
  // another tab — tears the app down immediately.
  it("SIGNED_OUT goes anonymous immediately, even while authed", async () => {
    const { result } = await renderAuthed()
    await act(async () => emit!("SIGNED_OUT", null))
    expect(result.current.kind).toBe("anonymous")
  })

  // Belt and braces: a SIGNED_OUT that somehow carries a session is still a
  // sign-out. The event is the authority here, not the payload.
  it("SIGNED_OUT wins even if a session is attached to the event", async () => {
    const { result } = await renderAuthed()
    await act(async () => emit!("SIGNED_OUT", ALICE))
    expect(result.current.kind).toBe("anonymous")
  })

  // Never a stale identity: a different user replaces Alice outright.
  it("a DIFFERENT user's session replaces the current identity", async () => {
    const { result } = await renderAuthed()
    await act(async () => emit!("SIGNED_IN", session("user-bob", "token-bob")))
    expect(result.current.kind === "authed" && result.current.user.id).toBe("user-bob")
  })

  // USER_UPDATED carries changed user fields (email_confirmed_at is read by
  // isUserEmailVerified), so a session-bearing event is always taken as-is.
  it("USER_UPDATED lands the new user object", async () => {
    const { result } = await renderAuthed()
    const updated = { ...session("user-alice", "token-alice"), user: { id: "user-alice", email_confirmed_at: "2026-01-01T00:00:00Z" } }
    await act(async () => emit!("USER_UPDATED", updated as unknown as Session))
    expect(
      result.current.kind === "authed" &&
        (result.current.user as unknown as { email_confirmed_at?: string }).email_confirmed_at,
    ).toBe("2026-01-01T00:00:00Z")
  })

  // From a NOT-authed state, no session still means anonymous — the mount
  // path is unchanged by this fix.
  it("a null session while unauthenticated still resolves anonymous", async () => {
    initialSession = null
    getSessionMock.mockResolvedValue({ data: { session: null } })
    const { result } = renderHook(() => useAuth(), { wrapper })
    await waitFor(() => expect(result.current.kind).not.toBe("loading"))
    expect(result.current.kind).toBe("anonymous")

    await act(async () => emit!("INITIAL_SESSION", null))
    expect(result.current.kind).toBe("anonymous")
  })
})

describe("the token is independent of React state", () => {
  // The security-relevant invariant. api.ts reads the module-level cached
  // session through the access-token provider, NOT auth state — so holding
  // state still can never hand a request a stale bearer.
  it("a refreshed token is served even when the state object does not move", async () => {
    const { result } = await renderAuthed()
    const before = result.current
    expect(await getAccessToken()).toBe("token-alice")

    await act(async () => emit!("TOKEN_REFRESHED", session("user-alice", "token-alice-rotated")))

    expect(await getAccessToken()).toBe("token-alice-rotated")
    expect(result.current.kind).toBe("authed")
    expect(before.kind).toBe("authed")
  })

  // The converse: after a genuine sign-out there is no token to hand out, so a
  // request made from a stale component cannot carry the old bearer.
  it("stops serving a token once signed out", async () => {
    await renderAuthed()
    expect(await getAccessToken()).toBe("token-alice")

    getSessionMock.mockResolvedValue({ data: { session: null } })
    await act(async () => emit!("SIGNED_OUT", null))

    expect(await getAccessToken()).toBeNull()
  })
})
