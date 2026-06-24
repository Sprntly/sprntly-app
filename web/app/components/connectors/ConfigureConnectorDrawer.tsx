/**
 * Per-connector "Configure" drawer (commit E).
 *
 * Mounted from ConnectorsSettings when the user clicks Configure on an
 * Active connector row. Hosts:
 *   - The connector's name, account label, and connected-since timestamp
 *   - A children slot for connector-specific config (the Drive folder
 *     picker is the only one wired today; others get a placeholder)
 *   - A Disconnect button (soft — revokes the OAuth token; ingested
 *     data stays on disk)
 *
 * Uses the existing right-side drawer styles from globals.css
 * (.drawer-overlay / .drawer / .drawer-head / .drawer-body / .drawer-foot).
 *
 * The View is pure — props in, JSX out — and unit-tested via
 * renderToStaticMarkup per the design-agent test convention. The
 * default-exported ConfigureConnectorDrawer wraps the View with hook
 * wiring (data fetch, disconnect call, slot dispatch by provider).
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
import { GithubInstallsSlot } from "./GithubInstallsSlot"
import { GoogleDrivePicker } from "./GoogleDrivePicker"
import { SlackChannelPicker } from "./SlackChannelPicker"

// ─────────────────────── Slack Sync Button ─────────────────────

function SlackSyncButton({
  dataset,
  onSynced,
}: {
  dataset: string
  onSynced: () => void
}) {
  const [syncing, setSyncing] = useState(false)
  const [result, setResult] = useState<{
    total_synced: number
    channels_count: number
    messages_count: number
    threads_count: number
    errors: string[]
  } | null>(null)
  const [error, setError] = useState<string | null>(null)

  const handleSync = useCallback(async () => {
    setSyncing(true)
    setError(null)
    setResult(null)
    try {
      const res = await connectorsApi.syncSlack(dataset)
      setResult(res)
      onSynced()
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? apiErrorMessage(e.status, e.body)
          : e instanceof Error
            ? e.message
            : String(e)
      setError(msg)
    } finally {
      setSyncing(false)
    }
  }, [dataset, onSynced])

  return (
    <div style={{ padding: "12px 0" }}>
      <p style={{ fontSize: 13, color: "#888", marginBottom: 8 }}>
        Sync channels, messages, and threads from Slack into the knowledge base.
      </p>
      <button
        onClick={() => void handleSync()}
        disabled={syncing}
        style={{
          padding: "8px 16px",
          borderRadius: 6,
          border: "1px solid #ccc",
          background: syncing ? "#eee" : "#fff",
          cursor: syncing ? "not-allowed" : "pointer",
          fontSize: 13,
        }}
      >
        {syncing ? "Syncing…" : "Sync Slack Data"}
      </button>
      {result && (
        <p style={{ fontSize: 12, color: "#2a7", marginTop: 8 }}>
          Synced {result.total_synced} items ({result.channels_count} channels,{" "}
          {result.messages_count} messages, {result.threads_count} thread replies)
          {result.errors.length > 0 && (
            <span style={{ color: "#c33" }}> — {result.errors.join("; ")}</span>
          )}
        </p>
      )}
      {error && (
        <p style={{ fontSize: 12, color: "#c33", marginTop: 8 }}>{error}</p>
      )}
    </div>
  )
}

// ─────────────────────── HubSpot Sync Button ────────────────────

function HubSpotSyncButton({
  dataset,
  onSynced,
}: {
  dataset: string
  onSynced: () => void
}) {
  const [syncing, setSyncing] = useState(false)
  const [result, setResult] = useState<{
    total_synced: number
    contacts_count: number
    companies_count: number
    deals_count: number
    errors: string[]
  } | null>(null)
  const [error, setError] = useState<string | null>(null)

  const handleSync = useCallback(async () => {
    setSyncing(true)
    setError(null)
    setResult(null)
    try {
      const res = await connectorsApi.syncHubspot(dataset)
      setResult(res)
      onSynced()
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? apiErrorMessage(e.status, e.body)
          : e instanceof Error
            ? e.message
            : String(e)
      setError(msg)
    } finally {
      setSyncing(false)
    }
  }, [dataset, onSynced])

  return (
    <div style={{ padding: "12px 0" }}>
      <p style={{ fontSize: 13, color: "#888", marginBottom: 8 }}>
        Sync contacts, companies, and deals from HubSpot into the knowledge base.
      </p>
      <button
        onClick={() => void handleSync()}
        disabled={syncing}
        style={{
          padding: "8px 16px",
          borderRadius: 6,
          border: "1px solid #ccc",
          background: syncing ? "#eee" : "#fff",
          cursor: syncing ? "not-allowed" : "pointer",
          fontSize: 13,
        }}
      >
        {syncing ? "Syncing…" : "Sync CRM Data"}
      </button>
      {result && (
        <p style={{ fontSize: 12, color: "#2a7", marginTop: 8 }}>
          Synced {result.total_synced} records ({result.contacts_count} contacts,{" "}
          {result.companies_count} companies, {result.deals_count} deals)
          {result.errors.length > 0 && (
            <span style={{ color: "#c33" }}> — {result.errors.join("; ")}</span>
          )}
        </p>
      )}
      {error && (
        <p style={{ fontSize: 12, color: "#c33", marginTop: 8 }}>{error}</p>
      )}
    </div>
  )
}

// ─────────────────────────── Pure View ───────────────────────────

/** Live connection status, checked automatically when the drawer opens.
 * `checking` while the health probe is in flight; `connected` once it
 * succeeds; `disconnected` if there's no connection or the probe fails. */
