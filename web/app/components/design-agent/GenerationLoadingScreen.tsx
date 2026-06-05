"use client"

/*
 * UX-EXPLORE (throwaway — REVERT): Full-screen "Building your prototype" loading
 * screen, shown WHILE a prototype generates and dismissed when generation
 * resolves. Faithfully reproduces David's mockup `.proto-generating` block
 * (12-prototype.html lines ~1589–1604 + sprntly.css) — animated orb, blinking
 * cursor headline, status line, progress bar with elapsed-time label, and a
 * streamed steps checklist (spinner → check, one-by-one).
 *
 * ADAPTED for THIS app:
 *  - Colors/fonts use the app's design tokens (forest-green --accent, off-white
 *    surfaces, --font-serif / --font-mono). NO coral, NO new palette.
 *  - Step copy is generic + plausible for this app's generation (no demo lines
 *    like care-web / "86 components from Figma" / "Instrument Serif, coral").
 *  - Full-screen FIXED overlay (inset:0, z-index above the app chrome incl. the
 *    sidebar) so it takes over the whole viewport — see .proto-gen-overlay in
 *    design-agent.css.
 *
 * IMPORTANT — the step reveal + progress fill are COSMETIC. The real backend
 * (runDesignAgentGeneration) does NOT emit per-step events; it only resolves
 * ready/failed/timeout. So the steps animate on a local timer purely to give the
 * wait some texture. Actual dismissal is driven by the parent (`open` flips
 * false once generation resolves AND the min-visible duration elapsed).
 */

import { useEffect, useRef, useState } from "react"

// UX-EXPLORE (throwaway — REVERT): generic, plausible steps for this app's
// generation (NOT David's demo-specific care-web/Figma/coral lines).
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

export function GenerationLoadingScreen({
  open,
  onDone,
}: {
  open: boolean
  /** Optional: fired once the exit fade completes (cosmetic hook). */
  onDone?: () => void
}) {
  // Number of steps marked done (0..STEPS.length). The "active" step is `done`.
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

    // Progressive step reveal — advance one step per STEP_MS, but never mark the
    // LAST step done while we're still waiting (so the final spinner keeps
    // spinning until the parent dismisses on real completion).
    const stepTimer = window.setInterval(() => {
      setDoneCount((c) => (c < STEPS.length - 1 ? c + 1 : c))
    }, STEP_MS)

    return () => {
      window.clearInterval(tick)
      window.clearInterval(stepTimer)
    }
  }, [open])

  // Fire onDone after the overlay is removed.
  const prevOpen = useRef(open)
  useEffect(() => {
    if (prevOpen.current && !open) onDone?.()
    prevOpen.current = open
  }, [open, onDone])

  if (!open) return null

  const elapsedS = elapsedMs / 1000
  // Cap the cosmetic fill at 96% until real completion dismisses the screen.
  const pct = Math.min(96, Math.round((elapsedMs / ESTIMATE_MS) * 100))
  const activeIndex = Math.min(doneCount, STEPS.length - 1)
  const status = STATUSES[activeIndex] ?? STATUSES[STATUSES.length - 1]

  return (
    <div
      className="proto-gen-overlay design-agent-surface"
      role="status"
      aria-live="polite"
      aria-label="Building your prototype"
    >
      <div className="proto-gen-inner">
        <div className="proto-gen-orb" aria-hidden="true">
          <div className="r1" />
          <div className="r2" />
          <div className="r3" />
          <div className="d" />
        </div>
        <div className="proto-gen-h">
          Building your prototype
          <span className="thinking-cursor" aria-hidden="true" />
        </div>
        <div className="proto-gen-s">{status}</div>
        <div className="proto-gen-progress">
          <div className="bar">
            <div className="fill" style={{ width: `${pct}%` }} />
          </div>
          <div className="lbl">
            {elapsedS.toFixed(1)}s · est. ~{Math.round(ESTIMATE_MS / 1000)}s
          </div>
        </div>
        <div className="proto-gen-steps">
          {STEPS.map((label, i) => {
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
