/**
 * Onboarding connector modal — same OAuth / API-key flow as the
 * Settings → Connectors Configure drawer, but bundled into a modal
 * that pops in from the connector grid in Onboarding step 4.
 *
 * Three modes (one component, conditional rendering):
 *   - Pre-connect OAuth   — "Connect with Figma" CTA
 *   - Pre-connect API-key — paste-the-key form (Fireflies)
 *   - Connected           — "✓ Connected as alice@x.com" + provider-
 *                            specific config slot (Drive folder picker,
 *                            Slack channel picker) + Done button
 *
 * Plus a "complete or restart" prompt that appears when the user
 * re-opens the modal after starting an OAuth flow without finishing
 * it (a flag is left in localStorage by the wrapper when Connect is
 * clicked; cleared on success or restart).
 *
 * View is pure (props in, JSX out) — unit-tested via
 * renderToStaticMarkup per the project's component-test convention.
 */
"use client"

import { useCallback, useEffect, useState } from "react"
import {
  ApiError,
  apiErrorMessage,
  connectorsApi,
  type ConnectionSummary,
} from "../../lib/api"
import { CONNECTOR_CATALOG } from "../../lib/connectorsCatalog"
import type { ConnectorItemRow } from "../../types/content"
import { ConnectorLogo } from "./ConnectorLogo"
import type { CredentialsValues } from "./CredentialsPromptModal"
import { openOauthTab } from "../../lib/connectorsOauth"
import { GithubInstallsSlot } from "./GithubInstallsSlot"
import { GoogleDrivePicker } from "./GoogleDrivePicker"
import { SlackChannelPicker } from "./SlackChannelPicker"

/**
 * Provider page (keyed by connector id) where the user can view and copy
 * their API key. Rendered as a link in the api-key blurb so they don't have
 * to hunt through the provider's menus. Omit a connector to fall back to the
 * plain "paste yours below" copy.
 */
const APIKEY_PAGE_URL: Record<string, string> = {
  fireflies: "https://app.fireflies.ai/integrations/custom/fireflies",
}

// ─────────────────────────── Pure View ───────────────────────────

export type ConnectorConnectModalViewProps = {
  open: boolean
  /** Null when no connector is being configured — modal renders nothing. */
  item: ConnectorItemRow | null
  /** Live connection record; non-null means the connector is already active. */
  connection: ConnectionSummary | null

  /** "oauth" (most providers), "apikey" (Fireflies), or "credentials"
   *  (self-hosted tools like Superset — URL + username + password). */
  authType: "oauth" | "apikey" | "credentials"

  /** Controlled api-key input value (apikey mode only). */
  apiKey: string
  apiKeyError: string | null
  isSubmittingApiKey: boolean

  /** Controlled form values (credentials mode only). */
  credentials?: CredentialsValues
  credentialsError?: string | null
  isSubmittingCredentials?: boolean

  /** True while the OAuth startOauth request is in flight (between
   *  click + browser navigation). */
  isConnecting: boolean
  /** Inline error from a failed startOauth call. */
  oauthError: string | null

  /** True when the user re-opens the modal mid-OAuth (started but
   *  didn't return successfully). Suppressed when connection is set. */
  showCompleteOrRestart: boolean

  onClose: () => void
  onSkipForLater: () => void
  onConnect: () => void
  onApiKeyChange: (next: string) => void
  onSubmitApiKey: () => void
  onCredentialsChange?: (next: CredentialsValues) => void
  onSubmitCredentials?: () => void
  onCompleteFlow: () => void
  onRestartFlow: () => void

  /** Provider-specific config slot shown when connected (folder /
   *  channel pickers). */
  children?: React.ReactNode
}

