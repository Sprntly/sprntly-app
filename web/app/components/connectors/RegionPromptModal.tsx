/**
 * Modal for OAuth connectors that run more than one regional deployment.
 *
 * Marvin is the first: its US and EU installs are separate products with
 * separate authorization servers, so we cannot start the OAuth redirect until
 * the user tells us which one holds their workspace — guessing sends EU
 * customers to a consent screen for an account they don't have.
 *
 * Sibling of ApiKeyPromptModal / CredentialsPromptModal with the same
 * View/wrapper split: a pure View (renderToStaticMarkup-testable) plus a
 * hooks-wired wrapper owning the selection and the in-flight state. Unlike
 * those two, submitting here does not connect — it hands the chosen region to
 * the caller's OAuth start, which then redirects.
 */
"use client"

import { useState, type ReactNode } from "react"
import type { ConnectorRegion } from "../../types/content"

export type RegionPromptModalViewProps = {
  open: boolean
  /** Connector name shown in the heading ("Marvin"). */
  connectorName: string
  regions: ConnectorRegion[]
  /** Currently selected region value. */
  value: string
  /** Helper copy above the picker (what the choice affects). */
  helpText?: ReactNode
  /** True while the OAuth start request is in flight. */
  submitting: boolean
  /** Inline error from a failed start-OAuth call, if any. */
  error: string | null
  onChange: (next: string) => void
  onSubmit: () => void
  onClose: () => void
}

export function RegionPromptModalView({
  open,
  connectorName,
  regions,
  value,
  helpText,
  submitting,
  error,
  onChange,
  onSubmit,
  onClose,
}: RegionPromptModalViewProps) {
  if (!open) return null
  // Same overlay contract as the sibling modals: the modal must be a CHILD of
  // the overlay for `.modal-overlay.open .modal` to reveal it.
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
          <label className="field-label" htmlFor="conn-region">
            {connectorName} region
          </label>
          <select
            id="conn-region"
            className="input"
            value={value}
            onChange={(e) => onChange(e.target.value)}
          >
            {regions.map((region) => (
              <option key={region.value} value={region.value}>
                {region.label}
              </option>
            ))}
          </select>
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
            disabled={submitting}
            onClick={onSubmit}
          >
            {submitting ? "Connecting…" : `Continue to ${connectorName}`}
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
  regions: ConnectorRegion[]
  helpText?: ReactNode
  /**
   * Starts the OAuth flow for the chosen region. Throws or rejects on
   * failure — the modal catches and shows the message inline. On success the
   * browser is already navigating, so the modal simply closes.
   */
  onConnect: (region: string) => Promise<void>
  onClose: () => void
}

export function RegionPromptModal({
  open,
  connectorName,
  regions,
  helpText,
  onConnect,
  onClose,
}: Props) {
  const [value, setValue] = useState(regions[0]?.value ?? "")
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit() {
    setSubmitting(true)
    setError(null)
    try {
      await onConnect(value || regions[0]?.value || "")
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <RegionPromptModalView
      open={open}
      connectorName={connectorName}
      regions={regions}
      value={value || regions[0]?.value || ""}
      helpText={helpText}
      submitting={submitting}
      error={error}
      onChange={setValue}
      onSubmit={() => void handleSubmit()}
      onClose={onClose}
    />
  )
}
