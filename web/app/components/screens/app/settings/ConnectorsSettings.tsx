/**
 * Settings → Connectors pane (commit D).
 *
 * Renders the 8-category × 29-connector grid from CONNECTOR_CATALOG per
 * sprntly_Design-3 (Sprntly.html lines 2266-2333). Connection state
 * (Active vs Off) and the per-row "Configure"/"Connect" action come from
 * `connectorsApi.list()`.
 *
 * The exported View component is pure (no hooks, no IO) and unit-tested
 * via renderToStaticMarkup per the design-agent test convention. The
 * default-exported ConnectorsSettings hooks-component wires state and
 * navigation callbacks into the View.
 */
"use client"

import { useCallback, useEffect, useState } from "react"
import { useCompany } from "../../../../context/CompanyContext"
import { useContent } from "../../../../context/ContentContext"
import {
  CONNECTOR_CATALOG,
  CONNECTOR_IDS_WITH_OAUTH,
} from "../../../../lib/connectorsCatalog"
import {
  companiesApi,
  connectorsApi,
  type ConnectionSummary,
} from "../../../../lib/api"
import {
  getConnectorRowState,
} from "../../../../lib/connectorRowState"
import type { ConnectorCategoryRow } from "../../../../types/content"

// ─────────────────────────── Pure View ───────────────────────────

export type ConnectorsSettingsViewProps = {
  categories: ConnectorCategoryRow[]
  /** Lookup keyed by provider id. Missing entry = not connected. */
  connectionByProvider: Map<string, ConnectionSummary>
  /** True while the initial connections list is loading. */
  loading: boolean
  /** Inline error from the connections-list fetch, or null. */
  loadError: string | null
  /** Fired when a "Connect" link is clicked (only for OAuth-supported providers). */
  onConnect: (providerId: string) => void
  /**
   * Fired when "Configure" is clicked on an Active connector. Commit D
   * renders a placeholder behavior; commit E mounts the real drawer.
   */
  onConfigure: (providerId: string) => void
  /** Fired when a category's upload strip receives one or more files. */
  onUpload: (categoryKey: string, files: FileList) => void
}

export function ConnectorsSettingsView({
  categories,
  connectionByProvider,
  loading,
  loadError,
  onConnect,
  onConfigure,
  onUpload,
}: ConnectorsSettingsViewProps) {
  return (
    <div className="set-pane sp-connectors">
      <div className="set-h">Connectors</div>
      <div className="set-sub">
        Every source feeding your agents, grouped by category. Connect a tool
        or upload files directly to any category.
      </div>

      {loadError ? (
        <p className="settings-msg settings-msg-error" role="alert">
          Could not load connections: {loadError}
        </p>
      ) : null}
      {loading ? <p className="settings-loading">Loading connectors…</p> : null}

      {categories.map((cat) => (
        <div key={cat.key} className="set-block">
          <div className="set-block-h">
            <div className="set-block-t">
              {cat.title}
              {cat.subLabel ? (
                <span className="set-block-s-inline">  ·  {cat.subLabel}</span>
              ) : null}
            </div>
          </div>

          {cat.items.map((item) => {
            const conn = connectionByProvider.get(item.id) ?? null
            const state = getConnectorRowState(item, conn)
            return (
              <div key={item.id} className="set-conn-row">
                <div
                  className="logo"
                  style={{ background: item.logoColor ?? "#444" }}
                >
                  {item.logoText ?? item.logo}
                </div>
                <div className="nm">
                  <div className="t">{item.name}</div>
                  <div className="s">{state.statsString}</div>
                </div>
                <span className={`st ${state.status === "active" ? "on" : "off"}`}>
                  {state.status === "active" ? "Active" : "Off"}
                </span>
                <button
                  type="button"
                  className="ac"
                  disabled={!state.canClick}
                  title={
                    state.canClick
                      ? undefined
                      : "Coming soon — no integration available yet"
                  }
                  onClick={() => {
                    if (!state.canClick) return
                    if (state.actionLabel === "Configure") onConfigure(item.id)
                    else if (state.actionLabel === "Connect") onConnect(item.id)
                  }}
                >
                  {state.actionLabel}
                </button>
              </div>
            )
          })}

          <label
            className="set-conn-upload"
            title={`Upload a file to the ${cat.title} category`}
          >
            <i className="ti ti-cloud-upload" aria-hidden />
            Upload {cat.title.toLowerCase()} files
            <span className="muted">{cat.uploadAccept ?? ""}</span>
            <input
              type="file"
              multiple
              accept={(cat.uploadExtensions ?? []).join(",")}
              style={{ display: "none" }}
              onChange={(e) => {
                if (e.target.files && e.target.files.length > 0) {
                  onUpload(cat.key, e.target.files)
                  // Reset so the same file can be picked again after a failed run.
                  e.target.value = ""
                }
              }}
            />
          </label>
        </div>
      ))}
    </div>
  )
}

// ───────────────────── Hooks-wired wrapper ─────────────────────

export function ConnectorsSettings() {
  const { activeCompany } = useCompany()
  const { setContent } = useContent()

  const [connections, setConnections] = useState<ConnectionSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

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
      const msg = e instanceof Error ? e.message : String(e)
      setLoadError(msg)
    } finally {
      setLoading(false)
    }
  }, [setContent])

  useEffect(() => {
    setLoading(true)
    void reload()
  }, [reload])

  const connectionByProvider = new Map<string, ConnectionSummary>()
  for (const c of connections) {
    connectionByProvider.set(c.provider, c)
  }

  const onConnect = useCallback(
    (providerId: string) => {
      if (!CONNECTOR_IDS_WITH_OAUTH.has(providerId)) return
      if (providerId === "google_drive") {
        window.location.href = connectorsApi.googleDriveAuthorizeUrl(activeCompany)
      } else if (providerId === "figma") {
        window.location.href = connectorsApi.figmaAuthorizeUrl()
      } else if (providerId === "github") {
        window.location.href = connectorsApi.githubAuthorizeUrl()
      }
    },
    [activeCompany],
  )

  const onConfigure = useCallback((providerId: string) => {
    // Commit E mounts the real drawer. For now: a console marker so the
    // click is observably wired without changing layout.
    if (typeof window !== "undefined") {
      window.console.info("[connectors] Configure clicked for", providerId)
    }
  }, [])

  const onUpload = useCallback(
    async (categoryKey: string, files: FileList) => {
      try {
        await companiesApi.uploadFiles(activeCompany, Array.from(files))
        // No toast wiring in this commit — the user sees the file picker
        // close and (later) the file appear in /sources. Real success/
        // error toasts ride on top of the future shared toast system.
      } catch (e) {
        if (typeof window !== "undefined") {
          window.console.error(
            "[connectors] Manual upload failed for",
            categoryKey,
            e,
          )
        }
      }
    },
    [activeCompany],
  )

  return (
    <ConnectorsSettingsView
      categories={CONNECTOR_CATALOG}
      connectionByProvider={connectionByProvider}
      loading={loading}
      loadError={loadError}
      onConnect={onConnect}
      onConfigure={onConfigure}
      onUpload={onUpload}
    />
  )
}
