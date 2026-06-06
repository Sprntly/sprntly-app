"use client"

/**
 * P7 — comment-clarify dialog.
 *
 * Intercepts the Apply flow on the signed-in CommentsPanel mount. When the
 * user clicks Apply on an open comment, this dialog fires a lightweight
 * `designAgentApi.clarifyComment` call (Haiku, <1s), shows the returned
 * clarifying question, and lets the user optionally add context before
 * confirming. The confirmed payload is an enriched prompt string:
 *
 *   "<original comment body>\n\nClarification: <user answer>"
 *
 * — or the bare comment body when the user adds nothing.
 *
 * The dialog resolves the comment itself (via the `onConfirm` → handleResolve
 * path in CommentsPanel) so callers never see the raw comment after Apply.
 *
 * CSS lives in design-agent.css (`.clarify-comment-quote`, `.clarify-question`,
 * `.clarify-loading`, `.clarify-textarea`). Modal chrome reuses the existing
 * `.modal-overlay`, `.modal-box`, `.modal-head`, `.modal-body`, `.modal-foot`,
 * `.modal-cancel`, `.modal-confirm` classes from design-agent.css.
 */

import { useEffect, useState } from "react"
import { designAgentApi, type CommentRecord } from "../../lib/api"

interface Props {
  open: boolean
  comment: CommentRecord | null
  prototypeId: number
  onConfirm: (enrichedPrompt: string) => void
  onCancel: () => void
}

export function CommentClarifyDialog({ open, comment, prototypeId, onConfirm, onCancel }: Props) {
  const [question, setQuestion] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [clarification, setClarification] = useState("")

  useEffect(() => {
    if (!open || !comment) {
      setQuestion(null)
      setClarification("")
      return
    }
    setLoading(true)
    designAgentApi
      .clarifyComment(prototypeId, comment.body)
      .then((r) => setQuestion(r.question))
      .catch(() => setQuestion("Looks good — any additional context to add?"))
      .finally(() => setLoading(false))
  }, [open, comment?.id, prototypeId])

  if (!open || !comment) return null

  const handleConfirm = () => {
    const enriched = clarification.trim()
      ? `${comment.body}\n\nClarification: ${clarification}`
      : comment.body
    onConfirm(enriched)
  }

  return (
    <div className="modal-overlay open" role="dialog" aria-modal="true" aria-label="Confirm change">
      <div className="modal-box">
        <div className="modal-head">
          <div className="modal-title">Confirm change</div>
        </div>
        <div className="modal-body">
          <blockquote className="clarify-comment-quote">{comment.body}</blockquote>
          <div className="clarify-question">
            {loading ? (
              <span className="clarify-loading">Analyzing…</span>
            ) : (
              <span>{question}</span>
            )}
          </div>
          <textarea
            className="clarify-textarea"
            placeholder="Add clarification or answer the question above…"
            value={clarification}
            onChange={(e) => setClarification(e.target.value)}
            rows={3}
          />
        </div>
        <div className="modal-foot">
          <button className="modal-cancel" onClick={onCancel}>
            Cancel
          </button>
          <button className="modal-confirm" onClick={handleConfirm} disabled={loading}>
            Apply change
          </button>
        </div>
      </div>
    </div>
  )
}
