// @vitest-environment jsdom
//
// Bundle-proxy view-grant flow (Option B — same-origin serving).
//
// What these prove:
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
import { act, cleanup, renderHook, waitFor } from "@testing-library/react"
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
  readinessAction,
  preflightBundle,
  VIEW_GRANT_REMINT_CAP,
  GRANT_REFRESH_INTERVAL_MS,
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
  // Unmount any mounted hook BEFORE clearing globals so its visibilitychange /
  // focus listeners are torn down (they live on the shared window/document) and
  // can't fire into a later test.
  cleanup()
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
    expect(result.current.reloadSignal).toBe(0) // clean first load, no cache-bust
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

describe("useViewGrant — bounded single re-mint on asset 401", () => {
  it("re-mints EXACTLY ONCE on an asset error, then surfaces an error on a second failure", async () => {
    const { result } = renderHook(() => useViewGrant(PID, BUNDLE))

    // Initial mint succeeded → bundle exposed.
    await waitFor(() => expect(result.current.grantedBundleUrl).toBe(BUNDLE))
    expect(viewGrant).toHaveBeenCalledTimes(1)

    // First asset 401 → re-mint ONCE (cap = 1) + bump reloadSignal to force reload.
    await act(async () => {
      result.current.notifyAssetError()
    })
    await waitFor(() => expect(viewGrant).toHaveBeenCalledTimes(2))
    expect(result.current.grantedBundleUrl).toBe(BUNDLE)
    expect(result.current.reloadSignal).toBe(1)
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
    expect(result.current.reloadSignal).toBe(0)
  })
})

// The PROD INCIDENT this whole family of effects exists for: the grant TTL is
// short (~10 min) and, once the initial mint→preflight cycle settled, NOTHING
// re-checked the grant. A viewer left open past the TTL hit a 401-bodied asset
// GET on its next request with no re-mint — only a full manual page reload
// recovered. These prove the viewer now self-heals: on tab refocus, and proactively
// on a timer before the grant can even expire.
describe("useViewGrant — recovers a lapsed grant without a manual reload", () => {
  it("REPRO→FIX: a grant that lapses while the bundle is open re-mints + reloads on tab refocus", async () => {
    const { result } = renderHook(() => useViewGrant(PID, BUNDLE))

    // Initial mint + healthy (200) preflight → bundle exposed, clean first load.
    await waitFor(() => expect(result.current.grantedBundleUrl).toBe(BUNDLE))
    expect(viewGrant).toHaveBeenCalledTimes(1)
    expect(result.current.reloadSignal).toBe(0)

    // The grant TTL elapses while the tab is backgrounded: the recovery probe 401s
    // once (the lapsed grant), then the re-mint restores it so the next probe is
    // healthy again — the real recovery shape. (Pre-fix there was NO listener
    // watching for this, so the lapse went unnoticed until a manual reload — the bug.)
    let probe = 0
    fetchMock.mockImplementation(() => {
      probe += 1
      return Promise.resolve(
        new Response(probe === 1 ? '{"detail":"grant required"}' : "<!doctype html>", {
          status: probe === 1 ? 401 : 200,
        }),
      )
    })

    // Tab comes back to the foreground → recover.
    await act(async () => {
      Object.defineProperty(document, "visibilityState", {
        value: "visible",
        configurable: true,
      })
      document.dispatchEvent(new Event("visibilitychange"))
      await Promise.resolve()
    })

    // Recovery fired: a fresh re-mint POST AND a forced iframe reload (reloadSignal bump),
    // and the bundle stays exposed (the raw 401 body is never pointed at the iframe).
    await waitFor(() => expect(viewGrant).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(result.current.reloadSignal).toBe(1))
    expect(result.current.grantedBundleUrl).toBe(BUNDLE)
    expect(result.current.error).toBeNull()
  })

  it("TIMER: proactively re-mints before the TTL, silently (no iframe reload)", async () => {
    vi.useFakeTimers()
    try {
      const { result } = renderHook(() => useViewGrant(PID, BUNDLE))

      // Let the initial mint + preflight settle under fake timers.
      await act(async () => {
        await vi.runOnlyPendingTimersAsync()
      })
      expect(result.current.grantedBundleUrl).toBe(BUNDLE)
      expect(viewGrant).toHaveBeenCalledTimes(1)
      expect(result.current.reloadSignal).toBe(0)

      // Advance past the refresh interval — the grant is still "ok" (200), but we
      // refresh it anyway so it can never reach the TTL.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(GRANT_REFRESH_INTERVAL_MS + 1)
      })

      // A proactive re-mint fired...
      expect(viewGrant).toHaveBeenCalledTimes(2)
      // ...silently: the live iframe is NOT reloaded out from under the user.
      expect(result.current.reloadSignal).toBe(0)
      expect(result.current.grantedBundleUrl).toBe(BUNDLE)
    } finally {
      vi.useRealTimers()
    }
  })

  it("TIMER tolerates failure: a transient blip on a proactive renewal does NOT blank a healthy viewer", async () => {
    vi.useFakeTimers()
    try {
      const { result } = renderHook(() => useViewGrant(PID, BUNDLE))

      await act(async () => {
        await vi.runOnlyPendingTimersAsync()
      })
      expect(result.current.grantedBundleUrl).toBe(BUNDLE)
      expect(viewGrant).toHaveBeenCalledTimes(1)

      // The proactive renewal hits a transient failure (network blip / 429 / 5xx)
      // while the CURRENT grant is still valid (we refresh under the TTL).
      viewGrant.mockRejectedValueOnce(new Error("transient"))
      await act(async () => {
        await vi.advanceTimersByTimeAsync(GRANT_REFRESH_INTERVAL_MS + 1)
      })

      // It tried to renew (call #2) and failed — but the still-valid grant + bundle
      // are LEFT INTACT: the viewer is NOT blanked and NO error is surfaced. (A
      // genuine lapse is still caught by the preflight / visibility recovery; a
      // proactive pre-expiry renewal failing is harmless and must not tear down a
      // working session — that would itself be a regression introduced by the timer.)
      expect(viewGrant).toHaveBeenCalledTimes(2)
      expect(result.current.grantedBundleUrl).toBe(BUNDLE)
      expect(result.current.error).toBeNull()
      expect(result.current.reloadSignal).toBe(0)
    } finally {
      vi.useRealTimers()
    }
  })

  it("RE-ARMABLE: two separate lapses both recover (cap reset per lapse), no loop within one lapse", async () => {
    const { result } = renderHook(() => useViewGrant(PID, BUNDLE))
    await waitFor(() => expect(result.current.grantedBundleUrl).toBe(BUNDLE))
    expect(viewGrant).toHaveBeenCalledTimes(1)
    // Let the INITIAL post-mint preflight settle so the lapse mock below only
    // governs the recovery probes, not a trailing initial probe.
    await act(async () => {
      await Promise.resolve()
    })

    // --- Lapse #1: preflight 401 once (the lapsed grant), then healthy again. ---
    // A boolean flag, flipped by the recovery re-mint, models "the re-mint restored
    // the grant" — so the re-mint's own post-mint preflight is healthy and the cycle
    // settles: no infinite re-mint↔401 loop within a single lapse.
    let lapsed = true
    viewGrant.mockImplementation(() => {
      lapsed = false // a successful (re)mint restores the grant
      return Promise.resolve(undefined)
    })
    fetchMock.mockImplementation(() =>
      Promise.resolve(
        lapsed
          ? new Response('{"detail":"grant required"}', { status: 401 })
          : new Response("<!doctype html>", { status: 200 }),
      ),
    )

    await act(async () => {
      window.dispatchEvent(new Event("focus"))
      await Promise.resolve()
    })
    await waitFor(() => expect(viewGrant).toHaveBeenCalledTimes(2)) // ONE re-mint
    await waitFor(() => expect(result.current.reloadSignal).toBe(1))
    expect(result.current.error).toBeNull()
    // Settle any trailing post-mint preflight, then prove no extra re-mint looped.
    await act(async () => {
      await Promise.resolve()
    })
    expect(viewGrant).toHaveBeenCalledTimes(2)

    // --- Lapse #2: a NEW lapse later — recovery must fire AGAIN (cap re-armed). ---
    lapsed = true
    await act(async () => {
      window.dispatchEvent(new Event("focus"))
      await Promise.resolve()
    })
    // A THIRD mint — proving the per-lapse cap was re-armed and did not permanently
    // disable recovery after lapse #1.
    await waitFor(() => expect(viewGrant).toHaveBeenCalledTimes(3))
    await waitFor(() => expect(result.current.reloadSignal).toBe(2))
    expect(result.current.error).toBeNull()
  })
})

