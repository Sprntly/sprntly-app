"use client"

/**
 * Left-panel agent-flow activity transcript. Renders the modular `ActivityEvent[]`
 * from `useIterateRun` in the `.proto-msg` chat style:
 *   - user request  → a right-aligned user message bubble,
 *   - working steps → an "agent working" card with an animated status + a
 *                     streamed-steps checklist (active spinner / done check),
 *   - question      → the agent's clarifying question (the answer surface itself
 *                     renders separately, inline just below — wired by the host),
 *   - done          → a completion line,
 *   - error         → an error line.
 *
 * Pure presentational (no hooks/I/O) → SSR-renderable. The activity is DRIVEN by
 * the poll in useIterateRun: the intermediate working steps are cosmetic (no real
 * backend step stream yet), but the terminal "done" line is emitted only on the
 * poll's real completion — this component holds no timer of its own and renders
 * "done" purely from the event it is handed. A real backend SSE/step stream would
 * feed the same event list via appendActivity.
 */

import type { ActivityEvent } from "./useIterateRun"

export function IterateActivityStream({
  activity,
  running,
}: {
  activity: ActivityEvent[]
  running: boolean
}) {
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
        switch (e.kind) {
          case "user":
            return (
              <div
                key={e.id}
                className="proto-msg proto-msg--user"
                data-testid="da-activity-user"
              >
                <p className="proto-msg-body">{e.text}</p>
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
                <p className="da-activity-agent-label">Design Agent asks</p>
                <p className="proto-msg-body">{e.question}</p>
              </div>
            )
          case "done":
            return (
              <div
                key={e.id}
                className="da-activity-done"
                data-testid="da-activity-done"
              >
                <span className="da-activity-done-icon" aria-hidden="true">✓</span>
                <span>{e.text}</span>
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
