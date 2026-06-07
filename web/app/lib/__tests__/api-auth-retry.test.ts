// @vitest-environment jsdom
//
// Transient-401 resilience. Two layers are covered here:
//
//   1. withAuthRetry — the shared primitive. A 401 is treated as a token-refresh
//      race: re-acquire the token and retry once; non-401 errors pass straight
//      through; a 401 that survives the retry re-throws.
//
//   2. The Generate modal's connector rows — the primitive's first consumer that
//      must HOLD last-known UI state. These render the real modal and assert a
//      transient 401 never flips a connected source to "Not connected" and never
//      reflows the modal (so the Generate button stays put). They live in this
//      file because the connector behaviour is a direct application of the auth
//      retry primitive and the modal has no standalone test module of its own.
//
// jsdom is opted into per-file (the global vitest config stays node-env), mirroring
// the existing ShareMenu / ApproveModal DOM tests. The api module is partially
// mocked: the connector calls are stubbed so they resolve without the network,
// while withAuthRetry / ApiError / setAccessTokenProvider stay REAL so the
// primitive under test actually runs.
import * as React from "react"
import { act, cleanup, render } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

// Sprntly components carry no `import React`; vitest's esbuild transform uses the
// classic runtime, so expose React globally (repo test convention).
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

// NavigationProvider depends on next/navigation. Stub the router/pathname so the
// provider mounts without a Next router context.
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => "/prd",
}))

// Partial mock: keep withAuthRetry / ApiError / setAccessTokenProvider REAL, stub
// only the connector network calls the modal kicks off on open.
vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>()
  return {
    ...actual,
    connectorsApi: {
      ...actual.connectorsApi,
      list: vi.fn(),
      listGithubRepos: vi.fn().mockResolvedValue({ repositories: [] }),
    },
    designAgentApi: { ...actual.designAgentApi, generate: vi.fn() },
  }
})

import {
  ApiError,
  connectorsApi,
  setAccessTokenProvider,
  withAuthRetry,
  type ConnectionSummary,
} from "../api"
import { NavigationProvider } from "../../context/NavigationContext"
import { GenerateModal } from "../../components/design-agent/GenerateModal"

const listMock = vi.mocked(connectorsApi.list)

beforeEach(() => {
  // Default to a no-op token provider so withAuthRetry's re-acquire is safe.
  setAccessTokenProvider(() => Promise.resolve(null))
})

afterEach(() => {
  cleanup()
  // mockReset (not just clearAllMocks) so any unconsumed `...Once` queue on the
  // connector mock is drained — otherwise a leftover once-impl leaks into the
  // next test and masks failures.
  listMock.mockReset()
  vi.clearAllMocks()
  setAccessTokenProvider(() => Promise.resolve(null))
})

// ---- withAuthRetry primitive ------------------------------------------------

describe("withAuthRetry", () => {
  it("test_with_auth_retry_retries_once_on_401: retries after re-acquiring the token, succeeds on the retry", async () => {
    const reacquire = vi.fn().mockResolvedValue("fresh-token")
    setAccessTokenProvider(reacquire)

    const fn = vi
      .fn<() => Promise<string>>()
      .mockRejectedValueOnce(new ApiError(401, { detail: "expired" }))
      .mockResolvedValueOnce("ok")

    const result = await withAuthRetry(fn, { backoffMs: 0 })

    expect(result).toBe("ok")
    expect(fn).toHaveBeenCalledTimes(2)
    // The token was re-acquired before the retry.
    expect(reacquire).toHaveBeenCalledTimes(1)
  })

  it("test_with_auth_retry_passes_through_non_401: re-throws a non-401 immediately, no retry", async () => {
    const fn = vi
      .fn<() => Promise<string>>()
      .mockRejectedValue(new ApiError(500, { detail: "boom" }))

    await expect(withAuthRetry(fn, { backoffMs: 0 })).rejects.toBeInstanceOf(
      ApiError,
    )
    expect(fn).toHaveBeenCalledTimes(1)
  })

  it("test_with_auth_retry_passes_through_non_api_error: a plain Error never retries", async () => {
    const fn = vi
      .fn<() => Promise<string>>()
      .mockRejectedValue(new Error("network down"))

    await expect(withAuthRetry(fn, { backoffMs: 0 })).rejects.toThrow(
      "network down",
    )
    expect(fn).toHaveBeenCalledTimes(1)
  })

  it("test_with_auth_retry_gives_up_after_budget: a persistent 401 re-throws after the single retry", async () => {
    const fn = vi
      .fn<() => Promise<string>>()
      .mockRejectedValue(new ApiError(401, { detail: "still expired" }))

    await expect(withAuthRetry(fn, { backoffMs: 0 })).rejects.toMatchObject({
      status: 401,
    })
    // Original attempt + exactly one retry — never an unbounded loop.
    expect(fn).toHaveBeenCalledTimes(2)
  })

  it("returns the value directly when the wrapped call succeeds first time", async () => {
    const fn = vi.fn<() => Promise<number>>().mockResolvedValue(42)
    await expect(withAuthRetry(fn, { backoffMs: 0 })).resolves.toBe(42)
    expect(fn).toHaveBeenCalledTimes(1)
  })
})

