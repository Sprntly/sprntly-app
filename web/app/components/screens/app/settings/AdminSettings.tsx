"use client"

import { useCallback, useEffect, useState } from "react"
import { adminApi, ApiError, apiErrorMessage, type LlmKeyStatus } from "../../../../lib/api"
import { SettingsSection, SettingsMessage } from "./SettingsLayout"
import { UsageSettings } from "./UsageSettings"
import { registerSettingsCacheReset } from "../../../../lib/settingsCache"

/**
 * Admin pane — the company's own Claude (Anthropic) API key.
 *
 * When a key is set, ALL of the company's Claude LLM calls use THAT key instead
 * of the platform key (OpenAI embeddings are unaffected). Restricted to
 * owners/admins — the backend enforces this; a non-admin sees a restricted
 * message here (the initial status fetch returns 403).
 *
 * The View is pure (props in, JSX out) for renderToStaticMarkup unit tests; the
 * default-exported AdminSettings wraps it with the API wiring.
 */
export type AdminSettingsViewProps = {
  status: LlmKeyStatus | null
  restricted: boolean
  loading: boolean
  keyInput: string
  saving: boolean
  removing: boolean
  testing: boolean
  error: string | null
  message: string | null
  onKeyInputChange: (v: string) => void
  onSave: (e: React.FormEvent) => void
  onRemove: () => void
  onTest: () => void
}

function TrashIcon() {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d="M3 6h18" />
      <path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2" />
      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
      <path d="M10 11v6M14 11v6" />
    </svg>
  )
}

export function AdminSettingsView({
  status,
  restricted,
  loading,
  keyInput,
  saving,
  removing,
  testing,
  error,
  message,
  onKeyInputChange,
  onSave,
  onRemove,
  onTest,
}: AdminSettingsViewProps) {
  if (restricted) {
    return (
      <SettingsSection
        title="Admin"
        sub="Workspace-level administrative settings."
      >
        <p className="settings-placeholder">
          Admin settings are restricted to owners and admins.
        </p>
      </SettingsSection>
    )
  }

  const configured = status?.configured ?? false
  const canSave = keyInput.trim().length > 0 && !saving

  return (
    <SettingsSection
      title="Claude API key"
      sub="Use your own Anthropic (Claude) API key for this workspace. When set, all of Sprntly's Claude calls run on your key and are billed to your Anthropic account. Embeddings are unaffected."
    >
      {loading ? (
        <p className="settings-placeholder">Loading…</p>
      ) : (
        <form onSubmit={onSave}>
          {/* The stored key gets its own full-width row: the key on the left,
              the actions that operate ON it on the right. Keeping Test/Remove
              here rather than next to the input separates "act on the existing
              key" from "supply a new one". */}
          {configured && (
            <div className="akey-current">
              <div className="akey-current-main">
                <span className="akey-current-label">Current key</span>
                {status?.masked ? (
                  <code className="akey-current-value">{status.masked}</code>
                ) : (
                  // configured but undecryptable (e.g. TOKEN_ENCRYPTION_KEY
                  // rotated) — no preview to show, but Remove must stay usable.
                  <span className="akey-current-value akey-current-muted">
                    Stored — preview unavailable
                  </span>
                )}
              </div>
              <div className="akey-current-actions">
                <button
                  type="button"
                  className="btn"
                  onClick={onTest}
                  disabled={testing}
                >
                  {testing ? "Testing…" : "Test key"}
                </button>
                <button
                  type="button"
                  className="btn akey-icon-btn"
                  onClick={onRemove}
                  disabled={removing}
                  // Icon-only, so the name has to come from the accessible
                  // name — screen readers and the native tooltip both read it.
                  aria-label="Remove key"
                  title="Remove key"
                  aria-busy={removing}
                >
                  <TrashIcon />
                </button>
              </div>
            </div>
          )}

          <div className="field">
            <label className="field-label" htmlFor="anthropic-api-key">
              {configured ? "Replace key" : "API key"}
            </label>
            {/* Input and its submit share one row — the button acts on the
                field beside it, so they belong together. */}
            <div className="akey-input-row">
              <input
                id="anthropic-api-key"
                type="password"
                className="input"
                value={keyInput}
                onChange={(e) => onKeyInputChange(e.target.value)}
                autoComplete="off"
                spellCheck={false}
                placeholder="sk-ant-…"
              />
              <button
                type="submit"
                className="btn btn-primary akey-submit"
                disabled={!canSave}
              >
                {saving ? "Saving…" : configured ? "Replace key" : "Save key"}
              </button>
            </div>
          </div>
          {error && <SettingsMessage kind="error">{error}</SettingsMessage>}
          {message && <SettingsMessage kind="success">{message}</SettingsMessage>}
        </form>
      )}
    </SettingsSection>
  )
}

