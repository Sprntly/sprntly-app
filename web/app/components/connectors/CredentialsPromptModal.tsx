/**
 * Modal for credentials-based connectors (authType "credentials").
 *
 * Self-hosted tools like Superset have no vendor OAuth and no single API
 * key — the user connects with their instance URL + a service-account
 * login. Sibling of ApiKeyPromptModal with the same View/wrapper split:
 * pure View (renderToStaticMarkup-testable) + a hooks-wired default
 * wrapper that owns the field state and submit call.
 */
"use client"

import { useState, type ReactNode } from "react"

export type CredentialsValues = {
  baseUrl: string
  username: string
  password: string
}

export type CredentialsPromptModalViewProps = {
  open: boolean
  /** Connector name shown in the heading ("Superset"). */
  connectorName: string
  values: CredentialsValues
  /** Helper copy above the form (where the service account comes from). */
  helpText?: ReactNode
  /** True while the connect request is in flight. */
  submitting: boolean
  /** Inline error from the backend's validation attempt, if any. */
  error: string | null
  onChange: (next: CredentialsValues) => void
  onSubmit: () => void
  onClose: () => void
}

export function CredentialsPromptModalView({
  open,
  connectorName,
  values,
  helpText,
  submitting,
  error,
  onChange,
  onSubmit,
  onClose,
}: CredentialsPromptModalViewProps) {
  if (!open) return null
  const canSubmit =
    values.baseUrl.trim().length > 0 &&
    values.username.trim().length > 0 &&
    values.password.length > 0 &&
    !submitting
  // Same overlay contract as ApiKeyPromptModal: the modal must be a CHILD
  // of the overlay for `.modal-overlay.open .modal` to reveal it.
  return (
    <div
      className="modal-overlay open"
      onClick={(e) => {
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
          <label className="field-label">Instance URL</label>
          <input
            type="url"
            className="input"
            value={values.baseUrl}
            onChange={(e) => onChange({ ...values, baseUrl: e.target.value })}
            placeholder={`https://your-${connectorName.toLowerCase()}.example.com`}
            autoComplete="off"
            spellCheck={false}
          />
          <label className="field-label">Username</label>
          <input
            type="text"
            className="input"
            value={values.username}
            onChange={(e) => onChange({ ...values, username: e.target.value })}
            placeholder="Service-account username"
            autoComplete="off"
            spellCheck={false}
          />
          <label className="field-label">Password</label>
          <input
            type="password"
            className="input"
            value={values.password}
            onChange={(e) => onChange({ ...values, password: e.target.value })}
            placeholder="Service-account password"
            autoComplete="new-password"
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

const EMPTY: CredentialsValues = { baseUrl: "", username: "", password: "" }

type Props = {
  open: boolean
  connectorName: string
  helpText?: ReactNode
  /**
   * Performs the actual connect call (POSTs the credentials to the
   * backend). Throws or rejects on failure — the modal catches and shows
   * the error message inline.
   */
  onConnect: (values: CredentialsValues) => Promise<void>
  onClose: () => void
}

export function CredentialsPromptModal({
  open,
  connectorName,
  helpText,
  onConnect,
  onClose,
}: Props) {
  const [values, setValues] = useState<CredentialsValues>(EMPTY)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit() {
    setSubmitting(true)
    setError(null)
    try {
      await onConnect({
        baseUrl: values.baseUrl.trim(),
        username: values.username.trim(),
        password: values.password,
      })
      setValues(EMPTY)
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <CredentialsPromptModalView
      open={open}
      connectorName={connectorName}
      values={values}
      helpText={helpText}
      submitting={submitting}
      error={error}
      onChange={setValues}
      onSubmit={() => void handleSubmit()}
      onClose={onClose}
    />
  )
}