export function ConnectorConnectModalView({
  open,
  item,
  connection,
  authType,
  apiKey,
  apiKeyError,
  isSubmittingApiKey,
  credentials = { baseUrl: "", username: "", password: "" },
  credentialsError = null,
  isSubmittingCredentials = false,
  isConnecting,
  oauthError,
  showCompleteOrRestart,
  onClose,
  onSkipForLater,
  onConnect,
  onApiKeyChange,
  onSubmitApiKey,
  onCredentialsChange = () => {},
  onSubmitCredentials = () => {},
  onCompleteFlow,
  onRestartFlow,
  children,
}: ConnectorConnectModalViewProps) {
  if (!open || !item) return null

  const isConnected = connection != null
  const canSubmitApiKey = apiKey.trim().length > 0 && !isSubmittingApiKey
  const canSubmitCredentials =
    credentials.baseUrl.trim().length > 0 &&
    credentials.username.trim().length > 0 &&
    credentials.password.length > 0 &&
    !isSubmittingCredentials
  // Connected state wins — never show the in-flight prompt at the same
  // time as a Done screen.
  const showPrompt = showCompleteOrRestart && !isConnected

  return (
    <div
      className="modal-overlay open"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
      aria-hidden={false}
    >
      <div className="modal modal-md" role="dialog" aria-label={`Connect ${item.name}`}>
        <div className="modal-head">
          <ConnectorLogo item={item} className="conn-modal-logo" />
          <h2 className="modal-title">
            {isConnected ? `${item.name} connected` : `Connect ${item.name}`}
          </h2>
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
          {/* ─── Connected state ─── */}
          {isConnected ? (
            <>
              <p className="conn-modal-status">
                <strong>✓ Connected as</strong>{" "}
                {connection?.account_label ??
                  connection?.google_email ??
                  "(no label)"}
              </p>
              {children ? (
                <div className="conn-modal-slot">{children}</div>
              ) : null}
            </>
          ) : showPrompt ? (
            /* ─── In-flight prompt ─── */
            <div className="conn-modal-prompt">
              <p>
                Looks like you started connecting <strong>{item.name}</strong>{" "}
                but didn't finish. Pick up where you left off, or start over?
              </p>
              <div className="conn-modal-prompt-actions">
                <button
                  type="button"
                  className="btn btn-sm btn-primary"
                  onClick={onCompleteFlow}
                >
                  Complete connection
                </button>
                <button
                  type="button"
                  className="btn btn-sm"
                  onClick={onRestartFlow}
                >
                  Start over
                </button>
              </div>
            </div>
          ) : authType === "apikey" ? (
            /* ─── Pre-connect API-key form ─── */
            <>
              <p className="conn-modal-blurb">
                {item.name} uses an API key.{" "}
                {APIKEY_PAGE_URL[item.id] ? (
                  <>
                    Open your{" "}
                    <a
                      href={APIKEY_PAGE_URL[item.id]}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      {item.name} API settings
                    </a>
                    {" "}and paste your key below.
                  </>
                ) : (
                  "Paste yours below."
                )}
              </p>
              <label className="field-label" htmlFor="conn-modal-apikey">
                API key
              </label>
              <input
                id="conn-modal-apikey"
                type="password"
                className="input"
                value={apiKey}
                onChange={(e) => onApiKeyChange(e.target.value)}
                autoComplete="off"
                spellCheck={false}
                placeholder={`Paste your ${item.name} API key`}
              />
              {apiKeyError ? (
                <p className="conn-modal-error" role="alert">
                  {apiKeyError}
                </p>
              ) : null}
            </>
          ) : authType === "credentials" ? (
            /* ─── Pre-connect credentials form (self-hosted tools) ─── */
            <>
              <p className="conn-modal-blurb">
                {item.name} is self-hosted — enter your instance URL and a
                service-account login (ideally a dedicated read-only user
                created just for Sprntly).
              </p>
              <label className="field-label" htmlFor="conn-modal-cred-url">
                Instance URL
              </label>
              <input
                id="conn-modal-cred-url"
                type="url"
                className="input"
                value={credentials.baseUrl}
                onChange={(e) =>
                  onCredentialsChange({ ...credentials, baseUrl: e.target.value })
                }
                placeholder={`https://your-${item.name.toLowerCase()}.example.com`}
                autoComplete="off"
                spellCheck={false}
              />
              <label className="field-label" htmlFor="conn-modal-cred-user">
                Username
              </label>
              <input
                id="conn-modal-cred-user"
                type="text"
                className="input"
                value={credentials.username}
                onChange={(e) =>
                  onCredentialsChange({ ...credentials, username: e.target.value })
                }
                placeholder="Service-account username"
                autoComplete="off"
                spellCheck={false}
              />
              <label className="field-label" htmlFor="conn-modal-cred-pass">
                Password
              </label>
              <input
                id="conn-modal-cred-pass"
                type="password"
                className="input"
                value={credentials.password}
                onChange={(e) =>
                  onCredentialsChange({ ...credentials, password: e.target.value })
                }
                placeholder="Service-account password"
                autoComplete="new-password"
              />
              {credentialsError ? (
                <p className="conn-modal-error" role="alert">
                  {credentialsError}
                </p>
              ) : null}
            </>
          ) : (
            /* ─── Pre-connect OAuth ─── */
            <>
              <p className="conn-modal-blurb">
                Sprntly will open {item.name} in a new tab to authorize the
                connection. Finish there, then come back to this tab — your
                onboarding stays right where it is.
              </p>
              {oauthError ? (
                <p className="conn-modal-error" role="alert">
                  {oauthError}
                </p>
              ) : null}
            </>
          )}
        </div>

        <div className="modal-foot">
          {isConnected ? (
            <button
              type="button"
              className="btn btn-sm btn-primary"
              onClick={onClose}
            >
              Done
            </button>
          ) : showPrompt ? (
            /* Footer actions live inside the prompt block above. */
            <button type="button" className="btn btn-sm" onClick={onClose}>
              Close
            </button>
          ) : authType === "apikey" ? (
            <>
              <button
                type="button"
                className="btn btn-sm"
                onClick={onSkipForLater}
              >
                Skip &amp; mark for later
              </button>
              <button
                type="button"
                className="btn btn-sm btn-primary"
                disabled={!canSubmitApiKey}
                onClick={onSubmitApiKey}
              >
                {isSubmittingApiKey ? "Connecting…" : "Connect"}
              </button>
            </>
          ) : authType === "credentials" ? (
            <>
              <button
                type="button"
                className="btn btn-sm"
                onClick={onSkipForLater}
              >
                Skip &amp; mark for later
              </button>
              <button
                type="button"
                className="btn btn-sm btn-primary"
                disabled={!canSubmitCredentials}
                onClick={onSubmitCredentials}
              >
                {isSubmittingCredentials ? "Connecting…" : "Connect"}
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                className="btn btn-sm"
                onClick={onSkipForLater}
              >
                Skip &amp; mark for later
              </button>
              <button
                type="button"
                className="btn btn-sm btn-primary"
                disabled={isConnecting}
                onClick={onConnect}
              >
                {isConnecting ? "Connecting…" : `Connect with ${item.name}`}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

// ───────────────────── Hooks-wired wrapper ─────────────────────

type Props = {
  /** The catalog item id that was clicked. Null = modal is closed. */
  providerId: string | null
  /** Active company slug — passed as `dataset` for Drive's start-OAuth. */
  activeCompany: string
  /** Live connection (matching the providerId), if any. */
  connection: ConnectionSummary | null
  /** Where the OAuth callback should bounce the user back to.
   *  Onboarding passes /onboarding/connectors so the user lands in the same step. */
  returnTo: string
  /** Fired when the user dismisses the modal. */
  onClose: () => void
  /** Fired after a successful API-key submit, so the parent can reload
   *  connections + the catalog grid. OAuth doesn't fire this — the
   *  full-page redirect handles it via ?connected= on return. */
  onConnected: () => void
  /** Fired when the user clicks "Skip & mark for later" — parent
   *  toggles the connector into its `planned` set. */
  onSkipForLater: () => void
}

const IN_FLIGHT_TTL_MS = 10 * 60 * 1000 // matches backend STATE_TTL_SECONDS

function inFlightKey(providerId: string): string {
  return `sprntly:connect-in-flight:${providerId}`
}

function markConnectInFlight(providerId: string): void {
  if (typeof window === "undefined") return
  try {
    window.localStorage.setItem(inFlightKey(providerId), String(Date.now()))
  } catch {
    /* private mode etc. — non-fatal */
  }
}

function clearConnectInFlight(providerId: string): void {
  if (typeof window === "undefined") return
  try {
    window.localStorage.removeItem(inFlightKey(providerId))
  } catch {
    /* non-fatal */
  }
}

function isConnectInFlight(providerId: string): boolean {
  if (typeof window === "undefined") return false
  try {
    const raw = window.localStorage.getItem(inFlightKey(providerId))
    if (!raw) return false
    const startedAt = Number(raw)
    if (!Number.isFinite(startedAt)) return false
    return Date.now() - startedAt < IN_FLIGHT_TTL_MS
  } catch {
    return false
  }
}

function lookupItem(providerId: string): ConnectorItemRow | null {
  for (const cat of CONNECTOR_CATALOG) {
    const found = cat.items.find((i) => i.id === providerId)
    if (found) return found
  }
  return null
}

export function ConnectorConnectModal({
  providerId,
  activeCompany,
  connection,
  returnTo,
  onClose,
  onConnected,
  onSkipForLater,
}: Props) {
  const item = providerId ? lookupItem(providerId) : null
  const authType: "oauth" | "apikey" | "credentials" =
    item?.authType === "apikey" || item?.authType === "credentials"
      ? item.authType
      : "oauth"
  const open = providerId != null && item != null

  const [apiKey, setApiKey] = useState("")
  const [apiKeyError, setApiKeyError] = useState<string | null>(null)
  const [isSubmittingApiKey, setIsSubmittingApiKey] = useState(false)
  const [credentials, setCredentials] = useState<CredentialsValues>(
    { baseUrl: "", username: "", password: "" },
  )
  const [credentialsError, setCredentialsError] = useState<string | null>(null)
  const [isSubmittingCredentials, setIsSubmittingCredentials] = useState(false)
  const [isConnecting, setIsConnecting] = useState(false)
  const [oauthError, setOauthError] = useState<string | null>(null)
  const [showCompleteOrRestart, setShowCompleteOrRestart] = useState(false)

  // Reset all transient state when the modal opens for a different
  // provider — otherwise stale errors / api-key text bleeds between
  // connectors.
  useEffect(() => {
    if (!providerId) return
    setApiKey("")
    setApiKeyError(null)
    setIsSubmittingApiKey(false)
    setCredentials({ baseUrl: "", username: "", password: "" })
    setCredentialsError(null)
    setIsSubmittingCredentials(false)
    setIsConnecting(false)
    setOauthError(null)
    // Detect in-flight: a Connect was started for this provider that
    // didn't complete (no active connection AND we have a fresh
    // localStorage flag).
    const inFlight = isConnectInFlight(providerId) && connection == null
    setShowCompleteOrRestart(inFlight)
  }, [providerId, connection])

  // When a connection lands (typically via the ?connected= return),
  // clear any in-flight flag so subsequent re-opens don't show the
  // prompt.
  useEffect(() => {
    if (connection != null && providerId) {
      clearConnectInFlight(providerId)
      setShowCompleteOrRestart(false)
    }
  }, [connection, providerId])

  const handleConnect = useCallback(async () => {
    if (!providerId) return
    setIsConnecting(true)
    setOauthError(null)
    markConnectInFlight(providerId)
    // Open the provider tab synchronously, while the click gesture is still
    // live, so the popup blocker doesn't reject it after the startOauth await.
    const oauthTab = openOauthTab()
    try {
      // Drive uses dataset to scope folder choice; others ignore it.
      const dataset = providerId === "google_drive" ? activeCompany : undefined
      const r = await connectorsApi.startOauth(providerId, dataset, returnTo)
      if (r.authorize_url) {
        oauthTab.finish(r.authorize_url)
      } else {
        oauthTab.abort()
      }
    } catch (e) {
      oauthTab.abort()
      const msg =
        e instanceof ApiError
          ? apiErrorMessage(e.status, e.body)
          : e instanceof Error
            ? e.message
            : String(e)
      setOauthError(msg)
      clearConnectInFlight(providerId) // never got off the ground
    } finally {
      setIsConnecting(false)
    }
  }, [providerId, activeCompany, returnTo])

  const handleSubmitApiKey = useCallback(async () => {
    if (!providerId || !apiKey.trim()) return
    setIsSubmittingApiKey(true)
    setApiKeyError(null)
    try {
      if (providerId === "fireflies") {
        await connectorsApi.connectFirefliesWithApiKey(apiKey.trim())
        onConnected()
      } else {
        throw new Error(`API-key connect not wired for provider: ${providerId}`)
      }
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? apiErrorMessage(e.status, e.body)
          : e instanceof Error
            ? e.message
            : String(e)
      setApiKeyError(msg)
    } finally {
      setIsSubmittingApiKey(false)
    }
  }, [providerId, apiKey, onConnected])

  const handleSubmitCredentials = useCallback(async () => {
    if (!providerId) return
    setIsSubmittingCredentials(true)
    setCredentialsError(null)
    try {
      if (providerId === "superset") {
        await connectorsApi.connectSupersetWithCredentials(
          credentials.baseUrl.trim(),
          credentials.username.trim(),
          credentials.password,
        )
        onConnected()
      } else {
        throw new Error(
          `Credentials connect not wired for provider: ${providerId}`,
        )
      }
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? apiErrorMessage(e.status, e.body)
          : e instanceof Error
            ? e.message
            : String(e)
      setCredentialsError(msg)
    } finally {
      setIsSubmittingCredentials(false)
    }
  }, [providerId, credentials, onConnected])

  const handleRestart = useCallback(() => {
    if (!providerId) return
    clearConnectInFlight(providerId)
    setShowCompleteOrRestart(false)
  }, [providerId])

  // Slot: provider-specific config shown only when connected. Mirrors
  // ConfigureConnectorDrawer's dispatch.
  let slot: React.ReactNode = null
  if (connection != null) {
    if (providerId === "google_drive") {
      slot = (
        <GoogleDrivePicker
          dataset={activeCompany}
          savedFiles={connection.config?.files}
          onSaved={onConnected}
        />
      )
    } else if (providerId === "slack") {
      slot = (
        <SlackChannelPicker
          savedChannelId={connection.config?.channel_id as string | undefined}
          savedChannelName={
            connection.config?.channel_name as string | undefined
          }
          onSaved={onConnected}
        />
      )
    } else if (providerId === "github") {
      // Same picker the settings Configure drawer mounts — lets the
      // user manage which repos the agent can read right inside the
      // onboarding modal, without bouncing to /settings or github.com.
      slot = <GithubInstallsSlot onChanged={onConnected} />
    }
  }

  return (
    <ConnectorConnectModalView
      open={open}
      item={item}
      connection={connection}
      authType={authType}
      apiKey={apiKey}
      apiKeyError={apiKeyError}
      isSubmittingApiKey={isSubmittingApiKey}
      credentials={credentials}
      credentialsError={credentialsError}
      isSubmittingCredentials={isSubmittingCredentials}
      isConnecting={isConnecting}
      oauthError={oauthError}
      showCompleteOrRestart={showCompleteOrRestart}
      onClose={onClose}
      onSkipForLater={onSkipForLater}
      onConnect={() => void handleConnect()}
      onApiKeyChange={setApiKey}
      onSubmitApiKey={() => void handleSubmitApiKey()}
      onCredentialsChange={setCredentials}
      onSubmitCredentials={() => void handleSubmitCredentials()}
      onCompleteFlow={() => void handleConnect()}
      onRestartFlow={handleRestart}
    >
      {slot}
    </ConnectorConnectModalView>
  )
}