// Module-scoped cache of the last-loaded admin key status. Survives the pane
// remounting on a settings tab-switch, so a revisit renders the key state
// INSTANTLY and revalidates in the background — no "Loading settings…" spinner
// every time. `null` = never loaded (the only cold case that spins). Cleared
// on sign-out via resetAdminSettingsCache.
let _adminCache: { status: LlmKeyStatus | null; restricted: boolean } | null = null

// Clear on sign-out so a different user never sees the previous account's key
// status (see lib/settingsCache).
registerSettingsCacheReset(() => {
  _adminCache = null
})

export function AdminSettings() {
  // Seed from cache so a tab-switch return renders instantly; the effect below
  // still revalidates in the background.
  const [status, setStatus] = useState<LlmKeyStatus | null>(() => _adminCache?.status ?? null)
  const [restricted, setRestricted] = useState(() => _adminCache?.restricted ?? false)
  const [loading, setLoading] = useState(() => _adminCache === null)
  const [keyInput, setKeyInput] = useState("")
  const [saving, setSaving] = useState(false)
  const [removing, setRemoving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const s = await adminApi.getLlmKey()
        if (!cancelled) {
          setStatus(s)
          _adminCache = { status: s, restricted: false }
        }
      } catch (e) {
        if (cancelled) return
        if (e instanceof ApiError && e.status === 403) {
          setRestricted(true)
          _adminCache = { status: null, restricted: true }
        } else {
          setError(e instanceof ApiError ? apiErrorMessage(e.status, e.body) : "Could not load settings.")
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const onSave = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault()
      setError(null)
      setMessage(null)
      const key = keyInput.trim()
      if (!key) return
      setSaving(true)
      try {
        const s = await adminApi.setLlmKey(key)
        setStatus(s)
        _adminCache = { status: s, restricted: false }
        setKeyInput("")
        setMessage("Claude API key saved. Sprntly will now use it for this workspace.")
      } catch (e) {
        setError(e instanceof ApiError ? apiErrorMessage(e.status, e.body) : "Could not save the key.")
      } finally {
        setSaving(false)
      }
    },
    [keyInput],
  )

  const onRemove = useCallback(async () => {
    setError(null)
    setMessage(null)
    setRemoving(true)
    try {
      const s = await adminApi.deleteLlmKey()
      setStatus(s)
      _adminCache = { status: s, restricted: false }
      setMessage("Key removed. Sprntly will use the platform key again.")
    } catch (e) {
      setError(e instanceof ApiError ? apiErrorMessage(e.status, e.body) : "Could not remove the key.")
    } finally {
      setRemoving(false)
    }
  }, [])

  const onTest = useCallback(async () => {
    setError(null)
    setMessage(null)
    setTesting(true)
    try {
      await adminApi.testLlmKey()
      setMessage("Key is valid — Anthropic accepted a test call.")
    } catch (e) {
      setError(e instanceof ApiError ? apiErrorMessage(e.status, e.body) : "Could not test the key.")
    } finally {
      setTesting(false)
    }
  }, [])

  return (
    <>
      <AdminSettingsView
        status={status}
        restricted={restricted}
        loading={loading}
        keyInput={keyInput}
        saving={saving}
        removing={removing}
        testing={testing}
        error={error}
        message={message}
        onKeyInputChange={setKeyInput}
        onSave={onSave}
        onRemove={onRemove}
        onTest={onTest}
      />
      {/* Usage lives in this pane, directly under the key it is reporting on:
          "here is the key we run on" then "here is what we ran on it". It is
          skipped entirely when restricted — UsageSettings would otherwise 403
          on its own and render a second copy of the same message. */}
      {!restricted && <UsageSettings keyConfigured={status?.configured ?? false} />}
    </>
  )
}
