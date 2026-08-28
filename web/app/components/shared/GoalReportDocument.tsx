"use client"

/**
 * A Goal Analysis report, open as an editable document.
 *
 * WHAT THIS FILE IS AND IS NOT. It is a BANNER and a frame. The editing — the
 * rich-text surface, the debounced autosave, the version compare-and-set, the
 * "someone else saved this" resolution — is `DocumentTab`, unchanged, because
 * a Goal Analysis report IS a `custom_artifacts` row and the whole argument for
 * storing it as one was that it inherits that machinery instead of growing a
 * second copy. `DocumentTab`'s own header says it: the same editor renders the
 * panel and the page "so they cannot drift". A third implementation here would
 * be the drift.
 *
 * THE BANNER IS THE PRODUCT CLAIM, NOT DECORATION. A run is reproducible —
 * every finding traces to claim ids and source documents, and re-running the
 * same corpus gives the same ranking. An edited report is not that any more.
 * If the two were indistinguishable on screen, then every report would carry
 * the reproducibility claim and only some of them would deserve it, which is
 * worse than not offering editing at all. So a detached report says so, above
 * the text, with the way back to the run it came from.
 *
 * ONLY WHEN DETACHED. An untouched report opened for editing shows no banner,
 * because nothing has happened yet: it is still byte-for-byte what the run
 * rendered, and telling someone their report has diverged before they have
 * typed anything trains them to ignore the notice that matters.
 */
import { lazy, Suspense } from "react"
import type { GoalReportDoc } from "../../lib/api"

// LAZY, for the reason ContentPanel gives about this exact component: it
// mounts the rich-text editor, which is a large chunk. A read-only report must
// not pay for it, and most runs are only ever read.
const DocumentTab = lazy(() =>
  import("./DocumentTab").then((m) => ({ default: m.DocumentTab })),
)

export function GoalReportDocument({
  doc,
  onBack,
  onSaveCopy,
  busy = false,
}: {
  doc: GoalReportDoc
  /** Back to the run's own read-only report — the findings, the ledger, the
   *  coverage notes, exactly as the analysis produced them. */
  onBack?: () => void
  /** Save a separate copy as an ordinary team document. Offered here as well
   *  as on the read-only report: an edited report is precisely the thing
   *  someone wants to branch from. */
  onSaveCopy?: () => void
  busy?: boolean
}) {
  return (
    <div className="ga-doc-editing" data-testid="goal-report-document">
      {doc.detached ? (
        <div
          className="ga-doc-detached"
          role="status"
          data-testid="goal-report-detached"
        >
          <p className="ga-doc-detached-lede">
            <b>Edited</b> — this report is no longer regenerated from the run.
            The analysis behind it is unchanged: its findings, what it ruled out
            and what it could not tell you are all still there.
          </p>
          <div className="ga-doc-detached-actions">
            {onBack ? (
              <button
                type="button"
                className="ga-doc-action"
                data-testid="goal-report-back"
                onClick={onBack}
              >
                See the original analysis
              </button>
            ) : null}
            {onSaveCopy ? (
              <button
                type="button"
                className="ga-doc-action"
                data-testid="goal-report-save-copy"
                disabled={busy}
                onClick={onSaveCopy}
              >
                Save as document
              </button>
            ) : null}
          </div>
        </div>
      ) : (
        <div className="ga-doc-attached" data-testid="goal-report-attached">
          <p className="ga-doc-detached-lede">
            You are editing this report. The analysis it came from stays as it
            is — the moment you change a word here, this document stops being
            regenerated from it.
          </p>
          <div className="ga-doc-detached-actions">
            {onBack ? (
              <button
                type="button"
                className="ga-doc-action"
                data-testid="goal-report-back"
                onClick={onBack}
              >
                See the original analysis
              </button>
            ) : null}
          </div>
        </div>
      )}
      <Suspense
        fallback={<div className="ga-loading">Loading the editor…</div>}
      >
        {/* Keyed on the document id for the reason ContentPanel keys it: the
            editor holds the buffer it was mounted for, and remounting is the
            only way to point it at another document. */}
        {/* NO `onQuote`. `DocumentTab` only renders its "Ask in chat" button
            when a handler is supplied, and the chat cannot yet answer a
            question about a document at all — nothing on the ask path reads a
            custom artifact's body. Offering the button would hand a passage to
            a composer whose answer could not be grounded in it. */}
        <DocumentTab key={doc.id} documentId={doc.id} />
      </Suspense>
    </div>
  )
}

export default GoalReportDocument
