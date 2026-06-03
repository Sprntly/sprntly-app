// DORMANT (commit A, 2026-06-01): This component is no longer rendered.
// The standalone /connectors route was deleted as part of the design-3 reset
// (see /Users/ceo/Projects/sprtnly/SETTINGS_PAGE_PLAN.md). The OAuth
// handlers, sync flow, and folder-picker mount logic here are kept on disk
// for salvage when commit D builds the new Settings → Connectors pane.
"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import { useCompany } from "../../../context/CompanyContext"
import { useContent } from "../../../context/ContentContext"
import { useNavigation } from "../../../context/NavigationContext"
import {
  ApiError,
  connectorsApi,
  type ConnectionSummary,
} from "../../../lib/api"
import {
  CONNECTOR_CATALOG,
  CONNECTOR_IDS_WITH_OAUTH,
} from "../../../lib/connectorsCatalog"
import { publicPath } from "../../../lib/public-path"
import { GoogleDriveFolderPicker } from "../../connectors/GoogleDriveFolderPicker"
import { AppLayout } from "./AppLayout"
import { IconGrid } from "../../shared/app-icons"

function formatSyncHint(conn: ConnectionSummary | undefined): string | null {
  if (!conn) return null
  if (conn.last_sync_error) return `Sync error: ${conn.last_sync_error}`
  if (conn.last_sync_at) {
    try {
      const d = new Date(conn.last_sync_at)
      return `Last sync ${d.toLocaleString()}`
    } catch {
      return `Last sync ${conn.last_sync_at}`
    }
  }
  if (!conn.config.folder_id) {
    return "Connected — choose a folder below"
  }
  const label = conn.config.folder_name ?? "folder"
  return `Syncing “${label}” — run Sync now to update Sources`
}

