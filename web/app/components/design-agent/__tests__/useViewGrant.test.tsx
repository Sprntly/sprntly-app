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
  preflightBundle,
  VIEW_GRANT_REMINT_CAP,
} from "../useViewGrant"

const PID = 99
const BUNDLE = "https://app.test/_da-bundle/v1/design-agent/99/bundle/index.html"

const viewGrant = designAgentApi.viewGrant as unknown as ReturnType<typeof vi.fn>

// The hook now preflights the granted bundle (credentialed GET) after each
// (re)mint to detect a 401-bodied index.html the iframe `load` event would hide.
// Mock global fetch so the preflight is deterministic. Default: 200 (healthy
// grant) so the existing assertions about the mint sequence are unaffected; the
// 401 case has its own describe block below.
let fetchMock: ReturnType<typeof vi.fn>

beforeEach(() => {
  viewGrant.mockReset()
  viewGrant.mockResolvedValue(undefined)
  fetchMock = vi
    .fn()
    .mockResolvedValue(new Response("<!doctype html>", { status: 200 }))
  vi.stubGlobal("fetch", fetchMock)
})

afterEach(() => {
  vi.clearAllMocks()
  vi.unstubAllGlobals()
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

// The PROD INCIDENT regression lock: a 401-bodied index.html is a *successful*
// load to the browser (it renders the JSON error and fires the iframe `load`
// event, NOT `error`), so the iframe onError never fired and the bounded re-mint
// never ran. The credentialed preflight closes that link — it inspects the HTTP
// status the iframe load hides and drives the SAME bounded re-mint path.
describe("useViewGrant — 401-bodied index.html preflight drives the bounded re-mint", () => {
  it("re-mints EXACTLY ONCE on a persistent 401 preflight, then withholds the bundle (raw error body never exposed as the iframe src)", async () => {
    // Every preflight GET 401s with a JSON body — the exact prod shape the iframe
    // `load` event would have hidden. The grant POST itself keeps "succeeding"
    // (204), so ONLY the preflight detects the lapsed grant.
    fetchMock.mockResolvedValue(
      new Response('{"detail":"grant required"}', { status: 401 }),
    )

    const { result } = renderHook(() => useViewGrant(PID, BUNDLE))

    // The preflight 401 drives ONE bounded re-mint (initial mint + 1 re-mint),
    // then the cap is exhausted → the bundle is WITHHELD (null) and an error is
    // surfaced. The iframe src is `grantedBundleUrl`, so a null url means the
    // frame is never pointed at the 401 body — the raw {"detail":…} can't render.
    await waitFor(() => expect(result.current.error).not.toBeNull())
    expect(viewGrant).toHaveBeenCalledTimes(2) // initial + EXACTLY ONE re-mint
    expect(result.current.grantedBundleUrl).toBeNull()

    // The preflight is a credentialed, same-origin GET so the path-scoped grant
    // cookie attaches exactly as the iframe asset GETs do.
    expect(fetchMock).toHaveBeenCalledWith(
      BUNDLE,
      expect.objectContaining({ method: "GET", credentials: "include" }),
    )
  })

  it("a healthy (200) preflight does NOT re-mint — the bundle stays exposed", async () => {
    // Default fetchMock is 200; the bundle is exposed and stays put, with no
    // spurious re-mint from the preflight.
    const { result } = renderHook(() => useViewGrant(PID, BUNDLE))
    await waitFor(() => expect(result.current.grantedBundleUrl).toBe(BUNDLE))
    // Give any (non-)preflight re-mint a chance to fire, then assert it did not.
    await act(async () => {
      await Promise.resolve()
    })
    expect(viewGrant).toHaveBeenCalledTimes(1)
    expect(result.current.error).toBeNull()
    expect(result.current.reloadKey).toBe(0)
  })
})

describe("preflightBundle — credentialed 401 detection (pure, injectable fetch)", () => {
  it("reports 'unauthorized' ONLY on a 401, 'ok' otherwise, and on a thrown fetch", async () => {
    const ok = vi.fn().mockResolvedValue(new Response("ok", { status: 200 }))
    const unauthorized = vi.fn().mockResolvedValue(new Response("{}", { status: 401 }))
    const notReady = vi.fn().mockResolvedValue(new Response("nope", { status: 404 }))
    const threw = vi.fn().mockRejectedValue(new Error("network"))

    expect(await preflightBundle(BUNDLE, ok as unknown as typeof fetch)).toBe("ok")
    expect(await preflightBundle(BUNDLE, unauthorized as unknown as typeof fetch)).toBe("unauthorized")
    // A non-401 non-ok (e.g. 404) is NOT the lapsed-grant case → don't burn the
    // re-mint budget; the iframe load path covers it.
    expect(await preflightBundle(BUNDLE, notReady as unknown as typeof fetch)).toBe("ok")
    // A transient network failure would also fail the real iframe load (onError),
    // so the preflight stays on that path and reports "ok".
    expect(await preflightBundle(BUNDLE, threw as unknown as typeof fetch)).toBe("ok")

    // Credentialed + no-store so the grant cookie attaches and the probe is never
    // served a stale cached response.
    expect(ok).toHaveBeenCalledWith(
      BUNDLE,
      expect.objectContaining({ credentials: "include", cache: "no-store" }),
    )
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
