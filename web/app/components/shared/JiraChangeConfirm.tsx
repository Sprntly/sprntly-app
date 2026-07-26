"use client"

import { useState } from "react"
import { jiraApi, type JiraWriteResult, type PendingJiraChange } from "../../lib/api"
import { useNavigation } from "../../context/NavigationContext"

/**
 * The confirmation gate for a Jira change the agent proposed in chat.
 *
 * The agent can only ever PROPOSE — `jira_propose_change` validates a change and
 * describes it, and no backend path lets a model apply one. This card is the
 * other half: applying takes a person clicking Confirm, which is the single
 * point where a sentence becomes a real edit to a ticket the whole team sees.
 *
 * That split exists because chat commands are guesses about intent. "Move it to
 * done" can land on the wrong issue, a date can be misread, a field can be one
 * the user never meant — and Jira has no undo. Showing the exact before → after
 * and waiting is cheap; explaining an unwanted write to someone's team is not.
 */
export function JiraChangeConfirm({ change }: { change: PendingJiraChange }) {
  const { showToast } = useNavigation()
  const [busy, setBusy] = useState(false)
  // null = undecided. Once decided the card becomes a record of what happened,
  // so the thread still reads correctly when scrolled back to later.
  const [result, setResult] = useState<JiraWriteResult | null>(null)
  const [dismissed, setDismissed] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const lines = change.preview?.length
    ? change.preview
    : // Defensive: a proposal should always carry rendered lines, but never show
      // a confirm button with nothing above it saying what it will do.
      [
        ...Object.entries(change.fields || {}).map(([k, v]) => `${k} → ${String(v)}`),
        ...(change.to_status ? [`Status → ${change.to_status}`] : []),
        ...(change.comment ? [`Comment: ${change.comment}`] : []),
      ]

  async function confirm() {
    setBusy(true)
    setError(null)
    try {
      const res = await jiraApi.applyChange({
        issue_key: change.issue_key,
        ...(Object.keys(change.fields || {}).length ? { fields: change.fields } : {}),
        ...(change.to_status ? { to_status: change.to_status } : {}),
        ...(change.comment ? { comment: change.comment } : {}),
      })
      setResult(res)
      // Partial success is reported as such: a request can set fields, move
      // status and comment, and any part can fail on its own.
      if (res.ok) {
        showToast("Jira updated", `${change.issue_key} — ${res.applied.join(", ")}`)
      } else {
        showToast("Jira partly updated", `Failed: ${res.failed.join(", ")}`)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't reach Jira.")
    } finally {
      setBusy(false)
    }
  }

  if (dismissed) {
    return (
      <div className="jira-confirm jira-confirm--done">
        Change to {change.issue_key} discarded — nothing was written.
      </div>
    )
  }

  return (
    <div className="jira-confirm" data-testid="jira-change-confirm">
      <div className="jira-confirm-head">
        <span className="jira-confirm-kind">JIRA</span>
        <span className="jira-confirm-key">{change.issue_key}</span>
        {change.summary ? (
          <span className="jira-confirm-summary">{change.summary}</span>
        ) : null}
      </div>
      <ul className="jira-confirm-lines">
        {lines.map((l, i) => (
          <li key={i}>{l}</li>
        ))}
      </ul>

      {result ? (
        <div className="jira-confirm-result" data-testid="jira-change-result">
          {result.ok
            ? `Applied to ${result.issue_key}: ${result.applied.join(", ")}.`
            : `Partly applied. Worked: ${result.applied.join(", ") || "nothing"}. ` +
              `Failed: ${result.failed.join(", ")}.`}
          {result.fields?.error ? ` ${result.fields.error}` : ""}
          {result.status?.error ? ` ${result.status.error}` : ""}
          {result.comment?.error ? ` ${result.comment.error}` : ""}
        </div>
      ) : (
        <>
          {error ? <div className="jira-confirm-error">{error}</div> : null}
          <div className="jira-confirm-actions">
            <button
              type="button"
              className="btn btn-primary btn-sm"
              disabled={busy}
              onClick={confirm}
            >
              {busy ? "Applying…" : "Confirm change"}
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
          <div className="jira-confirm-note">
            Nothing is written to Jira until you confirm.
          </div>
        </>
      )}
    </div>
  )
}
