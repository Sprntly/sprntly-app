"use client"

import { useEffect, useState, type ReactNode } from "react"

// ── The shared "an artifact is being generated" surfaces ─────────────────────
// Every artifact in the pipeline (Evidence → PRD → Tickets) is produced by a
// 30-90s LLM job, and each one used to announce that with a ~20px spinner and
// one muted 12-13px line. At that weight an in-flight run reads as a stalled
// panel, so all three now share two surfaces:
//
//   • GeneratingPane   — nothing to show yet. A full working state: pulsing
//     icon, the artifact named in a serif heading, a phase line rotating
//     through the run's real legs, an indeterminate bar, and skeletons SHAPED
//     like the thing being built, so the wait reads as a document/list filling
//     in rather than an empty tab.
//   • GeneratingBanner — content is already on screen and more is coming
//     (a partial set streaming in, or an existing artifact being replaced).
//     Loud enough to read at a glance from the top of the panel.
//
// Both are palette-agnostic: they consume `--gw-*` tokens, which globals.css
// maps to the app palette by default and to the ticket palette inside `.tkv2`.

/** How long each phase line stays up. */
const PHASE_MS = 7000

export type GeneratingPaneProps = {
  /** Small glyph inside the pulsing ring. */
  icon: ReactNode
  /** The heading. Pass JSX to italicize the artifact's name. */
  title: ReactNode
  /** Indicative legs of the run, rotated on a slow timer. They must describe
   *  work the job REALLY does, in the order it does it — nothing reports which
   *  leg is live, so this is honest pacing, not a measured progress claim. */
  phases: readonly string[]
  /** Reassurance under the bar, before the run has gone long. */
  note: string
  /** Swapped in once every phase has been shown — a run past its usual length
   *  should say so rather than silently repeat itself. */
  slowNote: string
  /** Skeleton shape: prose document (PRD, evidence brief) or list rows. */
  skeleton?: "doc" | "rows"
  testId?: string
}

export function GeneratingPane({
  icon, title, phases, note, slowNote, skeleton = "doc", testId,
}: GeneratingPaneProps) {
  const [tick, setTick] = useState(0)
  useEffect(() => {
    const t = setInterval(() => setTick((n) => n + 1), PHASE_MS)
    return () => clearInterval(t)
  }, [])
  const atEnd = tick >= phases.length - 1
  const phase = phases[Math.min(tick, phases.length - 1)]

  return (
    <div className="gwip" data-testid={testId} aria-busy="true">
      <div className="gwip-head">
        <div className="gwip-headrow">
          <span className="gwip-orb" aria-hidden>{icon}</span>
          <div className="gwip-headtext">
            <div className="gwip-title">{title}</div>
            <div className="gwip-phase" role="status" aria-live="polite">
              {phase}
              <span className="gwip-dot" aria-hidden />
            </div>
          </div>
        </div>
        <div className="gwip-bar" aria-hidden><span className="gwip-bar-pill" /></div>
        <div className="gwip-note">{atEnd ? slowNote : note}</div>
      </div>

      {/* Shaped like the artifact, not a generic block. */}
      <div className="gwip-skels" aria-hidden>
        {skeleton === "rows"
          ? [0, 1, 2].map((i) => (
              <div className="gwip-skel gwip-skel--row" key={i}>
                <span className="gwip-skel-key" />
                <div className="gwip-skel-lines">
                  <span className="gwip-skel-line" />
                  <span className="gwip-skel-line" />
                  <span className="gwip-skel-line" />
                </div>
              </div>
            ))
          : [0, 1].map((i) => (
              <div className="gwip-skel gwip-skel--doc" key={i}>
                <div className="gwip-skel-lines">
                  <span className="gwip-skel-line gwip-skel-line--head" />
                  <span className="gwip-skel-line" />
                  <span className="gwip-skel-line" />
                  <span className="gwip-skel-line" />
                </div>
              </div>
            ))}
      </div>
    </div>
  )
}

export type GeneratingBannerProps = {
  /** `accent` = adding to what's there. `warn` = REPLACING it, which is the
   *  state a user most needs to notice before acting on the old content. */
  tone?: "accent" | "warn"
  title: string
  sub: string
  /** Batch progress, when the job reports it. */
  progress?: { done: number; total: number } | null
  testId?: string
}

export function GeneratingBanner({
  tone = "accent", title, sub, progress, testId,
}: GeneratingBannerProps) {
  const prog = progress && progress.total > 0 ? progress : null
  const pct = prog ? Math.round((Math.min(prog.done, prog.total) / prog.total) * 100) : null
  return (
    <div
      className={`gwip-banner${tone === "warn" ? " gwip-banner--warn" : ""}`}
      data-testid={testId}
      role="status"
      aria-live="polite"
    >
      <span className="gwip-banner-spin" aria-hidden />
      <div className="gwip-banner-main">
        <div className="gwip-banner-title">{title}</div>
        <div className="gwip-banner-sub">{sub}</div>
        {/* Determinate only once batches are reported — before that an
            indeterminate sweep, so the bar never parks at a fake 0%. */}
        <div className="gwip-bar gwip-bar--slim" aria-hidden>
          {pct == null
            ? <span className="gwip-bar-pill" />
            : <span className="gwip-bar-fill" style={{ width: `${Math.max(6, pct)}%` }} />}
        </div>
      </div>
      {prog ? <span className="gwip-banner-count">{prog.done}/{prog.total}</span> : null}
    </div>
  )
}
