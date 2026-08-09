// @vitest-environment node
//
// Unit tests for readPersistedSession — the synchronous pre-init snapshot of
// whoever is already signed in, used by /auth/callback to keep an invite magic
// link from silently hijacking an existing session. Exercises the supabase-js
// localStorage shapes (direct Session + legacy { currentSession }), the
// key-matching (skip the PKCE code-verifier sibling), and the empty/garbage
// fallbacks.
import { describe, expect, it } from "vitest"
import { readPersistedSession } from "../client"

/** Minimal in-memory Storage good enough for readPersistedSession. */
function fakeStorage(entries: Record<string, string>): Storage {
  const keys = Object.keys(entries)
  return {
    get length() {
      return keys.length
    },
    key(i: number) {
      return keys[i] ?? null
    },
    getItem(k: string) {
      return k in entries ? entries[k] : null
    },
    setItem() {},
    removeItem() {},
    clear() {},
  } as Storage
}

const SESSION = {
  access_token: "acc-A",
  refresh_token: "ref-A",
  user: { id: "user-A", email: "a@example.com" },
}

describe("readPersistedSession", () => {
  it("reads the direct supabase-js v2 Session shape", () => {
    const store = fakeStorage({ "sb-abcd-auth-token": JSON.stringify(SESSION) })
    expect(readPersistedSession(store)).toEqual({
      userId: "user-A",
      email: "a@example.com",
      accessToken: "acc-A",
      refreshToken: "ref-A",
    })
  })

  it("reads the legacy { currentSession } wrapper shape", () => {
    const store = fakeStorage({
      "sb-abcd-auth-token": JSON.stringify({ currentSession: SESSION }),
    })
    expect(readPersistedSession(store)?.userId).toBe("user-A")
  })

  it("ignores the PKCE code-verifier sibling key", () => {
    const store = fakeStorage({
      "sb-abcd-auth-token-code-verifier": JSON.stringify("verifier-only"),
    })
    expect(readPersistedSession(store)).toBeNull()
  })

  it("returns null when no auth-token key is present", () => {
    const store = fakeStorage({ "some-other-key": "x" })
    expect(readPersistedSession(store)).toBeNull()
  })

  it("returns null for malformed JSON", () => {
    const store = fakeStorage({ "sb-abcd-auth-token": "{not json" })
    expect(readPersistedSession(store)).toBeNull()
  })

  it("returns null when tokens are present but the user id is missing", () => {
    const store = fakeStorage({
      "sb-abcd-auth-token": JSON.stringify({
        access_token: "acc",
        refresh_token: "ref",
        user: {},
      }),
    })
    expect(readPersistedSession(store)).toBeNull()
  })

  it("defaults email to null when absent", () => {
    const store = fakeStorage({
      "sb-abcd-auth-token": JSON.stringify({
        access_token: "acc",
        refresh_token: "ref",
        user: { id: "user-A" },
      }),
    })
    expect(readPersistedSession(store)?.email).toBeNull()
  })
})
