"use client"

import { useCallback, useEffect, useState } from "react"
import { adminApi, ApiError, apiErrorMessage, type LlmKeyStatus } from "../../../../lib/api"
import { SettingsSection, SettingsMessage } from "./SettingsLayout"
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
          {configured && status?.masked && (
            <p className="settings-row-sub" style={{ marginBottom: 8 }}>
              Current key: <code>{status.masked}</code>
            </p>
          )}
          <div className="field">
            <label className="field-label">
              {configured ? "Replace key" : "API key"}
            </label>
            <input
              type="password"
              className="input"
              value={keyInput}
              onChange={(e) => onKeyInputChange(e.target.value)}
              autoComplete="off"
              spellCheck={false}
              placeholder="sk-ant-…"
            />
          </div>
          {error && <SettingsMessage kind="error">{error}</SettingsMessage>}
          {message && <SettingsMessage kind="success">{message}</SettingsMessage>}
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button type="submit" className="btn btn-primary" disabled={!canSave}>
              {saving ? "Saving…" : configured ? "Replace key" : "Save key"}
            </button>
            {configured && (
              <>
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
                  className="btn"
                  onClick={onRemove}
                  disabled={removing}
                >
                  {removing ? "Removing…" : "Remove key"}
                </button>
              </>
            )}
          </div>
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
  )
}
