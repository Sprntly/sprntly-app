"use client"

/**
 * Renders the uniform disclosure channel (`GoalRunPlan["notes"]`) — see
 * `GoalDisclosureNote` in `lib/api.ts` for what the shape means and why it
 * exists.
 *
 * THE ONE RULE THIS FILE EXISTS TO ENFORCE: every note in the array renders,
 * including a `kind` this file has never heard of. There is deliberately no
 * switch, no allowlist and no early return keyed on `kind` anywhere below —
 * that is what closes the class of defect the channel was built for (a
 * backend disclosure that reaches the API and reaches no reader). A future
 * change that adds a `case` on `kind` before deciding whether to render
 * `text` would reopen exactly that failure, one kind at a time.
 *
 * `text` is rendered verbatim. The backend owns the wording; nothing here
 * reconstructs or reformats a sentence from structured fields.
 */
import * as React from "react"

import type { GoalDisclosureNote } from "../../lib/api"

export function DisclosureNotes({
  notes,
  className,
  testIdPrefix = "goal-disclosure-note",
}: {
  notes: GoalDisclosureNote[] | undefined
  /** The caller's own register — a plan gate and a report panel are styled
   *  differently, and this component has no opinion of its own about which
   *  is right. Passed straight to each rendered `<p>`. */
  className?: string
  testIdPrefix?: string
}) {
  if (!notes?.length) return null
  return (
    <>
      {notes.map((note, i) => (
        <p
          key={`${note.kind}-${i}`}
          className={className}
          data-testid={`${testIdPrefix}-${note.kind}`}
        >
          {note.text}
        </p>
      ))}
    </>
  )
}
