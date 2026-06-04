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
  /**
   * Active workspace's uuid. Nullable on purpose — the workspace may
   * still be loading when the drawer first mounts. While null, all
   * actions (Test, Disconnect, the Drive folder slot) are inert; the
   * type says null so the type system enforces the guard at every call
   * site instead of papering over the unloaded state with `?? ""`.
   */
  workspaceId: string | null
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

async function callDisconnect(
  workspaceId: string,
  providerId: string,
): Promise<void> {
  if (providerId === "google_drive") {
    await connectorsApi.disconnectGoogleDrive(workspaceId)
  } else if (providerId === "figma") {
    await connectorsApi.disconnectFigma(workspaceId)
  } else if (providerId === "github") {
    await connectorsApi.disconnectGithub(workspaceId)
  } else if (providerId === "clickup") {
    await connectorsApi.disconnectClickup(workspaceId)
  } else if (providerId === "hubspot") {
    await connectorsApi.disconnectHubspot(workspaceId)
  } else if (providerId === "fireflies") {
    await connectorsApi.disconnectFireflies(workspaceId)
  } else if (providerId === "slack") {
    await connectorsApi.disconnectSlack(workspaceId)
  } else {
    throw new Error(`Disconnect not implemented for provider: ${providerId}`)
  }
}

export function ConfigureConnectorDrawer({
  providerId,
  connection,
  workspaceId,
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
    if (!providerId || !workspaceId) return
    setIsTesting(true)
    setTestResult(null)
    try {
      const r = await connectorsApi.testConnection(workspaceId, providerId)
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
  }, [providerId, workspaceId])

  const handleDisconnect = useCallback(async () => {
    if (!providerId || !workspaceId) return
    setIsDisconnecting(true)
    setDisconnectError(null)
    try {
      await callDisconnect(workspaceId, providerId)
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
  }, [providerId, workspaceId, onDisconnected, onClose])

  // Slot: provider-specific config component. The pickers read + write
  // through workspace-scoped endpoints, so suppress them until the
  // workspace is loaded — better than mounting them and watching them
  // 422 on every request.
  let slot: React.ReactNode = null
  if (providerId === "google_drive" && workspaceId) {
    slot = (
      <GoogleDriveFolderPicker
        workspaceId={workspaceId}
        dataset={activeCompany}
        selectedFolderId={connection?.config?.folder_id}
        selectedFolderName={connection?.config?.folder_name}
        onSelected={onDisconnected /* reuse the reload callback */}
      />
    )
  } else if (providerId === "slack" && workspaceId) {
    slot = (
      <SlackChannelPicker
        workspaceId={workspaceId}
        savedChannelId={connection?.config?.channel_id as string | undefined}
        savedChannelName={connection?.config?.channel_name as string | undefined}
        onSaved={onDisconnected /* reuse the reload callback */}
      />
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
