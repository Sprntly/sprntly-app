"use client"

/**
 * Left-panel agent-conversation thread. Renders the modular `ActivityEvent[]`
 * from `useIterateRun` in the `.proto-msg` chat style:
 *   - user request  → a right-aligned user message bubble,
 *   - working steps → an "agent working" card with an animated status + a
 *                     streamed-steps checklist (active spinner / done check),
 *   - question      → the agent's clarifying question (the answer surface itself
 *                     renders separately, inline just below — wired by the host),
 *   - done          → a completion turn carrying the agent's change summary,
 *   - error         → an error line.
 *
 * EVERY turn shows an author + a relative timestamp via the shared
 * `.da-activity-agent-label` styling: agent turns label "Sprntly · {ago}",
 * user turns "{userName ?? 'You'} · {ago}". The author is derived from `kind`
 * ("user" → the user, everything else → "Sprntly"). The relative time is
 * computed at render from each turn's client-captured `createdAt` (ms) and
 * refreshed by a light 30s ticker so "2m ago" stays current.
 *
 * LIVE-ONLY: the thread holds no persistence. `createdAt` is captured in-app at
 * append time and never reloaded; a refresh starts the thread empty. The thread
 * is presentation only — it is NOT fed back into the model as conversational
 * context (each iterate stays a discrete, bounded change request).
 *
 * The author/timestamp label is rendered INSIDE the existing `aria-live="polite"`
 * log region; the 30s ticker only re-derives the relative-time strings (no new
 * turns), so it does not spam the live region with content changes.
 */

import { useEffect, useState } from "react"
import { shortRelativeTime } from "./CommentsPanel"
import { IconSparkle } from "../shared/app-icons"
import type { ActivityEvent } from "./useIterateRun"

function stripAgentContext(text: string): string {
  return text.split('\n').filter(l => !l.startsWith('[ref:')).join('\n').trim()
}

/** Compose the author + relative-time label for a turn. `now` is injected so the
 *  pure formatting stays deterministic in tests. Pure → SSR/unit-testable. */
export function turnLabel(
  kind: ActivityEvent["kind"],
  createdAt: number | undefined,
  userName: string | null | undefined,
  now: number,
): string {
  const author = kind === "user" ? (userName?.trim() || "You") : "Sprntly"
  // Guard a missing/invalid timestamp (older callers, malformed events): omit the
  // relative-time suffix rather than throwing on `new Date(undefined)`.
  const rel =
    typeof createdAt === "number" && Number.isFinite(createdAt)
      ? shortRelativeTime(new Date(createdAt).toISOString(), now)
      : ""
  return rel ? `${author} · ${rel}` : author
}

export function IterateActivityStream({
  activity,
  running,
  userName = null,
}: {
  activity: ActivityEvent[]
  running: boolean
  /** The signed-in user's display name for user-turn labels. Falls back to
   *  "You" when null. Sourced upstream from `content.userName`. */
  userName?: string | null
}) {
  // Light ticker: re-render every 30s so the relative timestamps ("2m ago")
  // stay current without persisting anything. `now` is local component state.
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 30_000)
    return () => clearInterval(id)
  }, [])

  // Empty thread → a quiet empty-state instead of a blank region, so the panel
  // always reads as the Sprntly conversation even before the first change.
  if (activity.length === 0) {
    return (
      <div className="da-activity-empty" data-testid="da-activity-empty">
        <IconSparkle size={18} />
        <p className="da-activity-empty-text">
          No changes yet — describe one below to iterate.
        </p>
      </div>
    )
  }

  // ─── Derive the "Focus" view from the array in a single backward scan ───
  // The thread is no longer a row-per-event wall. We compute, from the same
  // ActivityEvent[], exactly three derived values and render ONE status region:
  //   • the user request bubble (first `user` event, at most one per run),
  //   • the terminal event (last done/skipped/error) — wins over everything,
  //   • the latest `step` text for the single live line, and whether a
  //     `question` is pending (a question with no terminal after it).
  const userEvent = activity.find((e) => e.kind === "user")
  let terminal: ActivityEvent | null = null
  let latestStep: Extract<ActivityEvent, { kind: "step" }> | null = null
  let pendingQuestion = false
  for (let i = activity.length - 1; i >= 0; i--) {
    const e = activity[i]
    if (!terminal && (e.kind === "done" || e.kind === "skipped" || e.kind === "error")) {
      terminal = e
    }
    if (!latestStep && e.kind === "step") {
      latestStep = e
    }
  }
  // A question is "pending" only when no terminal event followed it.
  if (!terminal && activity.some((e) => e.kind === "question")) {
    pendingQuestion = true
  }

  return (
    <div
      className="da-activity"
      data-testid="da-activity"
      role="log"
      aria-live="polite"
      aria-label="Sprntly activity"
    >
      {userEvent && (
        <div
          className="proto-msg proto-msg--user"
          data-testid="da-activity-user"
        >
          <p className="da-activity-agent-label">
            {turnLabel(userEvent.kind, userEvent.createdAt, userName, now)}
          </p>
          <p className="proto-msg-body">{stripAgentContext(userEvent.text)}</p>
        </div>
      )}

      {/* Exactly ONE status region below the user bubble. Precedence:
          terminal chip → frozen "waiting" line → single live line. */}
      {terminal ? (
        terminal.kind === "done" ? (
          <div
            className="da-activity-terminal da-activity-terminal--done proto-msg proto-msg--agent"
            data-testid="da-activity-done"
          >
            <p className="da-activity-agent-label">
              {turnLabel(terminal.kind, terminal.createdAt, userName, now)}
            </p>
            <p className="proto-msg-body da-activity-done-body">
              <span className="da-activity-done-icon" aria-hidden="true">✓</span>
              <span>{terminal.text}</span>
            </p>
          </div>
        ) : terminal.kind === "skipped" ? (
          <div
            className="da-activity-terminal da-activity-terminal--skipped"
            data-testid="da-activity-skipped"
          >
            <span className="da-activity-terminal-icon" aria-hidden="true">
              <svg
                width="11"
                height="11"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="3"
                aria-hidden="true"
              >
                <path d="M6 6l12 12M18 6L6 18" />
              </svg>
            </span>
            <span className="da-activity-terminal-text">{terminal.text}</span>
          </div>
        ) : (
          <div
            className="da-activity-terminal da-activity-terminal--error da-activity-error error"
            data-testid="da-activity-error"
            role="alert"
          >
            {terminal.text}
          </div>
        )
      ) : pendingQuestion ? (
        <p className="da-activity-waiting" data-testid="da-activity-waiting">
          Waiting for your answer…
        </p>
      ) : (
        <div className="da-activity-live" data-testid="da-activity-live">
          <span className="da-activity-spinner" />
          <span className="da-activity-live-text">
            <span className="da-activity-shim">
              {latestStep ? latestStep.text : "Working…"}
            </span>
          </span>
        </div>
      )}
    </div>
  )
}