export function ConnectorsScreen() {
  const { activeCompany } = useCompany()
  const { setContent } = useContent()
  const { showToast } = useNavigation()

  const [connections, setConnections] = useState<ConnectionSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [disconnecting, setDisconnecting] = useState(false)
  const [syncing, setSyncing] = useState(false)

  const connectionByProvider = useMemo(() => {
    const m = new Map<string, ConnectionSummary>()
    for (const c of connections) {
      if (c.status === "active") m.set(c.provider, c)
    }
    return m
  }, [connections])

  const connectedIds = useMemo(
    () => [...connectionByProvider.keys()],
    [connectionByProvider],
  )

  const reload = useCallback(async () => {
    setLoadError(null)
    try {
      const r = await connectorsApi.list()
      setConnections(r.connections)
      setContent({
        connectorCategories: CONNECTOR_CATALOG,
        connectedConnectorIds: r.connections
          .filter((c) => c.status === "active")
          .map((c) => c.provider),
      })
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? `API ${e.status}`
          : e instanceof Error
            ? e.message
            : String(e)
      setLoadError(msg)
      setConnections([])
      setContent({
        connectorCategories: CONNECTOR_CATALOG,
        connectedConnectorIds: [],
      })
    } finally {
      setLoading(false)
    }
  }, [setContent])

  useEffect(() => {
    setLoading(true)
    void reload()
  }, [reload])

  useEffect(() => {
    if (typeof window === "undefined") return
    const params = new URLSearchParams(window.location.search)
    const connected = params.get("connected")
    if (!connected) return

    if (connected === "google_drive") {
      showToast(
        "Google Drive connected",
        "Choose which Drive folder to sync, then run Sync now.",
      )
    } else if (connected === "figma") {
      showToast(
        "Figma connected",
        "Sprntly can now read your files for design-token extraction.",
      )
    } else if (connected === "github") {
      showToast(
        "GitHub connected",
        "Install the Sprntly app on the repos you want covered.",
      )
    } else {
      showToast("Connector connected", `Provider: ${connected}`)
    }

    window.history.replaceState(null, "", publicPath("/connectors"))
    void reload()
  }, [showToast, reload])

  const connectGoogleDrive = () => {
    window.location.href = connectorsApi.googleDriveAuthorizeUrl(activeCompany)
  }

  const connectFigma = () => {
    window.location.href = connectorsApi.figmaAuthorizeUrl()
  }

  const connectGithub = () => {
    window.location.href = connectorsApi.githubAuthorizeUrl()
  }

  const driveConn = connectionByProvider.get("google_drive")

  const runSync = async () => {
    if (!driveConn?.config.folder_id) {
      showToast("Choose a folder first", "Select a Drive folder below before syncing.")
      return
    }
    setSyncing(true)
    try {
      const r = await connectorsApi.syncGoogleDrive(activeCompany)
      const n = r.synced.length
      const sk = r.skipped.length
      const err = r.errors.length
      showToast(
        n ? `Synced ${n} file${n === 1 ? "" : "s"}` : "Sync complete",
        [
          sk ? `${sk} skipped` : null,
          err ? `${err} failed` : null,
          n ? "Check Sources for imported files." : "No new files to import.",
        ]
          .filter(Boolean)
          .join(" · "),
      )
      await reload()
    } catch (e) {
      const msg =
        e instanceof ApiError ? `API ${e.status}` : e instanceof Error ? e.message : String(e)
      showToast("Sync failed", msg)
    } finally {
      setSyncing(false)
    }
  }

  const disconnectGoogleDrive = async () => {
    if (disconnecting) return
    setDisconnecting(true)
    try {
      await connectorsApi.disconnectGoogleDrive()
      showToast("Google Drive disconnected", "Tokens removed from Sprntly.")
      await reload()
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? `API ${e.status}`
          : e instanceof Error
            ? e.message
            : String(e)
      showToast("Couldn't disconnect", msg)
    } finally {
      setDisconnecting(false)
    }
  }

  const disconnectProvider = async (
    provider: "figma" | "github",
    label: string,
  ) => {
    if (disconnecting) return
    setDisconnecting(true)
    try {
      if (provider === "figma") await connectorsApi.disconnectFigma()
      else await connectorsApi.disconnectGithub()
      showToast(`${label} disconnected`, "Tokens removed from Sprntly.")
      await reload()
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? `API ${e.status}`
          : e instanceof Error
            ? e.message
            : String(e)
      showToast("Couldn't disconnect", msg)
    } finally {
      setDisconnecting(false)
    }
  }

  const onChipClick = (itemId: string) => {
    if (itemId === "google_drive") connectGoogleDrive()
    else if (itemId === "figma") connectFigma()
    else if (itemId === "github") connectGithub()
  }

  const categories = CONNECTOR_CATALOG
  const connected = new Set(connectedIds)
  const connectedCount = connectedIds.length
  const totalItems = categories.reduce((n, c) => n + c.items.length, 0)

  return (
    <AppLayout>
      <div className="conn-summary">
        <div className="conn-summary-inner">
          <div className="conn-summary-eyebrow">Signal coverage</div>
          <h1 className="conn-summary-headline">
            <span>{connectedCount} connected</span> of {totalItems} integrations in
            your catalog.
          </h1>
          {loadError ? (
            <p className="main-sub" style={{ marginTop: 8, color: "var(--neg)" }}>
              Could not load connections: {loadError}
            </p>
          ) : null}
          <div className="conn-summary-stats">
            <div className="conn-summary-stat">
              <strong className="pos">{connectedCount}</strong>Connected
            </div>
            <div className="conn-summary-stat">
              <strong>{totalItems}</strong>Available
            </div>
            <div className="conn-summary-stat">
              <strong>—</strong>Signals/week
            </div>
            <div className="conn-summary-stat">
              <strong>
                {categories.filter((c) => c.items.some((i) => connected.has(i.id))).length}{" "}
                of {categories.length}
              </strong>Categories covered
            </div>
          </div>
        </div>
        <button
          type="button"
          className="btn"
          style={{
            background: "var(--surface)",
            color: "var(--ink)",
            borderColor: "var(--surface)",
          }}
        >
          + Request connector
        </button>
      </div>

      {loading ? (
        <p className="conn-mgmt-empty" style={{ padding: "24px 20px" }}>
          Loading connections…
        </p>
      ) : null}

      {categories.map((cat) => {
        const connectedItems = cat.items.filter((i) => connected.has(i.id))
        const availableItems = cat.items.filter((i) => !connected.has(i.id))
        return (
          <div key={cat.key} className="conn-mgmt-group">
            <div className="conn-mgmt-head">
              <div className="conn-mgmt-title-row">
                <div className="conn-mgmt-icon">
                  <IconGrid size={16} />
                </div>
                <div>
                  <h3 className="conn-mgmt-title">{cat.title}</h3>
                  <p className="conn-mgmt-sub">
                    {cat.subtitle ?? "Integrations in this category."}
                  </p>
                </div>
              </div>
              <div className="conn-mgmt-status">
                <span
                  className={`conn-mgmt-badge ${connectedItems.length ? "" : "none"}`}
                >
                  {connectedItems.length} connected
                </span>
              </div>
            </div>
            <div className="conn-mgmt-body">
              {connectedItems.length > 0 ? (
                <div className="conn-mgmt-connected-list">
                  {connectedItems.map((item) => {
                    const conn = connectionByProvider.get(item.id)
                    const hint = formatSyncHint(conn)
                    const isDrive = item.id === "google_drive"
                    const accountLabel =
                      conn?.google_email ?? conn?.account_label ?? null
                    return (
                      <div key={item.id} className="conn-mgmt-connected-row">
                        <div className="conn-mgmt-connected-pill">
                          <div className="conn-logo">{item.logo}</div>
                          <span>
                            {item.name}
                            {accountLabel ? (
                              <span className="conn-mgmt-email">
                                {" "}
                                · {accountLabel}
                              </span>
                            ) : null}
                          </span>
                        </div>
                        {hint ? (
                          <span className="conn-mgmt-sync-hint">{hint}</span>
                        ) : null}
                        {isDrive ? (
                          <>
                            <button
                              type="button"
                              className="btn btn-sm btn-primary"
                              disabled={syncing}
                              onClick={() => void runSync()}
                            >
                              {syncing ? "Syncing…" : "Sync now"}
                            </button>
                            <button
                              type="button"
                              className="btn btn-sm conn-mgmt-disconnect"
                              disabled={disconnecting}
                              onClick={() => void disconnectGoogleDrive()}
                            >
                              Disconnect
                            </button>
                          </>
                        ) : item.id === "figma" || item.id === "github" ? (
                          <button
                            type="button"
                            className="btn btn-sm conn-mgmt-disconnect"
                            disabled={disconnecting}
                            onClick={() =>
                              void disconnectProvider(
                                item.id as "figma" | "github",
                                item.name,
                              )
                            }
                          >
                            Disconnect
                          </button>
                        ) : null}
                      </div>
                    )
                  })}
                  {connectedItems.some((i) => i.id === "google_drive") && driveConn ? (
                    <GoogleDriveFolderPicker
                      dataset={activeCompany}
                      selectedFolderId={driveConn.config.folder_id}
                      selectedFolderName={driveConn.config.folder_name}
                      onSelected={() => {
                        showToast("Folder connected", "Run Sync now to import files.")
                        void reload()
                      }}
                    />
                  ) : null}
                </div>
              ) : null}
              {availableItems.length > 0 ? (
                <div className="conn-mgmt-available">
                  {availableItems.map((item) => {
                    const canConnect = CONNECTOR_IDS_WITH_OAUTH.has(item.id)
                    return (
                      <button
                        key={item.id}
                        type="button"
                        className="conn-mgmt-available-chip"
                        disabled={!canConnect}
                        title={
                          canConnect
                            ? item.id === "google_drive"
                              ? `Connect Drive for dataset “${activeCompany}”`
                              : "Connect"
                            : "Coming soon"
                        }
                        onClick={() => onChipClick(item.id)}
                      >
                        <div className="conn-logo">{item.logo}</div>
                        {item.name}
                      </button>
                    )
                  })}
                </div>
              ) : connectedItems.length === 0 ? (
                <div className="conn-mgmt-empty">No integrations listed for this group.</div>
              ) : null}
            </div>
          </div>
        )
      })}
    </AppLayout>
  )
}
