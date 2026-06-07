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

import { useCallback, useState } from "react"
import {
  ApiError,
  apiErrorMessage,
  connectorsApi,
  type ConnectionSummary,
} from "../../lib/api"
import { CONNECTOR_CATALOG } from "../../lib/connectorsCatalog"
import type { ConnectorItemRow } from "../../types/content"
import { GoogleDriveFolderPicker } from "./GoogleDriveFolderPicker"
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

export type TestConnectionResult =
  | { kind: "ok"; accountLabel: string; testedAt: string }
  | { kind: "error"; message: string }

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
  /** Fires the "Test connection" check (commit K). */
  onTestConnection: () => void
  isTesting: boolean
  /** Latest test result, or null if not tested in this session. */
  testResult: TestConnectionResult | null
  /** Connector-specific config slot (Drive folder picker, etc). */
  children?: React.ReactNode
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
  onTestConnection,
  isTesting,
  testResult,
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
            <span
              className="drawer-icon"
              style={{ background: item.logoColor ?? undefined }}
            >
              {item.logoText ?? item.logo}
            </span>
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

          <section className="conn-config-test">
            <div className="conn-config-test-row">
              <div className="conn-config-test-label">Test connection</div>
              <button
                type="button"
                className="btn btn-sm"
                disabled={isTesting || !hasConnection}
                onClick={onTestConnection}
              >
                {isTesting ? "Testing…" : "Test now"}
              </button>
            </div>
            {testResult ? (
              <p
                className={
                  testResult.kind === "ok"
                    ? "conn-config-test-ok"
                    : "conn-config-test-err"
                }
                role={testResult.kind === "error" ? "alert" : undefined}
              >
                {testResult.kind === "ok" ? (
                  <>
                    ✓ Connection working — {testResult.accountLabel || "verified"} ·{" "}
                    {formatConnectedSince(testResult.testedAt)}
                  </>
                ) : (
                  <>✗ {testResult.message}</>
                )}
              </p>
            ) : null}
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
  const [isTesting, setIsTesting] = useState(false)
  const [testResult, setTestResult] = useState<TestConnectionResult | null>(null)

  const item = providerId ? lookupItem(providerId) : null
  const open = providerId != null && item != null

  // Reset test result when the user opens a different connector's drawer.
  // Otherwise stale "✓ Working" copy from one connector bleeds into another.
  if (testResult != null && providerId == null) {
    setTestResult(null)
  }

  const handleTest = useCallback(async () => {
    if (!providerId) return
    setIsTesting(true)
    setTestResult(null)
    try {
      const r = await connectorsApi.testConnection(providerId)
      setTestResult({
        kind: "ok",
        accountLabel: r.account_label || "",
        testedAt: r.tested_at,
      })
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? apiErrorMessage(e.status, e.body)
          : e instanceof Error
            ? e.message
            : String(e)
      setTestResult({ kind: "error", message: msg })
    } finally {
      setIsTesting(false)
    }
  }, [providerId])

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
      <GoogleDriveFolderPicker
        dataset={activeCompany}
        selectedFolderId={connection?.config?.folder_id}
        selectedFolderName={connection?.config?.folder_name}
        onSelected={onDisconnected /* reuse the reload callback */}
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
      onTestConnection={() => void handleTest()}
      isTesting={isTesting}
      testResult={testResult}
    >
      {slot}
    </ConfigureConnectorDrawerView>
  )
}
