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
 *
 * ONLY THE PLAN GATE USES THIS. `GoalAnalysisReport` (the finished report
 * panel) deliberately does NOT — it renders `run.report_html` verbatim
 * through a sandboxed iframe (`backend/app/crucible/report.py` produces the
 * whole document, byte for byte), so any backend-composed sentence, this
 * channel's or not, already reaches that panel the moment `report.py` writes
 * it. It never had the drift problem this component exists to fix. The plan
 * gate is different: it renders ~44 individually named TYPED fields off the
 * plan object (`GoalRunPlan` in `lib/api.ts`), so a new disclosure there
 * stays invisible until a component is taught its field name — which is
 * exactly what happened to `source_scope_note` before this channel existed.
 * Adding a second `DisclosureNotes` call to the report panel would render
 * every note TWICE for a reader in `report_html`'s remit — once inside the
 * iframe, once outside it — for no gain, since nothing there was ever
 * missing. Don't reintroduce that.
 */
import * as React from "react"

import type { GoalDisclosureNote } from "../../lib/api"

export function DisclosureNotes({
  notes,
  className,
  testIdPrefix = "goal-disclosure-note",
}: {
  notes: GoalDisclosureNote[] | undefined
  /** The plan gate's own register, passed straight to each rendered `<p>`.
   *  This component has no styling opinion of its own. */
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