export type ConnectionStatus =
  | { kind: "checking" }
  | { kind: "connected"; accountLabel?: string }
  | { kind: "disconnected"; message?: string }

export type ConfigureConnectorDrawerViewProps = {
  open: boolean
  /** Null when no connector is being configured — drawer renders nothing. */
  item: ConnectorItemRow | null
  /** Live connection record. May be null even when item is set (mid-load). */
  connection: ConnectionSummary | null
  onClose: () => void
  onDisconnect: () => void
  isDisconnecting: boolean
  /** Optional inline error from the disconnect call. */
  disconnectError?: string | null
  /** Auto-checked connection status (null before the first probe resolves). */
  status: ConnectionStatus | null
  /** Connector-specific config slot (Drive file picker, etc). */
  children?: React.ReactNode
}

function StatusPill({ status }: { status: ConnectionStatus | null }) {
  // No probe yet (drawer just opened) reads as "checking" — never a flash of
  // "Disconnected" for a connection that's actually fine.
  const kind = status?.kind ?? "checking"
  const label =
    kind === "connected"
      ? "Connected"
      : kind === "disconnected"
        ? "Disconnected"
        : "Checking…"
  const detail =
    status?.kind === "connected"
      ? status.accountLabel
      : status?.kind === "disconnected"
        ? status.message
        : undefined
  return (
    <div className={`conn-config-status conn-config-status--${kind}`} role="status">
      <span className="conn-config-status-dot" aria-hidden />
      <span className="conn-config-status-label">{label}</span>
      {detail ? <span className="conn-config-status-detail">{detail}</span> : null}
    </div>
  )
}

function formatConnectedSince(isoLike: string | null | undefined): string {
  if (!isoLike) return "—"
  try {
    const d = new Date(isoLike)
    return d.toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    })
  } catch {
    return isoLike
  }
}

export function ConfigureConnectorDrawerView({
  open,
  item,
  connection,
  onClose,
  onDisconnect,
  isDisconnecting,
  disconnectError,
  status,
  children,
}: ConfigureConnectorDrawerViewProps) {
  if (!item) return null

  const accountLabel =
    connection?.account_label ?? connection?.google_email ?? null
  const connectedSince = formatConnectedSince(connection?.created_at)
  const hasConnection = connection != null

  return (
    <>
      <div
        className={`drawer-overlay${open ? " open" : ""}`}
        onClick={onClose}
        aria-hidden
      />
      <aside
        className={`drawer${open ? " open" : ""}`}
        role="dialog"
        aria-label={`Configure ${item.name}`}
        aria-hidden={!open}
      >
        <div className="drawer-head">
          <h2 className="drawer-title">
            <ConnectorLogo item={item} className="drawer-icon" />
            {item.name}
          </h2>
          <button
            type="button"
            className="drawer-close"
            onClick={onClose}
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        <div className="drawer-body">
          <StatusPill status={status} />

          <section className="conn-config-meta">
            <div className="conn-config-meta-row">
              <span className="conn-config-meta-k">Account</span>
              <span className="conn-config-meta-v">
                {hasConnection
                  ? (accountLabel ?? "Connected")
                  : "No connection — loading…"}
              </span>
            </div>
            <div className="conn-config-meta-row">
              <span className="conn-config-meta-k">Connected since</span>
              <span className="conn-config-meta-v">{connectedSince}</span>
            </div>
          </section>

          {children ? (
            <section className="conn-config-slot">{children}</section>
          ) : (
            <p className="conn-config-placeholder">
              No additional configuration for this connector yet.
            </p>
          )}
        </div>

        <div className="drawer-foot">
          {disconnectError ? (
            <p className="settings-msg settings-msg-error" role="alert">
              {disconnectError}
            </p>
          ) : (
            <span />
          )}
          <button
            type="button"
            className="btn btn-sm conn-mgmt-disconnect"
            disabled={isDisconnecting || !hasConnection}
            onClick={onDisconnect}
          >
            {isDisconnecting ? "Disconnecting…" : "Disconnect"}
          </button>
        </div>
      </aside>
    </>
  )
}

// ───────────────────── Hooks-wired wrapper ─────────────────────

