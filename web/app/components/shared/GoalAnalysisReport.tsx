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
 *
 * ONE THING THE DOCUMENT CANNOT DO: ACT. `HtmlReportView` renders it with no
 * `allow-scripts`, so a cut option printed inside it is prose forever, however
 * clearly `report.py` states the reason it was cut. This component adds the
 * one thing the sandbox rules out — a way to REOPEN a cut option — as plain
 * React, outside the frame: a button per `run.considered` entry that hands the
 * label to the chat composer as a question, so "was this actually weighed?"
 * is answerable on the spot, against the same corpus, at near-zero cost. It
 * does not restate the reason each was cut — that sentence already exists,
 * once, in the document above — only the label, which is what identifies the
 * option to ask about.
 *
 * A SECOND ADDITIVE LIST, BELOW THAT ONE: "where did this come from", for a
 * finding or a cut option's own first claim id. `report.py` already prints
 * this same lookup's answer AS TEXT inside the document above — baked in at
 * render time, so it survives into the editable copy and the forked/printed
 * one, which carry no script and (once forked) no run to ask again. This
 * list is the one place a reader can ask LIVE, because it is the one place
 * that still has both: a script-capable React tree and a run id to query.
 * `HtmlReportView`'s iframe forbids exactly this (`allow-scripts` is never
 * set, on purpose — see that file), which is why the click lives out here
 * and not inside the document.
 */
import { useCallback, useState } from "react"
import { DisclosureNotes } from "./DisclosureNotes"
import { HtmlReportView } from "./HtmlReportView"
import { goalAnalysisApi, type ClaimSource, type GoalRunDetail } from "../../lib/api"

//: THE SAME FOUR READINGS `backend/app/crucible/report.py`'s baked-in
//: identifier prints, kept in step BY HAND — the same discipline that
//: file's own `KILL_SIGNAL_CAVEAT`/`ACCOUNT_NAMING_DISCLOSURE` comments
//: describe for prose shared between the two renderers. A live check here
//: and the printed document are answering the same lookup about the same
//: claim, so they say the same thing about it.
function claimSourceText(source: ClaimSource): string {
  if (source.status === "resolved" && source.pointer) {
    if (source.pointer.kind === "call") {
      return source.pointer.call_date
        ? `${source.pointer.title}, ${source.pointer.call_date}`
        : source.pointer.title
    }
    return source.pointer.label
  }
  if (source.status === "no_pointer") {
    return (
      "a real claim with nothing a person could open — no linked call and "
      + "no named document"
    )
  }
  if (source.status === "dropped_for_space") {
    return (
      "cited here, but this run did not keep enough detail to look it up "
      + "again"
    )
  }
  // "not_found", or any future status this client does not yet know —
  // the same honest fallback the backend itself uses for both.
  return "no longer available"
}

/** One row: a claim id, checkable on demand. Closed until clicked — a panel
 *  that fired every lookup on mount would cost a database round trip per
 *  finding and per cut option on every open, for an answer most readers
 *  never ask for. */
function ClaimSourceCheck({
  runId, claimId, label,
}: {
  runId: number
  claimId: string
  label: string
}) {
  const [result, setResult] = useState<"idle" | "loading" | "error" | ClaimSource>("idle")
  const check = useCallback(async () => {
    setResult("loading")
    try {
      const source = await goalAnalysisApi.claimSource(runId, claimId)
      setResult(source)
    } catch {
      setResult("error")
    }
  }, [runId, claimId])
  return (
    <li>
      <button
        type="button"
        className="ga-doc-action"
        data-testid="goal-source-check"
        disabled={result === "loading"}
        onClick={check}
      >
        Where &ldquo;{label}&rdquo; came from
      </button>
      {result !== "idle" ? (
        <span className="ga-source-result" data-testid="goal-source-result">
          {result === "loading"
            ? "Checking…"
            : result === "error"
              ? "Could not check this right now."
              : claimSourceText(result)}
        </span>
      ) : null}
    </li>
  )
}

