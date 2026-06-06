"use client"

/*
 * Full-screen "Building your prototype" loading screen, shown while a prototype
 * generates and dismissed when generation resolves. Reproduces the product
 * mockup's generating block — animated forest-green orb (app `--accent` token,
 * no coral), blinking-cursor headline, status line, progress bar with elapsed
 * time, and a steps checklist. The step reveal + progress fill are COSMETIC: the
 * generation backend does not emit per-step events today, so the steps animate
 * on a local timer purely to give the wait some texture. Actual dismissal is
 * driven by the parent (`open` flips false once generation resolves AND the
 * min-visible duration has elapsed). Live per-step events are a future
 * enhancement.
 */

import { useEffect, useRef, useState } from "react"

// Generic, plausible step labels for this app's generation. These are cosmetic
// placeholders revealed on a local timer — the backend emits no per-step events
// yet — not live progress.
const STEPS = [
  "Reading the PRD",
  "Analyzing the design source",
  "Planning the layout",
  "Composing components",
  "Wiring interactions",
  "Accessibility pass",
  "Rendering preview",
]

// Matching ellipsised status lines for the italic status row (one per step).
const STATUSES = [
  "Reading the PRD acceptance criteria…",
  "Analyzing the connected design source…",
  "Planning the layout + screen flow…",
  "Composing components from the design system…",
  "Wiring up interactions + state…",
  "Accessibility pass · keyboard + screen reader…",
  "Rendering the preview frame…",
]

// Cosmetic timings (ms). The bar fills over ESTIMATE_MS; steps advance on a
// per-step cadence. These do NOT gate real completion — `open` does.
const ESTIMATE_MS = 30000
const STEP_MS = 3200

// Refresh-mode constants — shorter, simpler steps for the canvas load path.
const REFRESH_STEPS = [
  "Fetching your prototype…",
  "Restoring your design…",
  "Preparing the canvas…",
  "Almost there…",
]
const REFRESH_STATUSES = [
  "Fetching your prototype…",
  "Restoring your design…",
  "Preparing the canvas…",
  "Almost there…",
]
const REFRESH_ESTIMATE_MS = 5000
const REFRESH_STEP_MS = 800

export function GenerationLoadingScreen({
  open,
  onDone,
  mode = "generate",
}: {
  open: boolean
  /** Optional: fired once the exit fade completes (cosmetic hook). */
  onDone?: () => void
  /** "generate" (default) = full generation flow; "refresh" = canvas reload. */
  mode?: "generate" | "refresh"
}) {
  // Derive active arrays and timings from mode.
  const steps = mode === "refresh" ? REFRESH_STEPS : STEPS
  const statuses = mode === "refresh" ? REFRESH_STATUSES : STATUSES
  const estimateMs = mode === "refresh" ? REFRESH_ESTIMATE_MS : ESTIMATE_MS
  const stepMs = mode === "refresh" ? REFRESH_STEP_MS : STEP_MS

  // Number of steps marked done (0..steps.length). The "active" step is `done`.
  const [doneCount, setDoneCount] = useState(0)
  const [elapsedMs, setElapsedMs] = useState(0)
  const startedAtRef = useRef<number>(0)

  useEffect(() => {
    if (!open) {
      // Reset for the next run.
      setDoneCount(0)
      setElapsedMs(0)
      return
    }

    startedAtRef.current = Date.now()
    setDoneCount(0)
    setElapsedMs(0)

    // Progress / elapsed ticker (100ms cadence like the mockup).
    const tick = window.setInterval(() => {
      setElapsedMs(Date.now() - startedAtRef.current)
    }, 100)

    // Progressive step reveal — advance one step per stepMs, but never mark the
    // LAST step done while we're still waiting (so the final spinner keeps
    // spinning until the parent dismisses on real completion).
    const stepTimer = window.setInterval(() => {
      setDoneCount((c) => (c < steps.length - 1 ? c + 1 : c))
    }, stepMs)

    return () => {
      window.clearInterval(tick)
      window.clearInterval(stepTimer)
    }
  }, [open, steps.length, stepMs])

  // Fire onDone after the overlay is removed.
  const prevOpen = useRef(open)
  useEffect(() => {
    if (prevOpen.current && !open) onDone?.()
    prevOpen.current = open
  }, [open, onDone])

  if (!open) return null

  const elapsedS = elapsedMs / 1000
  // Cap the cosmetic fill at 96% until real completion dismisses the screen.
  const pct = Math.min(96, Math.round((elapsedMs / estimateMs) * 100))
  const activeIndex = Math.min(doneCount, steps.length - 1)
  const status = statuses[activeIndex] ?? statuses[statuses.length - 1]

  const headline =
    mode === "refresh" ? "Loading your prototype" : "Building your prototype"

  return (
    <div
      className="proto-gen-overlay design-agent-surface"
      role="status"
      aria-live="polite"
      aria-label={headline}
    >
      <div className="proto-gen-inner">
        <div className="proto-gen-orb" aria-hidden="true">
          <div className="r1" />
          <div className="r2" />
          <div className="r3" />
          <div className="d" />
        </div>
        <div className="proto-gen-h">
          {headline}
          <span className="thinking-cursor" aria-hidden="true" />
        </div>
        <div className="proto-gen-s">{status}</div>
        <div className="proto-gen-progress">
          <div className="bar">
            <div className="fill" style={{ width: `${pct}%` }} />
          </div>
          {mode !== "refresh" && (
            <div className="lbl">
              {elapsedS.toFixed(1)}s · est. ~{Math.round(estimateMs / 1000)}s
            </div>
          )}
        </div>
        <div className="proto-gen-steps">
          {steps.map((label, i) => {
            const isDone = i < doneCount
            const isActive = i === activeIndex && !isDone ? true : i === doneCount
            const cls =
              "proto-gen-step" +
              (isDone ? " done" : isActive ? " active" : "")
            return (
              <div key={label} className={cls}>
                {!isDone && <span className="spin" aria-hidden="true" />}
                {label}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
