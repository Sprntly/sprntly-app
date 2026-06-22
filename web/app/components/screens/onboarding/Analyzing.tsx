"use client"

import { useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { useOnboarding } from "../../../context/OnboardingContext"
import {
  getPendingAnalysis,
  resumeWebsiteAnalysis,
  runWebsiteAnalysis,
} from "../../../lib/onboarding/runWebsiteAnalysis"

/**
 * Onboarding interstitial — "Gathering information about your business".
 *
 * This is a TRANSIENT, UNNUMBERED route (`/onboarding/analyzing`): it is not in
 * ONBOARDING_SCREENS, it is not back-navigable, and it is excluded from the
 * progress-dot count (it renders no dots). It sits between the business-info
 * page (step 1) and the metrics page (`/onboarding/metrics`).
 *
 * BLUR/REMOUNT-SAFE (mirrors the chat Ask flow):
 *   - POST /v1/onboarding/analyze-website is fire-and-forget — it returns a
 *     job_id and the analysis keeps running SERVER-SIDE. Backgrounding the tab
 *     no longer stalls or restarts the work.
 *   - We poll the status endpoint via the shared visibility-aware `pollUntil`,
 *     so a backgrounded-then-refocused tab catches up immediately instead of
 *     waiting on a throttled timer.
 *   - The active job_id is persisted per workspace (jobResume), so a remount
 *     RE-ATTACHES to the running job instead of re-POSTing a duplicate run.
 *
 * RESILIENCE — onboarding must always complete:
 *   - On `ready` → stash the analysis, forward to metrics.
 *   - On `error` / transport failure / wall-clock budget exhaustion → still
 *     forward (manual fallback on the metrics page). The poll's Date.now budget
 *     (90s) is the "don't trap forever" backstop — it does NOT abandon a still-
 *     running analysis just because the tab was briefly backgrounded.
 *   - A manual "Skip" lets the user bail out immediately.
 *
 * All navigation is driven from the effect — never as a render side-effect. A
 * `forwarded` ref guarantees we route exactly once.
 */

export function Analyzing() {
  const { workspace, setWebsiteAnalysis, loading } = useOnboarding()
  const router = useRouter()
  const forwardedRef = useRef(false)
  const [showSkip, setShowSkip] = useState(false)

  const website = workspace?.product?.website ?? null
  const workspaceId = workspace?.id ?? null

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

  // Kick off (or re-attach to) the server-side analysis, then forward. The work
  // runs server-side; this effect only POSTs/resumes and polls. Runs once per
  // mount with a live workspace.
  useEffect(() => {
    if (loading || !workspace || !workspaceId) return
    let cancelled = false
    const isCancelled = () => cancelled

    // No website → nothing to analyze; go straight to manual entry.
    if (!website) {
      forward()
      return
    }

    // The workspace id keys both the persistence scope and the localStorage
    // company segment, so a remount re-attaches unambiguously.
    const company = workspaceId

    // Remount re-attach: if a job is already in flight for this workspace,
    // resume polling it instead of POSTing a duplicate analysis.
    const pending = getPendingAnalysis(company, workspaceId)
    const run = pending
      ? resumeWebsiteAnalysis(Number(pending.id), company, workspaceId, isCancelled)
      : runWebsiteAnalysis(website, company, workspaceId, isCancelled)

    run
      .then(({ result }) => {
        if (cancelled) return
        // Stash even when ok:false — downstream reads it and falls back to
        // manual entry gracefully. null (error/timeout) leaves it untouched.
        if (result) setWebsiteAnalysis(result)
      })
      .finally(() => {
        if (cancelled) return
        forward()
      })

    return () => {
      cancelled = true
    }
    // Intentionally keyed only on workspace presence + website so the analysis
    // fires exactly once when the workspace is ready.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, workspaceId, website])

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
