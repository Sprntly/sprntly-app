"use client"

import { useState } from "react"
import {
  jiraApi,
  ticketDataApi,
  type PendingTicketChange,
} from "../../lib/api"
import { useNavigation } from "../../context/NavigationContext"

/**
 * The confirmation gate for a PRD-grounded ticket rewrite the agent proposed.
 *
 * Sibling of JiraChangeConfirm, and the gate matters more here. That card sets a
 * field; this one REPLACES a description — so a rewrite aimed at the wrong
 * ticket, or one that quietly drops a paragraph someone wrote by hand, destroys
 * work rather than mislabelling it. Which is why the card shows the proposed
 * text in full rather than only summarizing it: the user is agreeing to specific
 * words, so they should be able to read the specific words.
 *
 * The backend resolves WHICH surface owns the ticket (it looked the ticket up);
 * this component only routes the write to the endpoint that already owns that
 * surface — PUT /v1/tickets/{key}/description for a Sprntly ticket, or the
 * existing POST /v1/jira/write for a Jira issue.
 */
export function TicketChangeConfirm({ change }: { change: PendingTicketChange }) {
  const { showToast } = useNavigation()
  const [busy, setBusy] = useState(false)
  // null = undecided. Once decided the card becomes a record of what happened,
  // so the thread still reads correctly when scrolled back to later.
  const [applied, setApplied] = useState(false)
  const [dismissed, setDismissed] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const isJira = change.target === "jira"
  const lines = change.preview?.length
    ? change.preview
    : // Defensive: a proposal should always carry rendered lines, but never show
      // a confirm button with nothing above it saying what it will do.
      ["Description: replaced with the text below"]

  async function confirm() {
    setBusy(true)
    setError(null)
    try {
      if (isJira) {
        const res = await jiraApi.applyChange({
          issue_key: change.ticket_key,
          fields: { description: change.description },
        })
        if (!res.ok) {
          setError(res.fields?.error || "Jira rejected the description update.")
          return
        }
      } else {
        // The endpoint REPLACES the criteria with whatever it is sent, so the
        // backend always resolves `acceptance_criteria` to the exact end state
        // for a Sprntly target — the ticket's current list when the agent left
        // them alone. `?? []` is only the impossible-null guard.
        await ticketDataApi.saveDescription(
          change.ticket_key,
          change.description,
          change.acceptance_criteria ?? [],
        )
      }
      setApplied(true)
      showToast(
        isJira ? "Jira updated" : "Ticket updated",
        `${change.ticket_key} — description rewritten from the PRD`,
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't apply the update.")
    } finally {
      setBusy(false)
    }
  }

  if (dismissed) {
    return (
      <div className="ticket-confirm ticket-confirm--done">
        Update to {change.ticket_key} discarded — nothing was written.
      </div>
    )
  }

  return (
    <div className="ticket-confirm" data-testid="ticket-change-confirm">
      <div className="ticket-confirm-head">
        <span className="ticket-confirm-kind">{isJira ? "JIRA" : "TICKET"}</span>
        <span className="ticket-confirm-key">{change.ticket_key}</span>
        {change.title ? (
          <span className="ticket-confirm-summary">{change.title}</span>
        ) : null}
      </div>
      <ul className="ticket-confirm-lines">
        {lines.map((l, i) => (
          <li key={i}>{l}</li>
        ))}
      </ul>

      <div className="ticket-confirm-body" data-testid="ticket-change-description">
        {change.description}
        {change.acceptance_criteria?.length ? (
          <>
            <div className="ticket-confirm-criteria-head">Acceptance criteria</div>
            <ul className="ticket-confirm-criteria">
              {change.acceptance_criteria.map((c, i) => (
                <li key={i}>{c}</li>
              ))}
            </ul>
          </>
        ) : null}
      </div>

      {applied ? (
        <div className="ticket-confirm-result" data-testid="ticket-change-result">
          Applied to {change.ticket_key}.
        </div>
      ) : (
        <>
          {error ? <div className="ticket-confirm-error">{error}</div> : null}
          <div className="ticket-confirm-actions">
            <button
              type="button"
              className="btn btn-primary btn-sm"
              disabled={busy}
              onClick={confirm}
            >
              {busy ? "Applying…" : "Confirm update"}
            </button>
            <button
              type="button"
              className="btn btn-sm"
              disabled={busy}
              onClick={() => setDismissed(true)}
            >
              Cancel
            </button>
          </div>
          <div className="ticket-confirm-note">
            This replaces the ticket&rsquo;s current description. Nothing is
            written until you confirm.
          </div>
        </>
      )}
    </div>
  )
}
