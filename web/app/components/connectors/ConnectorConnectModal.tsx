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
import { GithubInstallsSlot } from "./GithubInstallsSlot"
import { GoogleDriveFolderPicker } from "./GoogleDriveFolderPicker"
import { SlackChannelPicker } from "./SlackChannelPicker"

// ─────────────────────────── Pure View ───────────────────────────

export type ConnectorConnectModalViewProps = {
  open: boolean
  /** Null when no connector is being configured — modal renders nothing. */
  item: ConnectorItemRow | null
  /** Live connection record; non-null means the connector is already active. */
  connection: ConnectionSummary | null

  /** "oauth" (most providers) vs "apikey" (Fireflies). */
  authType: "oauth" | "apikey"

  /** Controlled api-key input value (apikey mode only). */
  apiKey: string
  apiKeyError: string | null
  isSubmittingApiKey: boolean

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
  isConnecting,
  oauthError,
  showCompleteOrRestart,
  onClose,
  onSkipForLater,
  onConnect,
  onApiKeyChange,
  onSubmitApiKey,
  onCompleteFlow,
  onRestartFlow,
  children,
}: ConnectorConnectModalViewProps) {
  if (!open || !item) return null

  const isConnected = connection != null
  const canSubmitApiKey = apiKey.trim().length > 0 && !isSubmittingApiKey
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
                {item.name} uses an API key. Paste yours below.
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
          ) : (
            /* ─── Pre-connect OAuth ─── */
            <>
              <p className="conn-modal-blurb">
                Sprntly will redirect you to {item.name} to authorize the
                connection, then bring you back here.
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
   *  Onboarding passes /onboarding/4 so the user lands in the same step. */
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
  const authType: "oauth" | "apikey" =
    item?.authType === "apikey" ? "apikey" : "oauth"
  const open = providerId != null && item != null

  const [apiKey, setApiKey] = useState("")
  const [apiKeyError, setApiKeyError] = useState<string | null>(null)
  const [isSubmittingApiKey, setIsSubmittingApiKey] = useState(false)
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
    try {
      // Drive uses dataset to scope folder choice; others ignore it.
      const dataset = providerId === "google_drive" ? activeCompany : undefined
      const r = await connectorsApi.startOauth(providerId, dataset, returnTo)
      if (r.authorize_url) {
        window.location.href = r.authorize_url
      }
    } catch (e) {
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
        <GoogleDriveFolderPicker
          dataset={activeCompany}
          selectedFolderId={connection.config?.folder_id}
          selectedFolderName={connection.config?.folder_name}
          onSelected={onConnected}
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
      isConnecting={isConnecting}
      oauthError={oauthError}
      showCompleteOrRestart={showCompleteOrRestart}
      onClose={onClose}
      onSkipForLater={onSkipForLater}
      onConnect={() => void handleConnect()}
      onApiKeyChange={setApiKey}
      onSubmitApiKey={() => void handleSubmitApiKey()}
      onCompleteFlow={() => void handleConnect()}
      onRestartFlow={handleRestart}
    >
      {slot}
    </ConnectorConnectModalView>
  )
}
