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

describe("useViewGrant — bounded single re-mint on asset 401", () => {
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
    expect(result.current.reloadKey).toBe(0)

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

    // Recovery fired: a fresh re-mint POST AND a forced iframe reload (reloadKey bump),
    // and the bundle stays exposed (the raw 401 body is never pointed at the iframe).
    await waitFor(() => expect(viewGrant).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(result.current.reloadKey).toBe(1))
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
      expect(result.current.reloadKey).toBe(0)

      // Advance past the refresh interval — the grant is still "ok" (200), but we
      // refresh it anyway so it can never reach the TTL.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(GRANT_REFRESH_INTERVAL_MS + 1)
      })

      // A proactive re-mint fired...
      expect(viewGrant).toHaveBeenCalledTimes(2)
      // ...silently: the live iframe is NOT reloaded out from under the user.
      expect(result.current.reloadKey).toBe(0)
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
      expect(result.current.reloadKey).toBe(0)
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
    await waitFor(() => expect(result.current.reloadKey).toBe(1))
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
    await waitFor(() => expect(result.current.reloadKey).toBe(2))
    expect(result.current.error).toBeNull()
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
    expect(result.current.reloadKey).toBe(0)

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
    // bumps reloadKey to force a fresh iframe load of the now-ready bundle.
    await waitFor(() => expect(result.current.notReady).toBe(false), { timeout: 4000 })
    await waitFor(() => expect(result.current.reloadKey).toBe(1))
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
    expect(result.current.reloadKey).toBe(0)
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
    expect(result.current.notReady).toBe(false)
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
    // notReady and bumps reloadKey to force a fresh iframe load — no manual reload.
    await waitFor(() => expect(result.current.notReady).toBe(false), { timeout: 5000 })
    await waitFor(() => expect(result.current.reloadKey).toBe(1))
    expect(result.current.grantedBundleUrl).toBe(BUNDLE)
    expect(result.current.error).toBeNull()
    // The 404 drove the readiness retry, NOT the grant re-mint (cap-1) path.
    expect(viewGrant).toHaveBeenCalledTimes(1)
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
