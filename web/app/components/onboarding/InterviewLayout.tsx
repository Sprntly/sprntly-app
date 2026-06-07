"use client"

import { useCallback, useRef, useState, type ReactNode } from "react"
import { ONBOARDING_STEP_COUNT } from "../../lib/onboarding/types"
import {
  validateRequired,
  type FieldCheck,
  type ValidationResult,
} from "../../lib/onboarding/validation"

type InterviewLayoutProps = {
  step: number
  eyebrow: string
  title: string
  agentMessage: string
  children: ReactNode
  rightPane: ReactNode
  onBack?: () => void
  onContinue?: () => void
  onSkip?: () => void
  continueLabel?: string
  continueDisabled?: boolean
  loading?: boolean
  skipLabel?: string
}

/**
 * Required-field validation for InterviewLayout-based steps.
 *
 * The step declares its checks (via `getChecks`) and tags each field's
 * wrapper with `data-field="<key>"`. Calling `validate()` on Continue
 * surfaces per-field messages (read by the step from `errors`) and
 * focuses the first invalid field; the step blocks navigation when it
 * returns `ok: false`. State lives here so a step needs only one hook.
 */
export function useFieldValidation(getChecks: () => FieldCheck[]) {
  const [errors, setErrors] = useState<Record<string, string>>({})
  const containerRef = useRef<HTMLDivElement | null>(null)
  const getChecksRef = useRef(getChecks)
  getChecksRef.current = getChecks

  const focusField = useCallback((key: string) => {
    const root = containerRef.current
    if (!root) return
    const el = root.querySelector<HTMLElement>(
      `[data-field="${key}"] input, [data-field="${key}"] textarea, [data-field="${key}"] select`,
    )
    el?.focus()
  }, [])

  /** Runs validation; updates errors and focuses the first invalid field. */
  const validate = useCallback((): ValidationResult => {
    const result = validateRequired(getChecksRef.current())
    setErrors(result.errors)
    if (result.firstInvalid) focusField(result.firstInvalid)
    return result
  }, [focusField])

  const clearError = useCallback((key: string) => {
    setErrors((prev) => {
      if (!(key in prev)) return prev
      const next = { ...prev }
      delete next[key]
      return next
    })
  }, [])

  return { errors, validate, clearError, containerRef }
}

export function InterviewLayout({
  step,
  eyebrow,
  title,
  agentMessage,
  children,
  rightPane,
  onBack,
  onContinue,
  onSkip,
  continueLabel = "Continue",
  continueDisabled,
  loading,
  skipLabel = "Skip for now",
}: InterviewLayoutProps) {
  return (
    <div className="interview-shell">
      <header className="interview-header">
        <div className="interview-logo">
          spr<span>ntly</span>
        </div>
        <div className="interview-progress">
          {Array.from({ length: ONBOARDING_STEP_COUNT }, (_, i) => i + 1).map((s) => (
            <div
              key={s}
              className={`interview-dot ${s < step ? "done" : ""} ${s === step ? "active" : ""}`}
            />
          ))}
        </div>
        <div className="interview-step-label">
          Step {step} of {ONBOARDING_STEP_COUNT}
        </div>
      </header>

      <div className="interview-body">
        <section className="interview-center">
          <div className="interview-agent">
            <div className="interview-agent-badge">Onboarding Agent</div>
            <p className="interview-agent-msg">{agentMessage}</p>
          </div>
          <div className="interview-form-wrap">
            <div className="interview-eyebrow">{eyebrow}</div>
            <h1 className="interview-title">{title}</h1>
            {children}
            <div className="interview-actions">
              {onBack && (
                <button type="button" className="btn" onClick={onBack} disabled={loading}>
                  Back
                </button>
              )}
              {onSkip && (
                <button type="button" className="btn btn-ghost" onClick={onSkip} disabled={loading}>
                  {skipLabel}
                </button>
              )}
              {onContinue && (
                <button
                  type="button"
                  className="btn btn-primary"
                  style={{ flex: 1 }}
                  onClick={onContinue}
                  disabled={continueDisabled || loading}
                >
                  {loading ? "Saving…" : continueLabel}
                </button>
              )}
            </div>
          </div>
        </section>
        <aside className="interview-right">{rightPane}</aside>
      </div>

      <style jsx>{`
        .interview-shell {
          min-height: 100vh;
          display: flex;
          flex-direction: column;
          background: var(--surface);
        }
        .interview-header {
          display: flex;
          align-items: center;
          gap: 24px;
          padding: 16px 28px;
          border-bottom: 1px solid var(--line);
        }
        .interview-logo {
          font-family: var(--font-display);
          font-weight: 600;
          font-size: 18px;
          letter-spacing: -0.02em;
        }
        .interview-logo span {
          color: var(--accent);
        }
        .interview-progress {
          display: flex;
          gap: 5px;
          flex: 1;
          justify-content: center;
        }
        .interview-dot {
          width: 28px;
          height: 3px;
          border-radius: 2px;
          background: var(--line);
        }
        .interview-dot.done {
          background: var(--accent);
        }
        .interview-dot.active {
          background: var(--ink);
        }
        .interview-step-label {
          font-size: 11px;
          color: var(--muted);
          text-transform: uppercase;
          letter-spacing: 0.1em;
          white-space: nowrap;
        }
        .interview-body {
          flex: 1;
          display: grid;
          grid-template-columns: 1fr 360px;
          min-height: 0;
        }
        .interview-center {
          padding: 32px 40px 40px;
          overflow-y: auto;
          max-width: 720px;
        }
        .interview-agent {
          background: var(--surface-2);
          border: 1px solid var(--line);
          border-radius: 12px;
          padding: 16px 18px;
          margin-bottom: 28px;
        }
        .interview-agent-badge {
          font-size: 10px;
          text-transform: uppercase;
          letter-spacing: 0.12em;
          color: var(--accent);
          font-weight: 600;
          margin-bottom: 8px;
        }
        .interview-agent-msg {
          margin: 0;
          font-size: 14px;
          line-height: 1.55;
          color: var(--ink-2);
        }
        .interview-eyebrow {
          font-size: 10.5px;
          text-transform: uppercase;
          letter-spacing: 0.14em;
          color: var(--muted);
          margin-bottom: 8px;
          font-weight: 600;
        }
        .interview-title {
          font-family: var(--font-display);
          font-weight: 600;
          font-size: 28px;
          line-height: 1.12;
          letter-spacing: -0.025em;
          margin: 0 0 20px;
        }
        .interview-actions {
          display: flex;
          gap: 8px;
          margin-top: 24px;
          align-items: center;
        }
        .interview-right {
          border-left: 1px solid var(--line);
          background: var(--surface-2);
          padding: 28px 24px;
          overflow-y: auto;
        }
        @media (max-width: 960px) {
          .interview-body {
            grid-template-columns: 1fr;
          }
          .interview-right {
            border-left: none;
            border-top: 1px solid var(--line);
          }
        }
      `}</style>
    </div>
  )
}
