// @vitest-environment jsdom
//
// signUpWithPassword's options.data threading for the new pendingShareToken
// field on SignUpInput — reuses the existing role/priorities/timezone
// metadata pattern verbatim (lib/auth.tsx). Renders the real AuthProvider
// with a stubbed supabase-js client so signUp's actual call args are
// asserted end-to-end, same mocking shape postLoginPath.test.ts uses for the
// supabase-js module.
import * as React from "react"
import { renderHook, waitFor } from "@testing-library/react"
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const signUpMock = vi.fn()

beforeAll(() => {
  process.env.NEXT_PUBLIC_SUPABASE_URL = "https://example.supabase.co"
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY = "anon-key"
})

vi.mock("@supabase/supabase-js", () => ({
  createClient: () => ({
    auth: {
      getSession: () => Promise.resolve({ data: { session: null } }),
      onAuthStateChange: () => ({ data: { subscription: { unsubscribe: () => {} } } }),
      signUp: (...a: unknown[]) => signUpMock(...a),
    },
  }),
}))

import { AuthProvider, useAuth } from "../auth"

function wrapper({ children }: { children: React.ReactNode }) {
  return <AuthProvider>{children}</AuthProvider>
}

describe("signUpWithPassword — pendingShareToken metadata threading", () => {
  beforeEach(() => {
    signUpMock.mockReset()
    signUpMock.mockResolvedValue({
      data: { user: { identities: [{}] }, session: null },
      error: null,
    })
  })
  afterEach(() => {
    vi.clearAllMocks()
  })

  it("includes pending_share_token in options.data when pendingShareToken is provided", async () => {
    const { result } = renderHook(() => useAuth(), { wrapper })
    await waitFor(() => expect(result.current.kind).not.toBe("loading"))

    await result.current.signUpWithPassword({
      email: "guest@example.com",
      password: "hunter2!!",
      firstName: "Guest",
      lastName: "User",
      pendingShareToken: "abc123",
    })

    expect(signUpMock).toHaveBeenCalledTimes(1)
    const call = signUpMock.mock.calls[0][0]
    expect(call.options.data.pending_share_token).toBe("abc123")
  })

  it("omits pending_share_token from options.data entirely when not provided", async () => {
    const { result } = renderHook(() => useAuth(), { wrapper })
    await waitFor(() => expect(result.current.kind).not.toBe("loading"))

    await result.current.signUpWithPassword({
      email: "person@example.com",
      password: "hunter2!!",
      firstName: "Regular",
      lastName: "User",
    })

    expect(signUpMock).toHaveBeenCalledTimes(1)
    const call = signUpMock.mock.calls[0][0]
    expect("pending_share_token" in call.options.data).toBe(false)
  })
})