describe("useViewGrant — manual retryAfterError recovers the terminal error state", () => {
  it("test_retry_after_error_remints_and_recovers_from_terminal_error", async () => {
    viewGrant.mockRejectedValueOnce(new Error("401"))
    const { result } = renderHook(() => useViewGrant(PID, BUNDLE))
    await waitFor(() => expect(result.current.error).not.toBeNull())
    expect(result.current.grantedBundleUrl).toBeNull()
    expect(viewGrant).toHaveBeenCalledTimes(1)

    // Restore the default (resolving) mock, then retry.
    viewGrant.mockResolvedValue(undefined)
    await act(async () => {
      result.current.retryAfterError()
    })

    await waitFor(() => expect(viewGrant).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(result.current.error).toBeNull())
    expect(result.current.grantedBundleUrl).toBe(BUNDLE)
    expect(result.current.reloadSignal).toBe(1)
    // The retry re-mints via the SAME derived view-grant URL as the first call.
    expect(viewGrant.mock.calls[1][0]).toBe(viewGrant.mock.calls[0][0])
  })

  it("test_retry_after_error_is_noop_when_grant_is_healthy", async () => {
    const { result } = renderHook(() => useViewGrant(PID, BUNDLE))
    await waitFor(() => expect(result.current.grantedBundleUrl).toBe(BUNDLE))
    expect(viewGrant).toHaveBeenCalledTimes(1)
    const errorBefore = result.current.error
    const bundleBefore = result.current.grantedBundleUrl
    const reloadSignalBefore = result.current.reloadSignal

    await act(async () => {
      result.current.retryAfterError()
    })

    expect(viewGrant).toHaveBeenCalledTimes(1)
    expect(result.current.error).toBe(errorBefore)
    expect(result.current.grantedBundleUrl).toBe(bundleBefore)
    expect(result.current.reloadSignal).toBe(reloadSignalBefore)
  })

  it("test_retry_after_error_consumes_its_own_remint_budget_so_a_later_asset_error_surfaces_immediately", async () => {
    viewGrant.mockRejectedValueOnce(new Error("401"))
    const { result } = renderHook(() => useViewGrant(PID, BUNDLE))
    await waitFor(() => expect(result.current.error).not.toBeNull())

    viewGrant.mockResolvedValue(undefined)
    await act(async () => {
      result.current.retryAfterError()
    })
    await waitFor(() => expect(viewGrant).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(result.current.error).toBeNull())

    // One subsequent asset 401 — the budget the successful retry consumed is
    // NOT reset, so this surfaces the terminal error again immediately, with
    // no third mint call (intentional parity with recoverIfLapsed's budget
    // bookkeeping — see Implementation Notes).
    await act(async () => {
      result.current.notifyAssetError()
    })
    await waitFor(() => expect(result.current.error).not.toBeNull())
    expect(viewGrant).toHaveBeenCalledTimes(2)
  })

  it("test_retry_after_error_can_be_invoked_again_after_a_second_failed_retry", async () => {
    viewGrant.mockRejectedValueOnce(new Error("401"))
    const { result } = renderHook(() => useViewGrant(PID, BUNDLE))
    await waitFor(() => expect(result.current.error).not.toBeNull())

    viewGrant.mockResolvedValue(undefined)
    await act(async () => {
      result.current.retryAfterError()
    })
    await waitFor(() => expect(viewGrant).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(result.current.error).toBeNull())

    await act(async () => {
      result.current.notifyAssetError()
    })
    await waitFor(() => expect(result.current.error).not.toBeNull())
    expect(viewGrant).toHaveBeenCalledTimes(2)

    // A SECOND explicit retry click gets its own fresh attempt — no permanent
    // lockout across repeated manual clicks.
    await act(async () => {
      result.current.retryAfterError()
    })
    await waitFor(() => expect(viewGrant).toHaveBeenCalledTimes(3))
    await waitFor(() => expect(result.current.error).toBeNull())
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
    // A 404 is the briefly-unavailable bundle (a not-yet-staged build) — it is
    // NOT the lapsed-grant case, so it never burns the re-mint budget; it drives
    // the bounded readiness retry + loading state instead.
    expect(await preflightBundle(BUNDLE, notReady as unknown as typeof fetch)).toBe("notready")
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

describe("readinessAction — the iframe-load probe decision is unit-locked", () => {
  it("maps each probe result to its action", () => {
    // A 401 routes through the grant re-mint; a 404 (briefly-unavailable bundle)
    // drives the loading state + bounded retry; an ok clears the loading state.
    expect(readinessAction("unauthorized")).toBe("remint")
    expect(readinessAction("notready")).toBe("retry")
    expect(readinessAction("ok")).toBe("clear")
  })
})

// The prod incident this whole readiness path exists for: after an iteration the
// preview iframe painted the raw `{"detail":"Not found"}` 404 body from the bundle
// proxy (a not-yet-staged build). A 404 body fires the iframe `load` event, NOT
// `error`, so onError never caught it. notifyBundleLoaded — wired to the iframe
// onLoad — probes the real status and recovers: it covers the iframe with a
// loading state, then reloads the bundle once it is ready, with NO manual reload.
describe("useViewGrant — bundle-readiness recovery via the iframe onLoad probe", () => {
  it("REPRO→FIX: a 404 on load sets notReady, then clears + reloads once the bundle is ready", async () => {
    const { result } = renderHook(() => useViewGrant(PID, BUNDLE))
    // Initial mint + healthy (200) post-mint preflight → bundle exposed.
    await waitFor(() => expect(result.current.grantedBundleUrl).toBe(BUNDLE))
    expect(result.current.notReady).toBe(false)
    expect(result.current.reloadSignal).toBe(0)

    // The iframe loads but the proxy is briefly 404ing the bundle: the first probe
    // (the onLoad probe) AND the first retry both 404; the second retry is ready.
    let probe = 0
    fetchMock.mockImplementation(() => {
      probe += 1
      const notReadyStill = probe <= 2
      return Promise.resolve(
        new Response(notReadyStill ? '{"detail":"Not found"}' : "<!doctype html>", {
          status: notReadyStill ? 404 : 200,
        }),
      )
    })

    // The iframe fired `load` on the 404 body — the container calls notifyBundleLoaded.
    await act(async () => {
      result.current.notifyBundleLoaded()
      await Promise.resolve()
    })
    // The loading state is up (the raw 404 body is covered).
    await waitFor(() => expect(result.current.notReady).toBe(true))

    // The bounded retry re-probes; once the bundle is ready it clears notReady and
    // bumps reloadSignal to force a fresh iframe load of the now-ready bundle.
    await waitFor(() => expect(result.current.notReady).toBe(false), { timeout: 4000 })
    await waitFor(() => expect(result.current.reloadSignal).toBe(1))
    expect(result.current.grantedBundleUrl).toBe(BUNDLE)
    expect(result.current.error).toBeNull()
  })

  it("a clean (200) load is a no-op — no loading state, no reload", async () => {
    // Default fetchMock is 200 throughout. A normal load probes ok → notReady
    // stays false and the iframe is never forced to reload.
    const { result } = renderHook(() => useViewGrant(PID, BUNDLE))
    await waitFor(() => expect(result.current.grantedBundleUrl).toBe(BUNDLE))

    await act(async () => {
      result.current.notifyBundleLoaded()
      await Promise.resolve()
    })
    // Give any spurious retry a chance to fire, then assert it did not.
    await act(async () => {
      await Promise.resolve()
    })
    expect(result.current.notReady).toBe(false)
    expect(result.current.reloadSignal).toBe(0)
    expect(result.current.error).toBeNull()
  })

  it("a 401 on load routes through the bounded re-mint, not the readiness retry", async () => {
    const { result } = renderHook(() => useViewGrant(PID, BUNDLE))
    await waitFor(() => expect(result.current.grantedBundleUrl).toBe(BUNDLE))
    expect(viewGrant).toHaveBeenCalledTimes(1)

    // The onLoad probe sees a 401 (a 401 body also fires `load`): hand off to the
    // bounded grant re-mint (cap = 1) — NOT the readiness retry.
    fetchMock.mockResolvedValue(
      new Response('{"detail":"grant required"}', { status: 401 }),
    )
    await act(async () => {
      result.current.notifyBundleLoaded()
      await Promise.resolve()
    })
    await waitFor(() => expect(viewGrant).toHaveBeenCalledTimes(2)) // one re-mint
    // The re-mint succeeds, but this test's fetchMock never stops 401ing, so the
    // post-remint preflight consumes the cap's second (terminal) check and
    // surfaces the error — bounded at exactly 2 total viewGrant calls, no third.
    // notReady now masks the iframe for the whole recovery window (the fix this
    // ticket adds) and is intentionally left set once the terminal error lands;
    // grantedBundleUrl going null unmounts the viewer, so the stuck flag has no
    // visible render effect (see useViewGrant.ts's handleReadiness doc comment).
    await waitFor(() => expect(result.current.error).not.toBeNull())
    expect(result.current.grantedBundleUrl).toBeNull()
    expect(viewGrant).toHaveBeenCalledTimes(2)
  })

  it("REPRO→FIX: the post-mint preflight (not onLoad) that sees 404 sets notReady + recovers", async () => {
    // The prod gap this closes: right after a (re)mint — first load / post-
    // completion — the proxy can briefly 404 a freshly-staged build. The post-mint
    // preflight effect ALREADY runs on every (re)mint, but used to DROP "notready"
    // (it only handled 401), so that first-load transient-404 flash was not
    // covered until the iframe onLoad probe fired. Wiring "notready" into the same
    // readiness path closes it with no extra probe. RED before the fix: notReady
    // never went true and no retry was scheduled because the post-mint preflight
    // ignored the 404.
    //
    // Make the VERY FIRST preflight (the post-mint one) 404, the next two 404, then
    // ready — exercising the bounded retry the post-mint path now drives.
    let probe = 0
    fetchMock.mockImplementation(() => {
      probe += 1
      const notReadyStill = probe <= 3
      return Promise.resolve(
        new Response(notReadyStill ? '{"detail":"Not found"}' : "<!doctype html>", {
          status: notReadyStill ? 404 : 200,
        }),
      )
    })

    const { result } = renderHook(() => useViewGrant(PID, BUNDLE))
    // Mint resolves and exposes the bundle; the post-mint preflight then 404s.
    await waitFor(() => expect(result.current.grantedBundleUrl).toBe(BUNDLE))
    // The post-mint preflight saw 404 → the loading state is up WITHOUT any
    // onLoad call (we never invoke notifyBundleLoaded here).
    await waitFor(() => expect(result.current.notReady).toBe(true))

    // The bounded retry the post-mint path started re-probes; once ready it clears
    // notReady and bumps reloadSignal to force a fresh iframe load — no manual reload.
    await waitFor(() => expect(result.current.notReady).toBe(false), { timeout: 5000 })
    await waitFor(() => expect(result.current.reloadSignal).toBe(1))
    expect(result.current.grantedBundleUrl).toBe(BUNDLE)
    expect(result.current.error).toBeNull()
    // The 404 drove the readiness retry, NOT the grant re-mint (cap-1) path.
    expect(viewGrant).toHaveBeenCalledTimes(1)
  })

  it("test_use_view_grant_notify_bundle_loaded_resolves_only_after_readiness_settles", async () => {
    const { result } = renderHook(() => useViewGrant(PID, BUNDLE))
    await waitFor(() => expect(result.current.grantedBundleUrl).toBe(BUNDLE))
    // Let the post-mint preflight (default 200) settle before swapping the mock,
    // so we don't race an in-flight preflight the mint itself already started.
    await waitFor(() => expect(result.current.notReady).toBe(false))

    let resolveFetch: (value: Response) => void = () => {}
    fetchMock.mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          resolveFetch = resolve
        }),
    )

    let settled = false
    let notifyPromise: Promise<void> = Promise.resolve()
    await act(async () => {
      notifyPromise = result.current.notifyBundleLoaded()
      notifyPromise.then(() => {
        settled = true
      })
      await Promise.resolve()
    })
    // The preflight fetch is still pending — the readiness decision hasn't
    // been made, so the returned promise must not have resolved yet.
    expect(settled).toBe(false)

    await act(async () => {
      resolveFetch(new Response("<!doctype html>", { status: 200 }))
      await notifyPromise
    })
    expect(settled).toBe(true)
  })
})

// The checkpoint-advance stale-grant incident: `da_view_grant` is bound to a
// checkpoint at mint time, and an iterate that overwrites the bundle in place
// (the common case — bundle_url unchanged) leaves a grant that LOOKS fine but
// is semantically stale — the bundle-proxy 401s ("grant stale") the moment the
// next checkpoint outpaces it. The mint-triggering effect used to key ONLY on
// bundleUrl, so a checkpoint-only change was inert until the next reload
// tripped the stale-grant 401. These prove the third `checkpointId` parameter
// closes that gap proactively.
describe("useViewGrant — checkpoint-id change triggers a fresh mint", () => {
  it("test_use_view_grant_checkpoint_id_change_triggers_fresh_mint_bundle_url_unchanged", async () => {
    const { result, rerender } = renderHook(
      ({ cp }: { cp: number | null }) => useViewGrant(PID, BUNDLE, cp),
      { initialProps: { cp: 1 } },
    )
    await waitFor(() => expect(result.current.grantedBundleUrl).toBe(BUNDLE))
    expect(viewGrant).toHaveBeenCalledTimes(1)

    // The checkpoint alone advances — bundle_url is IDENTICAL throughout (the
    // in-place-overwrite iterate case) — yet a fresh mint must fire.
    rerender({ cp: 2 })
    await waitFor(() => expect(viewGrant).toHaveBeenCalledTimes(2))
    // Same derived view-grant URL — proving the trigger was the checkpoint
    // change, not a (nonexistent) bundle_url change.
    expect(viewGrant.mock.calls[1][0]).toBe(viewGrant.mock.calls[0][0])
    expect(result.current.grantedBundleUrl).toBe(BUNDLE)
  })

  it("test_use_view_grant_remint_cap_still_bounded_when_checkpoint_driven", async () => {
    const { result, rerender } = renderHook(
      ({ cp }: { cp: number | null }) => useViewGrant(PID, BUNDLE, cp),
      { initialProps: { cp: 1 } },
    )
    await waitFor(() => expect(result.current.grantedBundleUrl).toBe(BUNDLE))

    // Exhaust the re-mint budget on checkpoint 1 (initial + 1 re-mint, then cap).
    await act(async () => result.current.notifyAssetError())
    await waitFor(() => expect(viewGrant).toHaveBeenCalledTimes(2))
    await act(async () => result.current.notifyAssetError())
    await waitFor(() => expect(result.current.error).not.toBeNull())
    expect(viewGrant).toHaveBeenCalledTimes(2) // still bounded for checkpoint 1

    // A NEW checkpoint arrives — a fresh mint AND a fresh re-mint budget, not a
    // second uncapped path around VIEW_GRANT_REMINT_CAP.
    rerender({ cp: 2 })
    await waitFor(() => expect(viewGrant).toHaveBeenCalledTimes(3))
    expect(result.current.error).toBeNull()

    await act(async () => result.current.notifyAssetError())
    await waitFor(() => expect(viewGrant).toHaveBeenCalledTimes(4))
    await act(async () => result.current.notifyAssetError())
    await waitFor(() => expect(result.current.error).not.toBeNull())
    expect(viewGrant).toHaveBeenCalledTimes(4) // bounded again for checkpoint 2
  })

  it("test_use_view_grant_bundle_url_change_still_triggers_fresh_mint", async () => {
    // Regression-pin: the checkpoint-dependency addition must not disturb the
    // pre-existing bundleUrl-change trigger — checkpointId held stable here.
    const { result, rerender } = renderHook(
      ({ url }: { url: string | null }) => useViewGrant(PID, url, 7),
      { initialProps: { url: BUNDLE } },
    )
    await waitFor(() => expect(result.current.grantedBundleUrl).toBe(BUNDLE))
    expect(viewGrant).toHaveBeenCalledTimes(1)

    const NEXT = BUNDLE.replace("index.html", "v2.html")
    rerender({ url: NEXT })
    await waitFor(() => expect(result.current.grantedBundleUrl).toBe(NEXT))
    expect(viewGrant).toHaveBeenCalledTimes(2)
  })

  it("test_use_view_grant_unrelated_rerender_no_extra_mint", async () => {
    const { result, rerender } = renderHook(
      ({ cp }: { cp: number | null }) => useViewGrant(PID, BUNDLE, cp),
      { initialProps: { cp: 3 } },
    )
    await waitFor(() => expect(result.current.grantedBundleUrl).toBe(BUNDLE))
    expect(viewGrant).toHaveBeenCalledTimes(1)

    // Re-render with the SAME bundleUrl and SAME checkpointId (an unrelated
    // prop change elsewhere in the tree) — no additional mint.
    rerender({ cp: 3 })
    await act(async () => {
      await Promise.resolve()
    })
    expect(viewGrant).toHaveBeenCalledTimes(1)
  })

  it("test_use_view_grant_checkpoint_id_null_by_default_is_a_noop_change", async () => {
    const { result, rerender } = renderHook(
      ({ withCp }: { withCp: boolean }) =>
        withCp ? useViewGrant(PID, BUNDLE, null) : useViewGrant(PID, BUNDLE),
      { initialProps: { withCp: false } },
    )
    await waitFor(() => expect(result.current.grantedBundleUrl).toBe(BUNDLE))
    expect(viewGrant).toHaveBeenCalledTimes(1)

    // Switching from the implicit (omitted third arg) default to an EXPLICIT
    // null is the same value — no spurious re-mint from the default itself.
    rerender({ withCp: true })
    await act(async () => {
      await Promise.resolve()
    })
    expect(viewGrant).toHaveBeenCalledTimes(1)
  })
})

// The masking-during-recovery bug: `handleReadiness`'s "remint" branch called
// `setNotReady(false)` right as the bounded 401 re-mint kicked off, so the raw
// 401-bodied response stayed visible for the 1-3s recovery window — the actual
// "red error, doesn't load" David reported. The fix masks the iframe for the
// duration of ANY 401 recovery (not just the pre-existing 404 case).
describe("useViewGrant — the 401-recovery masking fix", () => {
  it("test_use_view_grant_remint_masks_the_iframe_during_401_recovery", async () => {
    const { result } = renderHook(() => useViewGrant(PID, BUNDLE))
    await waitFor(() => expect(result.current.grantedBundleUrl).toBe(BUNDLE))
    // Let the initial post-mint preflight (default 200) settle before driving
    // the scenario below, so it can't race the 401 preflight we queue next.
    await waitFor(() => expect(result.current.notReady).toBe(false))

    // The next preflight (driven by the iframe onLoad probe) reports a 401 —
    // the grant lapsed. Hold the re-mint's viewGrant POST pending so we can
    // observe the masking state WHILE the recovery is still in flight.
    fetchMock.mockResolvedValueOnce(
      new Response('{"detail":"grant required"}', { status: 401 }),
    )
    let remintResolved = false
    let resolveRemint: (() => void) | null = null
    viewGrant.mockImplementationOnce(
      () =>
        new Promise<void>((res) => {
          resolveRemint = () => {
            remintResolved = true
            res()
          }
        }),
    )

    await act(async () => {
      result.current.notifyBundleLoaded()
      await Promise.resolve()
    })

    // The actual bug: pre-fix, this branch called setNotReady(false), so
    // notReady never went true during the recovery window.
    await waitFor(() => expect(result.current.notReady).toBe(true))
    expect(remintResolved).toBe(false) // still masked WHILE the re-mint is pending
    expect(viewGrant).toHaveBeenCalledTimes(2) // initial + the in-flight re-mint

    // Resolve the re-mint; the post-remint preflight (bumped reloadSignal) then
    // reports healthy — the loop closes via the EXISTING "clear" branch, with
    // no new polling mechanism.
    fetchMock.mockResolvedValue(new Response("<!doctype html>", { status: 200 }))
    await act(async () => {
      resolveRemint?.()
      await Promise.resolve()
    })

    await waitFor(() => expect(result.current.notReady).toBe(false))
    expect(result.current.grantedBundleUrl).toBe(BUNDLE)
    expect(result.current.error).toBeNull()
  })
})

// CONSOLIDATED REDESIGN: two deeper races a dedicated system-scan investigation
// found underneath the checkpoint-id fix above. Race A — the checkpoint effect
// (unchanged, still fires on every checkpointId change) used to have NOTHING
// wired from a successful checkpoint-driven mint to a forced reload; the
// caller (`InTabCanvas`) bumped its OWN nonce synchronously, racing ahead of
// this hook's mint. Now `reloadSignal` is the SOLE reload signal, bumped only
// AFTER a mint for the new checkpoint CONFIRMS success. Race B — the bounded
// re-mint counter used to increment unconditionally in `notifyAssetError`,
// before knowing whether `mint()` actually attempted a network call or was
// silently coalesced against another in-flight mint — a coincidental collision
// between any two of the hook's mint triggers could exhaust the single-retry
// budget with ZERO real attempts. `mint()` now coalesces (awaits + reconciles)
// instead of silently dropping, and returns `Promise<boolean>` so the caller
// knows whether ITS OWN call performed a genuine attempt.
describe("useViewGrant — checkpoint-advance reload signal waits for a CONFIRMED mint (Race A)", () => {
  it("test_use_view_grant_checkpoint_advance_reload_signal_waits_for_confirmed_mint_success", async () => {
    const { result, rerender } = renderHook(
      ({ cp }: { cp: number | null }) => useViewGrant(PID, BUNDLE, cp),
      { initialProps: { cp: 1 } },
    )
    await waitFor(() => expect(result.current.grantedBundleUrl).toBe(BUNDLE))
    expect(result.current.reloadSignal).toBe(0)

    // Hold the checkpoint-2 mint's viewGrant call pending so the PRE-resolve
    // state is observable — this is the whole point: the assertion below must
    // hold WHILE the triggering mint is still in flight, not just eventually.
    let resolveCp2Mint: (() => void) | null = null
    viewGrant.mockImplementationOnce(
      () => new Promise<void>((res) => { resolveCp2Mint = () => res() }),
    )

    rerender({ cp: 2 })
    await waitFor(() => expect(viewGrant).toHaveBeenCalledTimes(2))

    // The triggering mint is CONFIRMED IN FLIGHT (viewGrant already called a
    // second time for checkpoint 2) but NOT YET resolved — reloadSignal must
    // be UNCHANGED from its pre-rerender value. On the unfixed hook (no
    // checkpoint→reloadSignal wiring at all) this assertion trivially holds
    // too, but the NEXT assertion (after resolving) is what fails pre-fix —
    // together they prove the ORDER, not just the eventual state.
    expect(result.current.reloadSignal).toBe(0)

    await act(async () => {
      resolveCp2Mint?.()
    })

    // Only AFTER the mint resolves does reloadSignal advance, by exactly 1.
    await waitFor(() => expect(result.current.reloadSignal).toBe(1))
    await act(async () => { await Promise.resolve() })
    expect(result.current.reloadSignal).toBe(1)
  })
})

describe("useViewGrant — mutex collisions are coalesced/reconciled, never silently dropped (Race B)", () => {
  it("test_use_view_grant_mutex_collision_does_not_burn_the_retry_budget_without_a_real_attempt", async () => {
    const { result } = renderHook(() => useViewGrant(PID, BUNDLE))
    await waitFor(() => expect(result.current.grantedBundleUrl).toBe(BUNDLE))
    expect(viewGrant).toHaveBeenCalledTimes(1)

    // Hold the FIRST notifyAssetError-triggered mint pending so a second,
    // colliding notifyAssetError() call (fired before it resolves) has
    // something to coalesce into.
    let resolveFirstAttempt: (() => void) | null = null
    viewGrant.mockImplementationOnce(
      () => new Promise<void>((res) => { resolveFirstAttempt = () => res() }),
    )

    act(() => {
      result.current.notifyAssetError() // call A — starts the real attempt (pending)
      result.current.notifyAssetError() // call B — collides, must NOT issue its own POST
    })

    // Exactly ONE network attempt for the colliding pair (call B coalesced,
    // not a silent no-op that dropped its own need on the floor).
    expect(viewGrant).toHaveBeenCalledTimes(2) // initial mint + call A's attempt

    // CRITICAL (this is what the unconditional-increment mutation breaks):
    // call B must NOT have synchronously hit the cap and surfaced the
    // terminal error while call A is still in flight. If the budget were
    // burned unconditionally (pre-fix), call B's own shouldRemint check would
    // already see the cap exhausted from call A's synchronous pre-increment,
    // and would surface the terminal error/null the grant RIGHT HERE — before
    // call A even resolves. Under the fix, both calls issue a `remint=true`
    // decision (the ref hasn't incremented yet — that only happens once
    // mint() confirms an outcome), so the grant stays healthy throughout.
    expect(result.current.error).toBeNull()
    expect(result.current.grantedBundleUrl).toBe(BUNDLE)

    await act(async () => {
      resolveFirstAttempt?.()
    })

    // Still exactly one re-mint attempt settled for the pair — call B's need
    // was satisfied by call A's success, so the single VIEW_GRANT_REMINT_CAP
    // budget was consumed exactly once, not twice (and not zero times either).
    await waitFor(() => expect(result.current.grantedBundleUrl).toBe(BUNDLE))
    expect(viewGrant).toHaveBeenCalledTimes(2)
    expect(result.current.error).toBeNull()

    // --- Focus-recovery reset path: a GENUINE fresh lapse (detected
    // independently by recoverIfLapsed's own preflight, via the visibility/
    // focus effect) resets the budget, so a later notifyAssetError() gets its
    // OWN fresh attempt — proving the collision above did not permanently
    // exhaust recovery for this viewer's whole lifetime. (Deliberately NOT
    // asserting about a third notifyAssetError() call within the SAME
    // uncleared lapse window with no reset in between — that case correctly
    // stays capped and is already covered by the pre-existing bounded-re-mint
    // test above; this test only exercises the focus-recovery reset path.)
    let lapsed = true
    fetchMock.mockImplementation(() =>
      Promise.resolve(
        lapsed
          ? new Response('{"detail":"grant required"}', { status: 401 })
          : new Response("<!doctype html>", { status: 200 }),
      ),
    )
    viewGrant.mockImplementation(() => {
      lapsed = false // a successful (re)mint restores the grant
      return Promise.resolve(undefined)
    })

    await act(async () => {
      window.dispatchEvent(new Event("focus"))
    })

    await waitFor(() => expect(viewGrant).toHaveBeenCalledTimes(3))
    await waitFor(() => expect(result.current.error).toBeNull())
  })

  it("test_use_view_grant_mutex_collision_reconciles_a_request_the_in_flight_mint_did_not_satisfy", async () => {
    vi.useFakeTimers()
    try {
      const { result } = renderHook(() => useViewGrant(PID, BUNDLE))
      await act(async () => {
        await vi.runOnlyPendingTimersAsync()
      })
      expect(result.current.grantedBundleUrl).toBe(BUNDLE)
      expect(viewGrant).toHaveBeenCalledTimes(1)

      // The proactive silent renewal (forceReload=false) is put in flight and
      // will FAIL — hold it pending so a colliding notifyAssetError() call has
      // something to collide with that will NOT satisfy its own need.
      let rejectProactive: ((e: Error) => void) | null = null
      viewGrant.mockImplementationOnce(
        () => new Promise<void>((_res, rej) => { rejectProactive = rej }),
      )

      await act(async () => {
        await vi.advanceTimersByTimeAsync(GRANT_REFRESH_INTERVAL_MS + 1)
      })
      expect(viewGrant).toHaveBeenCalledTimes(2) // the proactive renewal's own POST

      // A genuine asset-error lapse collides with the still-pending proactive
      // renewal.
      act(() => {
        result.current.notifyAssetError()
      })
      // Still only the ONE (proactive) network call in flight — the collision
      // did not fire a second, concurrent POST.
      expect(viewGrant).toHaveBeenCalledTimes(2)

      // The proactive renewal FAILS (swallowed — its pre-existing contract: a
      // transient failure on a silent renewal must not tear down a healthy
      // viewer). It did NOT satisfy the colliding request's own need.
      viewGrant.mockResolvedValue(undefined) // the reconciled retry below succeeds
      await act(async () => {
        rejectProactive?.(new Error("transient"))
      })

      // Switch to real timers before polling with waitFor below — fake timers
      // would otherwise stall waitFor's own internal retry loop (the rest of
      // this reconciliation is pure microtask/promise chaining, not driven by
      // any further setTimeout/interval).
      vi.useRealTimers()

      // The colliding request is RECONCILED, not dropped: it retries for real
      // once the mutex frees, and that retry succeeds — granting/reload-
      // signalling normally (an explicit re-mint, so reloadSignal bumps).
      await waitFor(() => expect(viewGrant).toHaveBeenCalledTimes(3))
      await waitFor(() => expect(result.current.reloadSignal).toBe(1))
      expect(result.current.error).toBeNull()
      expect(result.current.grantedBundleUrl).toBe(BUNDLE)
    } finally {
      vi.useRealTimers()
    }
  })
})

describe("useViewGrant — mint() return-value + reload-signal creation/behaviour", () => {
  it("test_use_view_grant_mint_returns_true_when_it_performs_the_network_call", async () => {
    const { result } = renderHook(() => useViewGrant(PID, BUNDLE))
    await waitFor(() => expect(result.current.grantedBundleUrl).toBe(BUNDLE))

    // A collision-free notifyAssetError() call performs its own network
    // attempt (mint() resolves `true`), so it alone consumes the single
    // re-mint budget slot — observable as: a SECOND notifyAssetError() call
    // (still within the same uncleared lapse, no reset in between) is now
    // capped and surfaces the terminal error, proving the FIRST call's
    // mint() genuinely ran (a coalesced `false` would never have consumed the
    // budget — see the mutex-collision tests above).
    await act(async () => {
      result.current.notifyAssetError()
    })
    await waitFor(() => expect(viewGrant).toHaveBeenCalledTimes(2))
    expect(result.current.error).toBeNull()

    await act(async () => {
      result.current.notifyAssetError()
    })
    await waitFor(() => expect(result.current.error).not.toBeNull())
    expect(viewGrant).toHaveBeenCalledTimes(2) // no third attempt — budget was genuinely spent
  })

  it("test_use_view_grant_checkpoint_advance_same_bundle_url_bumps_reload_signal_exactly_once", async () => {
    const { result, rerender } = renderHook(
      ({ cp }: { cp: number | null }) => useViewGrant(PID, BUNDLE, cp),
      { initialProps: { cp: 1 } },
    )
    await waitFor(() => expect(result.current.grantedBundleUrl).toBe(BUNDLE))
    expect(result.current.reloadSignal).toBe(0)

    rerender({ cp: 2 })
    await waitFor(() => expect(viewGrant).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(result.current.reloadSignal).toBe(1))

    // Settle — no further bump beyond the one genuine checkpoint advance.
    await act(async () => { await Promise.resolve() })
    expect(result.current.reloadSignal).toBe(1)
  })

  it("test_use_view_grant_initial_mint_for_a_fresh_bundle_url_never_bumps_reload_signal", async () => {
    const { result } = renderHook(() => useViewGrant(PID, BUNDLE, 1))
    await waitFor(() => expect(result.current.grantedBundleUrl).toBe(BUNDLE))
    expect(viewGrant).toHaveBeenCalledTimes(1)
    expect(result.current.reloadSignal).toBe(0)
  })

  it("test_use_view_grant_bundle_url_change_alone_does_not_bump_reload_signal", async () => {
    const { result, rerender } = renderHook(
      ({ url }: { url: string | null }) => useViewGrant(PID, url, 5),
      { initialProps: { url: BUNDLE } },
    )
    await waitFor(() => expect(result.current.grantedBundleUrl).toBe(BUNDLE))
    expect(result.current.reloadSignal).toBe(0)

    const NEXT = BUNDLE.replace("index.html", "v2.html")
    rerender({ url: NEXT })
    await waitFor(() => expect(result.current.grantedBundleUrl).toBe(NEXT))
    expect(viewGrant).toHaveBeenCalledTimes(2)
    expect(result.current.reloadSignal).toBe(0)
  })

  it("test_use_view_grant_explicit_remint_success_still_bumps_reload_signal", async () => {
    const { result } = renderHook(() => useViewGrant(PID, BUNDLE))
    await waitFor(() => expect(result.current.grantedBundleUrl).toBe(BUNDLE))
    expect(result.current.reloadSignal).toBe(0)

    await act(async () => {
      result.current.notifyAssetError()
    })
    await waitFor(() => expect(result.current.reloadSignal).toBe(1))
    expect(result.current.error).toBeNull()
  })
})

describe("useViewGrant — edge cases: per-url tracking + bounded recursive collision retry", () => {
  it("test_use_view_grant_checkpoint_advance_after_a_bundle_url_change_still_requires_its_own_advance", async () => {
    const { result, rerender } = renderHook(
      ({ url, cp }: { url: string; cp: number | null }) => useViewGrant(PID, url, cp),
      { initialProps: { url: BUNDLE, cp: 1 } },
    )
    await waitFor(() => expect(result.current.grantedBundleUrl).toBe(BUNDLE))
    expect(viewGrant).toHaveBeenCalledTimes(1)
    expect(result.current.reloadSignal).toBe(0)

    // A bundle_url change (a genuinely NEW build) — a fresh mint for the new
    // url, but NOT a "checkpoint advance": mintedForRef has no prior entry
    // for THIS url, so it must not leak a stale comparison across urls.
    const NEXT = BUNDLE.replace("index.html", "v2.html")
    rerender({ url: NEXT, cp: 1 })
    await waitFor(() => expect(result.current.grantedBundleUrl).toBe(NEXT))
    expect(viewGrant).toHaveBeenCalledTimes(2)
    expect(result.current.reloadSignal).toBe(0) // first mint for THIS url — no bump

    // NOW a genuine checkpoint-only advance on the NEW url — bumps exactly once.
    rerender({ url: NEXT, cp: 2 })
    await waitFor(() => expect(viewGrant).toHaveBeenCalledTimes(3))
    await waitFor(() => expect(result.current.reloadSignal).toBe(1))
  })

  it("test_use_view_grant_recursive_collision_retry_terminates", async () => {
    const { result } = renderHook(() => useViewGrant(PID, BUNDLE))
    await waitFor(() => expect(result.current.grantedBundleUrl).toBe(BUNDLE))
    expect(viewGrant).toHaveBeenCalledTimes(1)

    // Three requests collide on the SAME in-flight attempt; that attempt
    // FAILS, forcing exactly one coalesced caller to retry for real — proving
    // the recursive collision-retry converges to a bounded, finite sequence
    // of real network calls rather than fanning out one retry per caller.
    let rejectFirst: ((e: Error) => void) | null = null
    viewGrant.mockImplementationOnce(
      () => new Promise<void>((_res, rej) => { rejectFirst = rej }),
    )

    act(() => {
      result.current.notifyAssetError() // call 1 — real attempt, held pending
      result.current.notifyAssetError() // call 2 — collides
      result.current.notifyAssetError() // call 3 — collides
    })
    expect(viewGrant).toHaveBeenCalledTimes(2) // initial + the one held attempt

    viewGrant.mockResolvedValue(undefined) // the forced retry succeeds
    await act(async () => {
      rejectFirst?.(new Error("first attempt failed"))
    })

    // Bounded: at most one retry beyond the initial failed attempt (never one
    // retry PER colliding caller, which would be unbounded as callers stack).
    await waitFor(() => expect(result.current.grantedBundleUrl).toBe(BUNDLE))
    await waitFor(() => expect(result.current.error).toBeNull())
    expect(viewGrant.mock.calls.length).toBeLessThanOrEqual(3)

    // Settles to a stable state — no further network call fires on its own.
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(viewGrant.mock.calls.length).toBeLessThanOrEqual(3)
  })
})

describe("GRANT_REFRESH_INTERVAL_MS — env-tunable, prod-default-locked", () => {
  it("defaults to 5 minutes when NEXT_PUBLIC_DA_GRANT_REFRESH_MS is unset", () => {
    // No override is set in the test environment, so the proactive-refresh cadence
    // must be the 5-minute production default — proving the env knob never silently
    // shifts prod behavior. (The override is read at build time; this locks the
    // default so a deploy without the env var is byte-identical to pre-knob.)
    expect(process.env.NEXT_PUBLIC_DA_GRANT_REFRESH_MS).toBeUndefined()
    expect(GRANT_REFRESH_INTERVAL_MS).toBe(5 * 60 * 1000)
  })
})