export function GoalAnalysisReport({
  run,
  editable = false,
  onEdit,
  onSaveCopy,
  busy = false,
  onSelectOption,
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
  /** Reopen a cut option: hands its label to the caller, which hands it to the
   *  chat composer as a question. Undefined renders no list at all — a caller
   *  with nowhere to send the label (there is none today; every caller sits
   *  beside a composer) gets the exact prose-only panel this file replaced
   *  nothing else about. */
  onSelectOption?: (label: string) => void
}) {
  const html = (run.report_html || "").trim()

  // WHAT CAN BE CHECKED LIVE: a finding or a cut option's own FIRST claim id
  // — the same one `report.py` already resolved once, at render time, into
  // the text sitting in the document above. Only rows that actually cite a
  // claim are offered; a finding or a rejection stored before `claim_ids`
  // existed has none, and there is nothing here for a click to ask about.
  const sourceRows: { key: string; claimId: string; label: string }[] = [
    ...run.findings
      .filter((f) => f.claim_ids && f.claim_ids.length > 0)
      .map((f) => ({
        key: `finding-${f.id}`,
        claimId: f.claim_ids[0],
        label: f.label || f.statement,
      })),
    ...run.considered
      .filter((c) => c.claim_ids && c.claim_ids.length > 0)
      .map((c) => ({
        key: `considered-${c.id}`,
        claimId: c.claim_ids[0],
        label: c.label,
      })),
  ]

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

      {/* THE DISCLOSURE CHANNEL, OUTSIDE THE SANDBOXED DOCUMENT. `notes`
          rides on the run's own PLAN, which — per its own field comment —
          survives into `ready` as the record of what was read; this is the
          same array the plan gate showed before the run started, read back
          here rather than recomputed. See `DisclosureNotes` for why every
          note renders regardless of `kind`. */}
      <DisclosureNotes
        notes={run.prioritisation?.plan?.notes}
        className="ga-doc-note"
        testIdPrefix="goal-report-note"
      />

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

      {/* THE REOPENABLE LIST. `run.considered` is already on the wire — see
          the type's own doc in `lib/api.ts` — and until now nothing rendered
          it here at all, since it is fully covered by the document above.
          This is additive to that coverage, not a replacement for it: closed
          by default (a reader who does not want to litigate cut options never
          sees this open), and silent when there is nowhere to send a
          selection or nothing was cut. */}
      {onSelectOption && run.considered.length > 0 ? (
        <details className="ga-considered" data-testid="goal-considered">
          <summary>
            {run.considered.length === 1
              ? "1 option considered and cut"
              : `${run.considered.length} options considered and cut`}
          </summary>
          <ul>
            {run.considered.map((option) => (
              <li key={option.id}>
                <button
                  type="button"
                  className="ga-doc-action"
                  data-testid="goal-considered-option"
                  onClick={() => onSelectOption(option.label)}
                >
                  Ask about &ldquo;{option.label}&rdquo;
                </button>
              </li>
            ))}
          </ul>
        </details>
      ) : null}

      {/* THE LIVE CHECK. Additive to the identifier `report.py` already
          baked into the document above — see this file's own header for why
          a live lookup can only ever live out here, in React, never inside
          the sandboxed report or the editable/forked copy. Closed by
          default, and silent when nothing on this run carries a claim id at
          all. */}
      {sourceRows.length > 0 ? (
        <details className="ga-sources" data-testid="goal-sources">
          <summary>
            {sourceRows.length === 1
              ? "1 source to check"
              : `${sourceRows.length} sources to check`}
          </summary>
          <ul>
            {sourceRows.map((row) => (
              <ClaimSourceCheck
                key={row.key}
                runId={run.id}
                claimId={row.claimId}
                label={row.label}
              />
            ))}
          </ul>
        </details>
      ) : null}
    </article>
  )
}

export default GoalAnalysisReport
