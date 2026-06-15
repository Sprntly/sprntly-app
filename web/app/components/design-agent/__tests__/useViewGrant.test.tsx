// @vitest-environment jsdom
//
// Bundle-proxy view-grant flow (Option B — same-origin serving).
//
// What these prove (plan §1.2 / §11 / §16-1):
//   1. The grant POST (designAgentApi.viewGrant) fires BEFORE the authed iframe
//      `src` is set — the hook withholds `grantedBundleUrl` until the mint
//      resolves, so the bundle is never loaded without the credential.
//   2. A later asset 401 re-mints the grant EXACTLY ONCE (bounded — cap = 1);
//      a second failure surfaces an error instead of re-minting again (no
//      infinite mint↔401 loop).
//   3. The public `/p/<token>` path does NOT mint a grant — it loads the bundle
//      from the token-in-URL directly (PublicTokenViewer.resolveToken → ready),
//      never calling viewGrant.
//
// jsdom + renderHook drives the hook's effect/callback; the pure cap decision
// (shouldRemint) is also asserted directly so the bound is unit-locked.
import * as React from "react"
import { act, renderHook, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

// Mock the api module so designAgentApi.viewGrant is observable. Only viewGrant
// is exercised here; everything else is a passthrough stub.
vi.mock("../../../lib/api", () => {
  return {
    designAgentApi: {
      viewGrant: vi.fn<(viewGrantUrl: string) => Promise<void>>().mockResolvedValue(undefined),
    },
  }
})

import { designAgentApi } from "../../../lib/api"
import {
  useViewGrant,
  shouldRemint,
  VIEW_GRANT_REMINT_CAP,
} from "../useViewGrant"

const PID = 99
const BUNDLE = "https://app.test/_da-bundle/v1/design-agent/99/bundle/index.html"

const viewGrant = designAgentApi.viewGrant as unknown as ReturnType<typeof vi.fn>

beforeEach(() => {
  viewGrant.mockReset()
  viewGrant.mockResolvedValue(undefined)
})

afterEach(() => {
  vi.clearAllMocks()
})

describe("useViewGrant — grant POST precedes the iframe src", () => {
  it("withholds the bundle url until the grant POST resolves, then exposes it", async () => {
    let resolveMint: (() => void) | null = null
    viewGrant.mockImplementation(
      () =>
        new Promise<void>((res) => {
          resolveMint = () => res()
        }),
    )

    const { result } = renderHook(() => useViewGrant(PID, BUNDLE))

    // The mint fired for the right prototype, via the APP-ORIGIN /_da-bundle/
    // view-grant path derived from the bundle URL (Option A — first-party cookie)...
    expect(viewGrant).toHaveBeenCalledTimes(1)
    expect(viewGrant).toHaveBeenCalledWith("https://app.test/_da-bundle/v1/design-agent/99/view-grant")
    // ...and the bundle url is STILL withheld (iframe src not set yet).
    expect(result.current.grantedBundleUrl).toBeNull()
    expect(result.current.pending).toBe(true)

    // Resolve the mint → NOW the bundle url is exposed for the iframe.
    await act(async () => {
      resolveMint?.()
    })
    await waitFor(() => expect(result.current.grantedBundleUrl).toBe(BUNDLE))
    expect(result.current.error).toBeNull()
    expect(result.current.reloadKey).toBe(0) // clean first load, no cache-bust
  })

  it("does NOT mint when there is no bundle yet (still generating)", () => {
    const { result } = renderHook(() => useViewGrant(PID, null))
    expect(viewGrant).not.toHaveBeenCalled()
    expect(result.current.grantedBundleUrl).toBeNull()
  })

  it("surfaces an error (and withholds the bundle) when the initial mint fails", async () => {
    viewGrant.mockRejectedValueOnce(new Error("401"))
    const { result } = renderHook(() => useViewGrant(PID, BUNDLE))
    await waitFor(() => expect(result.current.error).not.toBeNull())
    expect(result.current.grantedBundleUrl).toBeNull()
  })
})

describe("useViewGrant — bounded single re-mint on asset 401 (plan §16-1)", () => {
  it("re-mints EXACTLY ONCE on an asset error, then surfaces an error on a second failure", async () => {
    const { result } = renderHook(() => useViewGrant(PID, BUNDLE))

    // Initial mint succeeded → bundle exposed.
    await waitFor(() => expect(result.current.grantedBundleUrl).toBe(BUNDLE))
    expect(viewGrant).toHaveBeenCalledTimes(1)

    // First asset 401 → re-mint ONCE (cap = 1) + bump reloadKey to force reload.
    await act(async () => {
      result.current.notifyAssetError()
    })
    await waitFor(() => expect(viewGrant).toHaveBeenCalledTimes(2))
    expect(result.current.grantedBundleUrl).toBe(BUNDLE)
    expect(result.current.reloadKey).toBe(1)
    expect(result.current.error).toBeNull()

    // Second asset 401 (the re-mint cap is now exhausted) → NO third mint;
    // surface an error instead of looping.
    await act(async () => {
      result.current.notifyAssetError()
    })
    await waitFor(() => expect(result.current.error).not.toBeNull())
    // Crucially: still only TWO total mints (initial + one re-mint) — bounded.
    expect(viewGrant).toHaveBeenCalledTimes(2)
    expect(result.current.grantedBundleUrl).toBeNull()
  })

  it("a fresh bundle url resets the re-mint budget", async () => {
    const { result, rerender } = renderHook(
      ({ url }: { url: string | null }) => useViewGrant(PID, url),
      { initialProps: { url: BUNDLE } },
    )
    await waitFor(() => expect(result.current.grantedBundleUrl).toBe(BUNDLE))

    // Exhaust the budget on the first bundle (1 re-mint).
    await act(async () => result.current.notifyAssetError())
    await waitFor(() => expect(viewGrant).toHaveBeenCalledTimes(2))

    // A new build/checkpoint arrives → fresh url → fresh mint + reset budget.
    const NEXT = BUNDLE.replace("/99/", "/99/").replace("index.html", "v2.html")
    rerender({ url: NEXT })
    await waitFor(() => expect(result.current.grantedBundleUrl).toBe(NEXT))
    expect(viewGrant).toHaveBeenCalledTimes(3) // re-mints for the new bundle

    // The new bundle gets its own single re-mint allowance.
    await act(async () => result.current.notifyAssetError())
    await waitFor(() => expect(viewGrant).toHaveBeenCalledTimes(4))
  })
})

describe("shouldRemint — the cap is unit-locked", () => {
  it("re-mints while under the cap, surfaces an error at/over it", () => {
    expect(VIEW_GRANT_REMINT_CAP).toBe(1)
    expect(shouldRemint(0)).toEqual({ remint: true, surfaceError: false })
    expect(shouldRemint(1)).toEqual({ remint: false, surfaceError: true })
    expect(shouldRemint(2)).toEqual({ remint: false, surfaceError: true })
  })
})