type ConfigureConnectorDrawerProps = {
  providerId: string | null
  connection: ConnectionSummary | null
  activeCompany: string
  onClose: () => void
  /** Fired after a successful disconnect so the parent can reload connections. */
  onDisconnected: () => void
}

function lookupItem(providerId: string): ConnectorItemRow | null {
  for (const cat of CONNECTOR_CATALOG) {
    const found = cat.items.find((i) => i.id === providerId)
    if (found) return found
  }
  return null
}

async function callDisconnect(providerId: string): Promise<void> {
  if (providerId === "google_drive") {
    await connectorsApi.disconnectGoogleDrive()
  } else if (providerId === "figma") {
    await connectorsApi.disconnectFigma()
  } else if (providerId === "github") {
    await connectorsApi.disconnectGithub()
  } else if (providerId === "clickup") {
    await connectorsApi.disconnectClickup()
  } else if (providerId === "hubspot") {
    await connectorsApi.disconnectHubspot()
  } else if (providerId === "slack") {
    await connectorsApi.disconnectSlack()
  } else if (providerId === "fireflies") {
    await connectorsApi.disconnectFireflies()
  } else if (providerId === "slack") {
    await connectorsApi.disconnectSlack()
  } else {
    throw new Error(`Disconnect not implemented for provider: ${providerId}`)
  }
}

export function ConfigureConnectorDrawer({
  providerId,
  connection,
  activeCompany,
  onClose,
  onDisconnected,
}: ConfigureConnectorDrawerProps) {
  const [isDisconnecting, setIsDisconnecting] = useState(false)
  const [disconnectError, setDisconnectError] = useState<string | null>(null)
  const [status, setStatus] = useState<ConnectionStatus | null>(null)

  const item = providerId ? lookupItem(providerId) : null
  const open = providerId != null && item != null
  const connectionId = connection?.id ?? null

  // Auto-probe the connection whenever the drawer opens on a connector (or the
  // resolved connection changes). Replaces the old manual "Test connection"
  // button — the status badge at the top now reflects a live health check with
  // no click. No connection row → immediately "disconnected"; otherwise show
  // "checking" while the probe runs, then connected/disconnected by its result.
  useEffect(() => {
    if (!providerId) {
      setStatus(null)
      return
    }
    if (!connectionId) {
      setStatus({ kind: "disconnected", message: "Not connected" })
      return
    }
    let cancelled = false
    setStatus({ kind: "checking" })
    void (async () => {
      try {
        const r = await connectorsApi.testConnection(providerId)
        if (!cancelled) {
          setStatus({ kind: "connected", accountLabel: r.account_label || undefined })
        }
      } catch (e) {
        if (cancelled) return
        const msg =
          e instanceof ApiError
            ? apiErrorMessage(e.status, e.body)
            : e instanceof Error
              ? e.message
              : String(e)
        setStatus({ kind: "disconnected", message: msg })
      }
    })()
    return () => {
      cancelled = true
    }
  }, [providerId, connectionId])

  const handleDisconnect = useCallback(async () => {
    if (!providerId) return
    setIsDisconnecting(true)
    setDisconnectError(null)
    try {
      await callDisconnect(providerId)
      onDisconnected()
      onClose()
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? apiErrorMessage(e.status, e.body)
          : e instanceof Error
            ? e.message
            : String(e)
      setDisconnectError(msg)
    } finally {
      setIsDisconnecting(false)
    }
  }, [providerId, onDisconnected, onClose])

  // Slot: provider-specific config component. The pickers fetch with
  // the Bearer-only API client — require_company resolves the tenant
  // server-side, so no workspaceId prop is needed here.
  let slot: React.ReactNode = null
  if (providerId === "google_drive") {
    slot = (
      <GoogleDrivePicker
        dataset={activeCompany}
        savedFiles={connection?.config?.files}
        onSaved={onDisconnected /* reuse the reload callback */}
      />
    )
  } else if (providerId === "hubspot") {
    slot = (
      <HubSpotSyncButton
        dataset={activeCompany}
        onSynced={onDisconnected /* reuse the reload callback */}
      />
    )
  } else if (providerId === "slack") {
    slot = (
      <>
        <SlackChannelPicker
          savedChannelId={connection?.config?.channel_id as string | undefined}
          savedChannelName={connection?.config?.channel_name as string | undefined}
          onSaved={onDisconnected /* reuse the reload callback */}
        />
        <SlackSyncButton
          dataset={activeCompany}
          onSynced={onDisconnected /* reuse the reload callback */}
        />
      </>
    )
  } else if (providerId === "github") {
    slot = <GithubInstallsSlot onChanged={onDisconnected} />
  }

  return (
    <ConfigureConnectorDrawerView
      open={open}
      item={item}
      connection={connection}
      onClose={onClose}
      onDisconnect={() => void handleDisconnect()}
      isDisconnecting={isDisconnecting}
      disconnectError={disconnectError}
      status={status}
    >
      {slot}
    </ConfigureConnectorDrawerView>
  )
}

