"use client"

import { useCallback, useEffect, useState } from "react"
import {
  adminApi,
  ApiError,
  apiErrorMessage,
  type LlmConfig,
  type LlmProvider,
} from "../../../../lib/api"
import { SettingsSection, SettingsMessage } from "./SettingsLayout"
import { UsageSettings } from "./UsageSettings"
import { registerSettingsCacheReset } from "../../../../lib/settingsCache"

/**
 * Admin pane — which AI provider this workspace runs on, and its API key.
 *
 * A workspace runs on Claude (Anthropic) or OpenAI. When a key is set for the
 * ACTIVE provider, ALL of the company's LLM calls use THAT key instead of the
 * platform key (embeddings are unaffected either way). Restricted to
 * owners/admins — the backend enforces this; a non-admin sees a restricted
 * message here (the initial status fetch returns 403).
 *
 * Choosing a provider is ONE click and takes effect immediately: the card IS
 * the setting, not a preview of it. Deliberately not a two-step "select, then
 * confirm" — a workspace with no key for the provider it just picked runs on
 * Sprntly's key for that provider, which is a working, already-supported state
 * (it is exactly what a keyless Claude workspace has always done), so there is
 * nothing to guard against. Each card keeps showing its own saved-key status
 * even when inactive, so switching never looks like it lost something.
 *
 * The View is pure (props in, JSX out) for renderToStaticMarkup unit tests; the
 * default-exported AdminSettings wraps it with the API wiring.
 */

type ProviderMeta = {
  id: LlmProvider
  name: string
  /** What running on this provider actually means, in one line. */
  blurb: string
  /** Placeholder + label copy for the key field. */
  keyLabel: string
  placeholder: string
  /** Where to go and get one. */
  consoleLabel: string
  consoleUrl: string
}

const PROVIDERS: ProviderMeta[] = [
  {
    id: "anthropic",
    name: "Claude",
    blurb: "Anthropic's models. Sprntly's default.",
    keyLabel: "Anthropic API key",
    placeholder: "sk-ant-…",
    consoleLabel: "console.anthropic.com",
    consoleUrl: "https://console.anthropic.com/settings/keys",
  },
  {
    id: "openai",
    name: "OpenAI",
    blurb: "GPT models, billed to your OpenAI account.",
    keyLabel: "OpenAI API key",
    placeholder: "sk-…",
    consoleLabel: "platform.openai.com",
    consoleUrl: "https://platform.openai.com/api-keys",
  },
]

function metaFor(provider: LlmProvider): ProviderMeta {
  return PROVIDERS.find((p) => p.id === provider) ?? PROVIDERS[0]
}

