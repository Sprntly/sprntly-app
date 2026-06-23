"use client"

import { useState } from "react"
import { feedbackApi, type FeedbackType } from "../../lib/api"
import { IconClose } from "./app-icons"

const TYPE_OPTIONS: { value: FeedbackType; label: string }[] = [
  { value: "feature_request", label: "Feature request" },
  { value: "connector_request", label: "New connector request" },
  { value: "bug", label: "Bug" },
  { value: "other", label: "General feedback" },
]

/** Lightweight feedback / feature-request form (June 20 #13 + #A).
 *
 * Opened from the left nav (next to sign-out). Free-text message + an optional
 * type; submit POSTs to /v1/feedback (stored + emailed to the team). Self-
 * contained: parent owns only the open/close boolean. */
export function FeedbackModal({
  open,
  onClose,
}: {
  open: boolean
  onClose: () => void
}) {
  const [type, setType] = useState<FeedbackType>("feature_request")
  const [message, setMessage] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)

  if (!open) return null

  const reset = () => {
    setType("feature_request")
    setMessage("")
    setError(null)
    setDone(false)
    setSubmitting(false)
  }

  const close = () => {
    reset()
    onClose()
  }

  const submit = async () => {
    const trimmed = message.trim()
    if (!trimmed) {
      setError("Please enter a message.")
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      await feedbackApi.submit({ message: trimmed, type })
      setDone(true)
    } catch {
      setError("Couldn't send feedback. Please try again.")
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div
      className="modal-overlay open"
      role="dialog"
      aria-modal="true"
      aria-label="Send feedback"
      onClick={(e) => e.target === e.currentTarget && close()}
    >
      <div className="modal feedback-modal">
        <div className="modal-head">
          <button
            type="button"
            className="modal-close"
            onClick={close}
            aria-label="Close"
          >
            <IconClose size={16} />
          </button>
          <div className="modal-badge">Feedback</div>
          <h2 className="modal-title">Send feedback</h2>
          <p className="modal-sub">
            Request a new connector, suggest a feature, or report a bug. It goes
            straight to the team.
          </p>
        </div>

        {done ? (
          <div style={{ padding: "0 26px 24px" }}>
            <p style={{ color: "var(--ink-2)" }}>
              Thanks — your feedback was sent to the team.
            </p>
            <div className="modal-foot" style={{ padding: "16px 0 0" }}>
              <button className="btn btn-accent" onClick={close}>
                Done
              </button>
            </div>
          </div>
        ) : (
          <>
            <div style={{ padding: "0 26px 20px" }}>
              <label className="field-label" htmlFor="feedback-type">
                Type
              </label>
              <select
                id="feedback-type"
                className="ticket-select"
                style={{ width: "100%" }}
                value={type}
                onChange={(e) => setType(e.target.value as FeedbackType)}
              >
                {TYPE_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>

              <div style={{ marginTop: 16 }}>
                <label className="field-label" htmlFor="feedback-message">
                  Message
                </label>
                <textarea
                  id="feedback-message"
                  className="textarea"
                  placeholder="Tell us what you'd like to see, or what's not working…"
                  style={{ minHeight: 110 }}
                  value={message}
                  onChange={(e) => setMessage(e.target.value)}
                />
              </div>

              {error && (
                <p
                  role="alert"
                  style={{ color: "var(--danger, #c0392b)", fontSize: 13, marginTop: 10 }}
                >
                  {error}
                </p>
              )}
            </div>
            <div className="modal-foot">
              <button className="btn btn-ghost" onClick={close} disabled={submitting}>
                Cancel
              </button>
              <button className="btn btn-accent" onClick={() => void submit()} disabled={submitting}>
                {submitting ? "Sending…" : "Send feedback"}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
