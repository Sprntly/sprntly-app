// @vitest-environment jsdom
//
// The held invitee session — the account minted by an invite magic link that
// was opened while somebody else was already signed in.
//
// It used to live in a module variable only. The link's one-time token is
// already spent by then, so a single reload of /invite-conflict destroyed the
// last remaining route into the invited account and left the user with nothing
// but "stay as your current account". The natural exit from that dead end is to
// sign up again, which is how a company ends up with a duplicate workspace
// (2026-08-19: an invited admin created a second org 71 seconds after landing
// there, and the original invite dangled unclaimed).
//
// So the property under test is survival across a reload, plus the fallbacks
// that must not regress: storage being unavailable, and storage containing junk.
import { beforeEach, describe, expect, it, vi } from "vitest"

import {
  clearPendingInviteSession,
  getPendingInviteSession,
  setPendingInviteSession,
} from "../client"

const STORAGE_KEY = "sprntly.pendingInviteSession"

const HELD = {
  email: "invitee@northwind.example",
  accessToken: "acc-invitee",
  refreshToken: "ref-invitee",
}

/** Drop the module's in-memory copy the way a full page reload would, WITHOUT
 *  touching sessionStorage — which is exactly the state this fix exists for. */
async function reloadPage() {
  vi.resetModules()
  return await import("../client")
}

beforeEach(() => {
  window.sessionStorage.clear()
  clearPendingInviteSession()
  vi.resetModules()
})

describe("pending invite session", () => {
  it("round-trips in the same page view", () => {
    setPendingInviteSession(HELD)
    expect(getPendingInviteSession()).toEqual(HELD)
  })

  it("SURVIVES A RELOAD — the regression this fix exists for", async () => {
    setPendingInviteSession(HELD)

    const fresh = await reloadPage()

    // The module variable is gone; the invited account is still reachable.
    expect(fresh.getPendingInviteSession()).toEqual(HELD)
  })

  it("is written to sessionStorage, not localStorage", () => {
    setPendingInviteSession(HELD)
    expect(window.sessionStorage.getItem(STORAGE_KEY)).toBeTruthy()
    // localStorage would outlive the tab, leaving a usable session for the next
    // person on a shared machine. sessionStorage dies with the tab.
    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull()
  })

  it("is cleared from storage on clear, so it never outlives the decision", async () => {
    setPendingInviteSession(HELD)
    clearPendingInviteSession()

    expect(getPendingInviteSession()).toBeNull()
    expect(window.sessionStorage.getItem(STORAGE_KEY)).toBeNull()

    const fresh = await reloadPage()
    expect(fresh.getPendingInviteSession()).toBeNull()
  })

  it("setting null clears storage too", () => {
    setPendingInviteSession(HELD)
    setPendingInviteSession(null)
    expect(window.sessionStorage.getItem(STORAGE_KEY)).toBeNull()
  })

  it("returns null when nothing was ever held", () => {
    expect(getPendingInviteSession()).toBeNull()
  })
})

describe("pending invite session — fallbacks that must not regress", () => {
  it("still works in-page when sessionStorage throws (private mode)", () => {
    const setItem = vi
      .spyOn(Storage.prototype, "setItem")
      .mockImplementation(() => {
        throw new DOMException("QuotaExceededError")
      })
    try {
      // The write fails; the module variable still serves this page view, which
      // is precisely the pre-fix behaviour. Degrading, never throwing.
      expect(() => setPendingInviteSession(HELD)).not.toThrow()
      expect(getPendingInviteSession()).toEqual(HELD)
    } finally {
      setItem.mockRestore()
    }
  })

  it("does not throw when reading throws", () => {
    const getItem = vi
      .spyOn(Storage.prototype, "getItem")
      .mockImplementation(() => {
        throw new DOMException("SecurityError")
      })
    try {
      expect(getPendingInviteSession()).toBeNull()
    } finally {
      getItem.mockRestore()
    }
  })

  it.each([
    ["not json at all", "}{"],
    ["a json primitive", '"just-a-string"'],
    ["an object missing tokens", '{"email":"a@b.example"}'],
    ["an empty access token", '{"email":null,"accessToken":"","refreshToken":"r"}'],
    ["an empty refresh token", '{"email":null,"accessToken":"a","refreshToken":""}'],
    ["a non-string email", '{"email":42,"accessToken":"a","refreshToken":"r"}'],
  ])("treats %s as no held session and evicts it", async (_label, raw) => {
    window.sessionStorage.setItem(STORAGE_KEY, raw)

    const fresh = await reloadPage()

    // Storage is attacker-writable in principle, and handing a malformed
    // object to setSession fails confusingly rather than falling back cleanly.
    expect(fresh.getPendingInviteSession()).toBeNull()
    // Evicted, so the next read is not a repeat of the same failure.
    expect(window.sessionStorage.getItem(STORAGE_KEY)).toBeNull()
  })

  it("accepts a null email, which is a legitimate held shape", async () => {
    setPendingInviteSession({ ...HELD, email: null })
    const fresh = await reloadPage()
    expect(fresh.getPendingInviteSession()).toEqual({ ...HELD, email: null })
  })
})