export type AdminSettingsViewProps = {
  config: LlmConfig | null
  restricted: boolean
  loading: boolean
  /** True while the provider switch is in flight — both cards lock so a
   *  double-click can't queue two switches. */
  switching: boolean
  keyInput: string
  saving: boolean
  removing: boolean
  testing: boolean
  error: string | null
  message: string | null
  onProviderChange: (provider: LlmProvider) => void
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

function CheckIcon() {
  return (
    <svg
      width="13"
      height="13"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d="M20 6L9 17l-5-5" />
    </svg>
  )
}

/** The two provider cards. A real radiogroup, not styled buttons pretending:
 *  arrow keys and screen-reader announcement come free, and the pressed state
 *  is `aria-checked` rather than colour alone. */
function ProviderChoice({
  config,
  disabled,
  onProviderChange,
}: {
  config: LlmConfig
  disabled: boolean
  onProviderChange: (provider: LlmProvider) => void
}) {
  return (
    <div className="prov-grid" role="radiogroup" aria-label="AI provider">
      {PROVIDERS.map((p) => {
        const active = config.provider === p.id
        const status = config.providers?.[p.id]
        return (
          <button
            key={p.id}
            type="button"
            role="radio"
            aria-checked={active}
            className={`prov-card${active ? " is-active" : ""}`}
            onClick={() => !active && onProviderChange(p.id)}
            disabled={disabled}
          >
            <span className="prov-card-head">
              <span className="prov-card-name">{p.name}</span>
              {active && (
                <span className="prov-card-active">
                  <CheckIcon />
                  In use
                </span>
              )}
            </span>
            <span className="prov-card-blurb">{p.blurb}</span>
            {/* Shown on the inactive card too — the point is that a stored key
                survives a switch, so it must not disappear from view. */}
            <span className="prov-card-key">
              {status?.configured ? "Your key is saved" : "No key — runs on Sprntly's"}
            </span>
          </button>
        )
      })}
    </div>
  )
}

export function AdminSettingsView({
  config,
  restricted,
  loading,
  switching,
  keyInput,
  saving,
  removing,
  testing,
  error,
  message,
  onProviderChange,
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

  const provider: LlmProvider = config?.provider ?? "anthropic"
  const meta = metaFor(provider)
  const status = config?.providers?.[provider] ?? null
  const configured = status?.configured ?? false
  const canSave = keyInput.trim().length > 0 && !saving
  const inputId = `${provider}-api-key`

  return (
    <SettingsSection
      title="AI provider"
      sub="Choose which models power Sprntly, and run them on your own API key. When a key is set, every Sprntly LLM call for this workspace goes through it and is billed to your account. Embeddings always run on Sprntly's infrastructure."
    >
      {loading || !config ? (
        <p className="settings-placeholder">Loading…</p>
      ) : (
        <>
          <ProviderChoice
            config={config}
            disabled={switching}
            onProviderChange={onProviderChange}
          />

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
              <label className="field-label" htmlFor={inputId}>
                {configured ? "Replace key" : meta.keyLabel}
              </label>
              {/* Input and its submit share one row — the button acts on the
                  field beside it, so they belong together. */}
              <div className="akey-input-row">
                <input
                  id={inputId}
                  // `key` forces a fresh node on a provider switch: without it
                  // React reuses the input and a half-typed key for the old
                  // provider would sit in the new provider's field.
                  key={inputId}
                  type="password"
                  className="input"
                  value={keyInput}
                  onChange={(e) => onKeyInputChange(e.target.value)}
                  autoComplete="off"
                  spellCheck={false}
                  placeholder={meta.placeholder}
                />
                <button
                  type="submit"
                  className="btn btn-primary akey-submit"
                  disabled={!canSave}
                >
                  {saving ? "Saving…" : configured ? "Replace key" : "Save key"}
                </button>
              </div>
              <p className="prov-hint">
                {configured
                  ? `Encrypted at rest and used only for this workspace's ${meta.name} calls.`
                  : `Get one at ${meta.consoleLabel}. Until you add it, ${meta.name} calls run on Sprntly's key at our cost.`}
              </p>
            </div>
            {error && <SettingsMessage kind="error">{error}</SettingsMessage>}
            {message && <SettingsMessage kind="success">{message}</SettingsMessage>}
          </form>
        </>
      )}
    </SettingsSection>
  )
}

// Module-scoped cache of the last-loaded admin config. Survives the pane
// remounting on a settings tab-switch, so a revisit renders the provider + key
// state INSTANTLY and revalidates in the background — no "Loading settings…"
// spinner every time. `null` = never loaded (the only cold case that spins).
// Cleared on sign-out via resetAdminSettingsCache.
let _adminCache: { config: LlmConfig | null; restricted: boolean } | null = null

// Clear on sign-out so a different user never sees the previous account's key
// status (see lib/settingsCache).
registerSettingsCacheReset(() => {
  _adminCache = null
})

export function AdminSettings() {
  // Seed from cache so a tab-switch return renders instantly; the effect below
  // still revalidates in the background.
  const [config, setConfig] = useState<LlmConfig | null>(() => _adminCache?.config ?? null)
  const [restricted, setRestricted] = useState(() => _adminCache?.restricted ?? false)
  const [loading, setLoading] = useState(() => _adminCache === null)
  const [switching, setSwitching] = useState(false)
  const [keyInput, setKeyInput] = useState("")
  const [saving, setSaving] = useState(false)
  const [removing, setRemoving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)

  const store = useCallback((next: LlmConfig) => {
    setConfig(next)
    _adminCache = { config: next, restricted: false }
  }, [])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const c = await adminApi.getLlmConfig()
        if (!cancelled) store(c)
      } catch (e) {
        if (cancelled) return
        if (e instanceof ApiError && e.status === 403) {
          setRestricted(true)
          _adminCache = { config: null, restricted: true }
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
  }, [store])

  const onProviderChange = useCallback(
    async (provider: LlmProvider) => {
      setError(null)
      setMessage(null)
      // A half-typed key belongs to the provider it was typed for.
      setKeyInput("")
      setSwitching(true)
      try {
        const next = await adminApi.setLlmProvider(provider)
        store(next)
        const name = metaFor(provider).name
        setMessage(
          next.providers?.[provider]?.configured
            ? `Sprntly now runs on ${name}, using your key.`
            : `Sprntly now runs on ${name}. Add your key below to bill it to your own account.`,
        )
      } catch (e) {
        setError(e instanceof ApiError ? apiErrorMessage(e.status, e.body) : "Could not switch provider.")
      } finally {
        setSwitching(false)
      }
    },
    [store],
  )

  const provider: LlmProvider = config?.provider ?? "anthropic"

  const onSave = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault()
      setError(null)
      setMessage(null)
      const key = keyInput.trim()
      if (!key) return
      setSaving(true)
      try {
        const s = await adminApi.setLlmKey(key, provider)
        if (config) store({ ...config, providers: { ...config.providers, [provider]: s } })
        setKeyInput("")
        setMessage(`${metaFor(provider).name} API key saved. Sprntly will now use it for this workspace.`)
      } catch (e) {
        setError(e instanceof ApiError ? apiErrorMessage(e.status, e.body) : "Could not save the key.")
      } finally {
        setSaving(false)
      }
    },
    [keyInput, provider, config, store],
  )

  const onRemove = useCallback(async () => {
    setError(null)
    setMessage(null)
    setRemoving(true)
    try {
      const s = await adminApi.deleteLlmKey(provider)
      if (config) store({ ...config, providers: { ...config.providers, [provider]: s } })
      setMessage("Key removed. Sprntly will use the platform key again.")
    } catch (e) {
      setError(e instanceof ApiError ? apiErrorMessage(e.status, e.body) : "Could not remove the key.")
    } finally {
      setRemoving(false)
    }
  }, [provider, config, store])

  const onTest = useCallback(async () => {
    setError(null)
    setMessage(null)
    setTesting(true)
    try {
      await adminApi.testLlmKey(provider)
      setMessage(`Key is valid — ${metaFor(provider).name} accepted a test call.`)
    } catch (e) {
      setError(e instanceof ApiError ? apiErrorMessage(e.status, e.body) : "Could not test the key.")
    } finally {
      setTesting(false)
    }
  }, [provider])

  return (
    <>
      <AdminSettingsView
        config={config}
        restricted={restricted}
        loading={loading}
        switching={switching}
        keyInput={keyInput}
        saving={saving}
        removing={removing}
        testing={testing}
        error={error}
        message={message}
        onProviderChange={onProviderChange}
        onKeyInputChange={setKeyInput}
        onSave={onSave}
        onRemove={onRemove}
        onTest={onTest}
      />
      {/* Usage lives in this pane, directly under the key it is reporting on:
          "here is the key we run on" then "here is what we ran on it". It is
          skipped entirely when restricted — UsageSettings would otherwise 403
          on its own and render a second copy of the same message. */}
      {!restricted && (
        <UsageSettings
          keyConfigured={config?.providers?.[provider]?.configured ?? false}
          provider={provider}
        />
      )}
    </>
  )
}
