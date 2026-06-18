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
 * `.da-activity-agent-label` styling: agent turns label "Design Agent · {ago}",
 * user turns "{userName ?? 'You'} · {ago}". The author is derived from `kind`
 * ("user" → the user, everything else → the Design Agent). The relative time is
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
  const author = kind === "user" ? (userName?.trim() || "You") : "Design Agent"
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

  if (activity.length === 0) return null
  return (
    <div
      className="da-activity"
      data-testid="da-activity"
      role="log"
      aria-live="polite"
      aria-label="Design Agent activity"
    >
      {activity.map((e) => {
        const label = turnLabel(e.kind, e.createdAt, userName, now)
        switch (e.kind) {
          case "user":
            return (
              <div
                key={e.id}
                className="proto-msg proto-msg--user"
                data-testid="da-activity-user"
              >
                <p className="da-activity-agent-label">{label}</p>
                <p className="proto-msg-body">{stripAgentContext(e.text)}</p>
              </div>
            )
          case "step":
            return (
              <div
                key={e.id}
                className={`da-activity-step${e.state === "done" ? " is-done" : " is-active"}`}
                data-testid="da-activity-step"
                data-state={e.state}
              >
                <span className="da-activity-step-icon" aria-hidden="true">
                  {e.state === "done" ? (
                    "✓"
                  ) : (
                    <span className="da-activity-spinner" />
                  )}
                </span>
                <span className="da-activity-step-text">{e.text}</span>
              </div>
            )
          case "question":
            return (
              <div
                key={e.id}
                className="proto-msg proto-msg--agent da-activity-question"
                data-testid="da-activity-question"
              >
                <p className="da-activity-agent-label">{label}</p>
                <p className="proto-msg-body">{e.question}</p>
              </div>
            )
          case "done":
            return (
              <div
                key={e.id}
                className="proto-msg proto-msg--agent"
                data-testid="da-activity-done"
              >
                <p className="da-activity-agent-label">{label}</p>
                <p className="proto-msg-body da-activity-done-body">
                  <span className="da-activity-done-icon" aria-hidden="true">✓</span>
                  <span>{e.text}</span>
                </p>
              </div>
            )
          case "skipped":
            return (
              <div
                key={e.id}
                className="proto-msg proto-msg--agent"
                data-testid="da-activity-skipped"
              >
                <p className="da-activity-agent-label">{label}</p>
                <p className="proto-msg-body da-activity-skipped-body">
                  {e.text}
                </p>
              </div>
            )
          case "error":
            return (
              <div
                key={e.id}
                className="da-activity-error error"
                data-testid="da-activity-error"
                role="alert"
              >
                {e.text}
              </div>
            )
          default:
            return null
        }
      })}
      {running && (
        <p className="da-activity-running" data-testid="da-activity-running">
          Working…
        </p>
      )}
    </div>
  )
}