// ---- GenerateModal connector rows -------------------------------------------

function activeFigmaConnection(): ConnectionSummary {
  return {
    id: "conn-figma",
    provider: "figma",
    status: "active",
    google_email: null,
    account_label: "design@acme.test",
    scopes: "",
    config: {},
    last_sync_at: "2026-06-01T10:00:00Z",
    last_sync_error: null,
    created_at: "2026-05-30T00:00:00Z",
    updated_at: "2026-06-01T10:00:00Z",
  }
}

function renderOpenModal() {
  return render(
    React.createElement(
      NavigationProvider,
      null,
      React.createElement(GenerateModal, {
        open: true,
        onClose: vi.fn(),
        prdId: 1,
        figmaFileKey: null,
      }),
    ),
  )
}

// Let the on-open fetch — including withAuthRetry's ~250ms backoff + retry —
// settle inside act() so the state updates land.
async function settle() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 350))
  })
}

function figmaRow(): HTMLElement {
  const rows = document.querySelectorAll(".src-row-compact")
  return rows[0] as HTMLElement
}

describe("GenerateModal connector rows — transient-401 resilience", () => {
  it("test_connector_rows_hold_state_on_transient_401: a transient 401 recovers to Connected, never shows Not connected", async () => {
    listMock
      .mockRejectedValueOnce(new ApiError(401, { detail: "expired" }))
      .mockResolvedValueOnce({ connections: [activeFigmaConnection()] })

    renderOpenModal()
    await settle()

    // The fetch was retried once (401 → recover), not aborted.
    expect(listMock).toHaveBeenCalledTimes(2)

    // The Figma row holds its connected state — no spurious disconnect.
    const text = figmaRow().textContent ?? ""
    expect(text).not.toContain("Not connected")
    expect(text).not.toContain("Connect Figma")
    expect(text).toContain("Connected")
  })

  it("test_generate_modal_layout_stable_under_401_flap: a 401-then-recover renders the same connector layout as a clean load (button does not move)", async () => {
    // Clean load — the baseline layout.
    listMock.mockResolvedValueOnce({ connections: [activeFigmaConnection()] })
    renderOpenModal()
    await settle()
    const cleanRowCount = document.querySelectorAll(".src-row-compact").length
    const cleanFigma = figmaRow().textContent ?? ""
    cleanup()

    // Flap load — same end state, but a transient 401 on the first attempt.
    listMock
      .mockRejectedValueOnce(new ApiError(401, { detail: "expired" }))
      .mockResolvedValueOnce({ connections: [activeFigmaConnection()] })
    renderOpenModal()
    await settle()
    const flapRowCount = document.querySelectorAll(".src-row-compact").length
    const flapFigma = figmaRow().textContent ?? ""

    // Row count is identical → the modal height (and the Generate button below
    // the rows) does not shift between a clean load and a 401 flap.
    expect(flapRowCount).toBe(cleanRowCount)
    // The Figma row never transiently flips to a disconnected layout.
    expect(flapFigma).not.toContain("Not connected")
    expect(flapFigma).not.toContain("Connect Figma")
    expect(flapFigma).toBe(cleanFigma)
    // Sanity: the Generate button is still rendered after the rows.
    expect(document.body.textContent).toContain("Generate")
  })

  it("clears to Not connected only on a genuine non-401 failure", async () => {
    listMock.mockRejectedValue(new ApiError(500, { detail: "server down" }))

    renderOpenModal()
    await settle()

    // No retry budget for a 500 — one attempt, then the rows clear.
    expect(listMock).toHaveBeenCalledTimes(1)
    const text = figmaRow().textContent ?? ""
    expect(text).toContain("Not connected")
  })
})
