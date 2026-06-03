/**
 * Modal for API-key-based connectors (commit J).
 *
 * Fireflies (and any future provider whose primary auth is a user-issued
 * API key) opens this on Connect instead of redirecting through OAuth.
 *
 * Pure View — props in, JSX out — tested via renderToStaticMarkup per
 * the project's component-test convention (no jsdom). The default-
 * exported ApiKeyPromptModal wraps the view with the local state and
 * submit callback.
 */
"use client"

import { useState, type ReactNode } from "react"

export type ApiKeyPromptModalViewProps = {
  open: boolean
  /** Connector name shown in the heading ("Fireflies"). */
  connectorName: string
  /** Value of the input field (controlled). */
  apiKey: string
  /** Where the user should obtain their key — rendered as helper copy. */
  helpText?: ReactNode
  /** True while a validation request is in flight. */
  submitting: boolean
  /** Inline error from the backend's validation attempt, if any. */
  error: string | null
  onChange: (next: string) => void
  onSubmit: () => void
  onClose: () => void
}

export function ApiKeyPromptModalView({
  open,
  connectorName,
  apiKey,
  helpText,
  submitting,
  error,
  onChange,
  onSubmit,
  onClose,
}: ApiKeyPromptModalViewProps) {
  if (!open) return null
  const canSubmit = apiKey.trim().length > 0 && !submitting
  // The overlay is a flex container; the modal MUST be a child of it
  // (not a sibling) — the existing CSS uses `.modal-overlay.open .modal
  // { transform: scale(1); }` to reveal the modal, so a sibling layout
  // leaves it stuck at scale(0.96) and invisible.
  return (
    <div
      className="modal-overlay open"
      onClick={(e) => {
        // Backdrop click closes; clicks inside the modal shouldn't.
        if (e.target === e.currentTarget) onClose()
      }}
      aria-hidden={false}
    >
      <div
        className="modal modal-sm"
        role="dialog"
        aria-label={`Connect ${connectorName}`}
      >
        <div className="modal-head">
          <h2 className="modal-title">Connect {connectorName}</h2>
          <button
            type="button"
            className="modal-close"
            onClick={onClose}
            aria-label="Close"
          >
            ✕
          </button>
        </div>
        <div className="modal-body">
          {helpText ? <p className="modal-sub">{helpText}</p> : null}
          <label className="field-label">API key</label>
          <input
            type="text"
            className="input"
            value={apiKey}
            onChange={(e) => onChange(e.target.value)}
            placeholder={`Paste your ${connectorName} API key`}
            autoComplete="off"
            spellCheck={false}
          />
          {error ? (
            <p className="settings-msg settings-msg-error" role="alert">
              {error}
            </p>
          ) : null}
        </div>
        <div className="modal-foot">
          <button type="button" className="btn btn-sm" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-sm btn-primary"
            disabled={!canSubmit}
            onClick={onSubmit}
          >
            {submitting ? "Connecting…" : "Connect"}
          </button>
        </div>
      </div>
    </div>
  )
}

// ───────────────────── Hooks-wired wrapper ─────────────────────

type Props = {
  open: boolean
  connectorName: string
  helpText?: ReactNode
  /**
   * Performs the actual connect call (POSTs the key to the backend, gets
   * back an account_label, etc.). Throws or rejects on failure — the
   * modal catches and shows the error message inline.
   */
  onConnect: (apiKey: string) => Promise<void>
  onClose: () => void
}

export function ApiKeyPromptModal({
  open,
  connectorName,
  helpText,
  onConnect,
  onClose,
}: Props) {
  const [apiKey, setApiKey] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit() {
    setSubmitting(true)
    setError(null)
    try {
      await onConnect(apiKey.trim())
      setApiKey("")
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <ApiKeyPromptModalView
      open={open}
      connectorName={connectorName}
      apiKey={apiKey}
      helpText={helpText}
      submitting={submitting}
      error={error}
      onChange={setApiKey}
      onSubmit={() => void handleSubmit()}
      onClose={onClose}
    />
  )
}
