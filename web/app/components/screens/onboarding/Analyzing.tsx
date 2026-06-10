"use client"

import { useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { useOnboarding } from "../../../context/OnboardingContext"
import { onboardingApi } from "../../../lib/api"

/**
 * Onboarding interstitial — "Gathering information about your business".
 *
 * This is a TRANSIENT, UNNUMBERED route (`/onboarding/analyzing`): it is not in
 * ONBOARDING_SCREENS, it is not back-navigable, and it is excluded from the
 * progress-dot count (it renders no dots). It sits between the business-info
 * page (step 1) and the metrics page (`/onboarding/metrics`).
 *
 * On mount it AWAITS the website analysis (POST /v1/onboarding/analyze-website),
 * stashes the result on OnboardingContext, then forwards to the metrics page.
 *
 * RESILIENCE — onboarding must always complete:
 *   - If the call rejects (transport failure), we still forward.
 *   - If it resolves with `ok: false`, we still forward (manual fallback on the
 *     metrics page).
 *   - A hard TIMEOUT guard forwards regardless if the call hangs.
 *   - A manual "Skip" lets the user bail out immediately.
 *
 * All navigation is driven from the effect / awaited promise — never as a
 * render side-effect. A `forwarded` ref guarantees we route exactly once.
 */

// Hard ceiling: never trap the user on the loader, even if analyze-website
// hangs past its own server timeout.
const ANALYZE_TIMEOUT_MS = 12_000

export function Analyzing() {
  const { workspace, setWebsiteAnalysis, loading } = useOnboarding()
  const router = useRouter()
  const forwardedRef = useRef(false)
  const [showSkip, setShowSkip] = useState(false)

  const website = workspace?.product?.website ?? null

  function forward() {
    if (forwardedRef.current) return
    forwardedRef.current = true
    router.replace("/onboarding/metrics")
  }

  // No workspace to anchor the flow → bounce back to the first step (in an
  // effect, so navigation never fires during render).
  useEffect(() => {
    if (!loading && !workspace) {
      router.replace("/onboarding/business-info")
    }
  }, [loading, workspace, router])

  // Surface the manual "Skip" affordance after a short delay so the loader
  // never feels stuck.
  useEffect(() => {
    const t = setTimeout(() => setShowSkip(true), 3_000)
    return () => clearTimeout(t)
  }, [])

  // Run the BLOCKING analysis, then forward. Resilient to failure, ok:false,
  // and hangs (timeout). Runs once per mount with a live workspace.
  useEffect(() => {
    if (loading || !workspace) return
    let cancelled = false

    // No website → nothing to analyze; go straight to manual entry.
    if (!website) {
      forward()
      return
    }

    const timeout = setTimeout(() => {
      if (!cancelled) forward()
    }, ANALYZE_TIMEOUT_MS)

    onboardingApi
      .analyzeWebsite(website)
      .then((res) => {
        if (cancelled) return
        // Stash even when ok:false — downstream reads it and falls back to
        // manual entry gracefully.
        setWebsiteAnalysis(res)
      })
      .catch(() => {
        /* transport failure → leave analysis null; metrics page handles it */
      })
      .finally(() => {
        if (cancelled) return
        clearTimeout(timeout)
        forward()
      })

    return () => {
      cancelled = true
      clearTimeout(timeout)
    }
    // Intentionally keyed only on workspace presence + website so the analysis
    // fires exactly once when the workspace is ready.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, workspace?.id, website])

  return (
    <div className="onb-shell">
      <div className="onb-head">
        <span className="onb-brand">
          sprntly<span className="dot">.</span>
        </span>
        <span className="save">
          <span className="pulse" />
          Saved
        </span>
      </div>

      <div className="onb-card">
        <div className="onb-analyzing">
          <div className="onb-spinner" role="status" aria-label="Analyzing" />
          <h1 className="onb-analyzing-h">
            Gathering information about your business
          </h1>
          <p className="onb-analyzing-sub">
            We&apos;re reading your website to draft your industry, business
            type, and a set of success metrics. This only takes a moment.
          </p>
          {showSkip && (
            <button
              type="button"
              className="btn btn-ghost onb-analyzing-skip"
              onClick={forward}
            >
              Skip and continue
            </button>
          )}
        </div>
      </div>

      <div className="onb-foot-meta">Progress auto-saves after every step.</div>
    </div>
  )
}
