"use client"

/**
 * The finished Goal Analysis, as a DOCUMENT.
 * (Engine name Crucible; that word never appears on screen.)
 *
 * WHAT THIS COMPONENT IS NOW: the chrome around a report, not a renderer of
 * one. The title, the two document actions and the consequence note live
 * here; the report itself is a self-contained HTML document produced by
 * `backend/app/crucible/report.render_report_document` and displayed in a
 * sandboxed iframe by `HtmlReportView` — the same path the PRD, the evidence
 * brief, the VoC report and chat's report replies already take.
 *
 * WHY IT STOPPED BEING A RENDERER. It used to rebuild the entire document in
 * React from `run.findings`, in parallel with the Python that renders the
 * exported one. Two renderers of one report, sharing no code, each carrying
 * its own copy of every rule about how a finding is written out — which is
 * exactly the shape of bug it kept producing:
 *
 *   * the decision box (owner, needed-by, what is at stake) existed in the
 *     exported document and had never existed here at all;
 *   * the grounded-money line ("customers named $X across N accounts") was
 *     likewise document-only, and could not be added here because the panel's
 *     payload did not carry the field;
 *   * the kill-signal caveat, the option headers, the unsized-coverage
 *     branching and the overflow wording all had to be kept in step by hand,
 *     with a comment on each saying so;
 *   * the caps were declared twice, in Python and in TypeScript, and had to
 *     be changed twice.
 *
 * None of that is a class of bug you fix. It is a class of bug you delete the
 * conditions for, and the condition was the second renderer. The mirrored
 * TypeScript modules that existed only to serve it (`goalMoscow`,
 * `goalDataGaps`, `goalTopics`, `goalProse`, `goalFindingsHeading`) went with
 * it.
 *
 * WHY AN IFRAME IS THE RIGHT ENVELOPE. `HtmlReportView` renders `srcDoc` with
 * `sandbox="allow-same-origin"` and WITHOUT `allow-scripts`, so the
 * document's stylesheet applies and nothing in it can execute or reach the
 * app around it. That is what lets the report carry a real stylesheet
 * (`backend/app/crucible/assets/goal-analysis.css`) instead of being
 * hand-styled here — and it is why these documents look designed and this
 * panel did not.
 *
 * `fitPanel` is set because this is the ~700px side panel: `HtmlReportView`
 * trims the sheet's own gutter and frame so the document does not sit as a
 * bordered page inside the panel's already-bordered card.
 *
 * THE RULES THIS COMPONENT USED TO OWN NOW LIVE IN ONE PLACE — `report.py`.
 * An unsized finding rendering as "could not be sized" and never as 0 (I3),
 * source documents beside the claim they support, coverage notes qualifying
 * what they qualify, and the closing section built from the run plan's own
 * gaps are all still true of what is shown here, because what is shown here
 * is what that file produced.
 */
import { HtmlReportView } from "./HtmlReportView"
import type { GoalRunDetail } from "../../lib/api"

export function GoalAnalysisReport({
  run,
  editable = false,
  onEdit,
  onSaveCopy,
  busy = false,
}: {
  run: GoalRunDetail
  /** Show the document actions. DEFAULT FALSE, so every existing caller
   *  renders exactly what it rendered before. */
  editable?: boolean
  /** Turn this report into an editable document and open it. The endpoint
   *  behind it is idempotent, so a double press cannot fork a second copy. */
  onEdit?: () => void
  /** Save a SEPARATE copy as an ordinary team document, leaving this report
   *  alone. The fork half of "edit in place AND fork on demand". */
  onSaveCopy?: () => void
  /** A document action is in flight. Both buttons disable together: they write
   *  to the same run, and letting the second fire while the first is still
   *  going is how you get a copy of a report that is mid-creation. */
  busy?: boolean
}) {
  const html = (run.report_html || "").trim()

  return (
    <article className="ga-doc" data-testid="goal-report">
      <header className="ga-doc-header">
        <p className="ga-doc-eyebrow">Goal analysis</p>
        <h1 className="ga-doc-title">{run.goal_text}</h1>
        {editable ? (
          <div className="ga-doc-actions" data-testid="goal-report-actions">
            <button
              type="button"
              className="ga-doc-action"
              data-testid="goal-report-edit"
              disabled={busy}
              onClick={onEdit}
            >
              Edit
            </button>
            <button
              type="button"
              className="ga-doc-action"
              data-testid="goal-report-save-copy"
              disabled={busy}
              onClick={onSaveCopy}
            >
              Save as document
            </button>
            {/* SAID BEFORE THE CLICK, not after it. Editing is not a mode you
                can back out of — it detaches the report from the run for good
                — and a reader who did not know that would be told only once it
                had happened. */}
            <p className="ga-doc-actions-note">
              Editing keeps the analysis exactly as it is and turns this report
              into a document you own. It stops updating from the run.
            </p>
          </div>
        ) : null}
      </header>

      {html ? (
        <HtmlReportView html={html} title="Goal analysis" fitPanel />
      ) : (
        // STATED, NOT BLANK. A run whose report has not been rendered yet —
        // one still generating, or one read by a client older than the field
        // — gets a sentence rather than an empty panel with two buttons under
        // it, which would read as "the analysis found nothing".
        <p className="ga-doc-note" data-testid="goal-report-pending">
          This analysis has no rendered report yet. It appears here as soon as
          the run finishes.
        </p>
      )}
    </article>
  )
}

export default GoalAnalysisReport
