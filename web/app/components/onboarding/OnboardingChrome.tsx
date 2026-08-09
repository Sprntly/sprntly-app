"use client"

import type { ReactNode } from "react"
import { ONBOARDING_STEP_COUNT } from "../../lib/onboarding/types"
import { ArrowLeft, ArrowRight } from "../auth/icons"

/**
 * Shared chrome for the v4-styled onboarding scenes (Company / Metrics).
 *
 * Renders the `.onb-shell` → `.onb-head` (brand + progress dots + autosave
 * pill) → `.onb-card` (the step body) → `.onb-footer` (meta + Back/Continue).
 *
 * The progress dots are driven by `step` over the fixed `ONBOARDING_STEP_COUNT`
 * NUMBERED steps. The analyzing interstitial deliberately does NOT use this
 * chrome (it renders its own `.onb-analyzing` body with no dots), so it never
 * appears as a counted step — that is how the loader is excluded from the dot
 * count.
 */
export function OnboardingChrome({
  step,
  title,
  subtitle,
  footerMeta,
  children,
  onBack,
  onSkip,
  skipLabel = "Skip",
  onContinue,
  continueLabel = "Continue",
  continueDisabled,
  loading,
  saveLabel = "Saved",
  wideCard = false,
}: {
  /** 1-based numbered step; controls the active dot. */
  step: number
  title: ReactNode
  subtitle?: ReactNode
  footerMeta?: ReactNode
  children: ReactNode
  onBack?: () => void
  /**
   * Optional Skip button, rendered between Back and Continue. Used by steps
   * that advance something in place (the connector accordion) rather than
   * leaving the step, where a footer-meta text link reads too weakly.
   */
  onSkip?: () => void
  skipLabel?: string
  onContinue?: () => void
  continueLabel?: string
  continueDisabled?: boolean
  loading?: boolean
  saveLabel?: string
  /** Widen the card (design's `.onb-card-wide`) for the narrative steps. */
  wideCard?: boolean
}) {
  const dots = Array.from({ length: ONBOARDING_STEP_COUNT }, (_, i) => i + 1)
  return (
    <div className="onb-shell">
      <div className="onb-head">
        <span className="onb-brand">
          sprntly<span className="dot">.</span>
        </span>
        <div className="onb-dots" data-step={step}>
          {dots.map((d) => (
            <span
              key={d}
              className={`od ${d < step ? "done" : ""} ${d === step ? "cur" : ""}`}
            />
          ))}
        </div>
        <span className="save">
          <span className="pulse" />
          {saveLabel}
        </span>
      </div>

      <div className={`onb-card${wideCard ? " onb-card-wide" : ""}`}>
        <div className="onb-h">{title}</div>
        {subtitle && <div className="onb-sub">{subtitle}</div>}
        {children}
      </div>

      <div className="onb-footer">
        <div className="meta">{footerMeta}</div>
        {onBack && (
          <button
            type="button"
            className="btn btn-ghost"
            onClick={onBack}
            disabled={loading}
          >
            <ArrowLeft style={{ width: 13, height: 13 }} aria-hidden /> Back
          </button>
        )}
        {onSkip && (
          <button
            type="button"
            className="btn btn-secondary"
            onClick={onSkip}
            disabled={loading}
          >
            {skipLabel}
          </button>
        )}
        {onContinue && (
          <button
            type="button"
            className="btn btn-brand"
            onClick={onContinue}
            disabled={continueDisabled || loading}
          >
            {loading ? "Saving…" : continueLabel}
            {!loading && <ArrowRight style={{ width: 13, height: 13 }} aria-hidden />}
          </button>
        )}
      </div>

      <div className="onb-foot-meta">Progress auto-saves after every step.</div>
    </div>
  )
}
