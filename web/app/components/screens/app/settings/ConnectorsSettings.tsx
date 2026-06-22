/**
 * Settings → Connectors pane (commit D).
 *
 * Renders the connector grid grouped by the 8 CONNECTOR_CATALOG categories.
 * Only connectors with a working integration (OAuth / API key) are shown
 * (`connectableCatalog`) — "Coming soon" connectors are hidden so we don't
 * surface things the user can't use; categories are kept even when empty so
 * their file-upload strip remains. Connection state (Active vs Off) and the
 * per-row "Configure"/"Connect" action come from `connectorsApi.list()`.
 *
 * The exported View component is pure (no hooks, no IO) and unit-tested
 * via renderToStaticMarkup per the design-agent test convention. The
 * default-exported ConnectorsSettings hooks-component wires state and
 * navigation callbacks into the View.
 */
"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { useCompany } from "../../../../context/CompanyContext"
import { useContent } from "../../../../context/ContentContext"
import { useNavigation } from "../../../../context/NavigationContext"
import {
  CONNECTOR_CATALOG,
  CONNECTOR_IDS_WITH_OAUTH,
  connectableCatalog,
} from "../../../../lib/connectorsCatalog"
import {
  companiesApi,
  connectorsApi,
  sourcesApi,
  type ConnectionSummary,
  type SourceFile,
} from "../../../../lib/api"
import {
  getConnectorRowState,
} from "../../../../lib/connectorRowState"
import {
  formatRelativeDate,
  humanizeBytes,
  iconForKind,
  truncateFilename,
} from "../../../../lib/sources-helpers"
import type {
  ConnectorCategoryRow,
  ConnectorItemRow,
} from "../../../../types/content"
import { openOauthTab } from "../../../../lib/connectorsOauth"
import { useConnectorConnectedSignal } from "../../../../lib/useConnectorConnectedSignal"
import { ApiKeyPromptModal } from "../../../connectors/ApiKeyPromptModal"
import { ConfigureConnectorDrawer } from "../../../connectors/ConfigureConnectorDrawer"

/**
 * Per-connector help text shown in the API-key modal. Keep it short and
 * point at the provider's own docs page where the key lives. Falls back
 * to a generic "look in your account settings" if not listed.
 */
const APIKEY_HELP: Record<string, string> = {
  fireflies:
    "Get your key from fireflies.ai → Settings → Integrations → Fireflies API.",
}

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
  /**
   * All files uploaded to the active company. The backend stores uploads at
   * the company level with no per-category attribution, so this is a single
   * company-wide list rendered once (not filtered per category).
   */
  files: SourceFile[]
}

export function ConnectorsSettingsView({
  categories,
  connectionByProvider,
  loading,
  loadError,
  onConnect,
  onConfigure,
  onUpload,
  files,
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

      {files.length > 0 ? (
        <div className="set-block sp-conn-files">
          <div className="set-block-h">
            <div className="set-block-t">
              Uploaded files
              <span className="set-block-s-inline">
                {"  ·  "}
                {files.length} file{files.length === 1 ? "" : "s"} across all
                categories
              </span>
            </div>
          </div>
          <ul className="src-list">
            {files.map((f) => (
              <li key={f.filename} className="src-row">
                <span className="src-row-icon" aria-hidden>
                  {iconForKind(f.kind)}
                </span>
                <span className="src-row-name" title={f.filename}>
                  {truncateFilename(f.filename, 40)}
                </span>
                <span className="src-kind-chip">{f.kind.toUpperCase()}</span>
                <span className="src-meta">{humanizeBytes(f.size_bytes)}</span>
                <span className="src-meta">{formatRelativeDate(f.added_at)}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  )
}

// ───────────────────── Hooks-wired wrapper ─────────────────────

export function ConnectorsSettings() {
  const { activeCompany } = useCompany()
  const { setContent } = useContent()
  const { showToast } = useNavigation()

  const [connections, setConnections] = useState<ConnectionSummary[]>([])
  const [files, setFiles] = useState<SourceFile[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [configuringProviderId, setConfiguringProviderId] = useState<
    string | null
  >(null)
  const [apiKeyConnectingItem, setApiKeyConnectingItem] =
    useState<ConnectorItemRow | null>(null)
  // Set when we send the user to a provider's OAuth page in a sibling tab —
  // tells the visibility listener to refresh connections when they switch back.
  const oauthInFlight = useRef(false)

  // Connector routes resolve the active company entirely from the
  // Supabase JWT (require_company), so the frontend doesn't need to
  // hold a tenant id — just call and let 401/403 surface as errors.
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

  // Company-wide uploaded-files list shown in the connectors pane. The backend
  // stores uploads at the company level with no per-category attribution, so
  // this is one shared list (mirrors SourcesScreen.reloadFiles).
  const reloadFiles = useCallback(async () => {
    if (!activeCompany) {
      setFiles([])
      return
    }
    try {
      const r = await sourcesApi.list(activeCompany)
      setFiles(r.files)
    } catch {
      // Non-fatal: the connectors grid still works without the file list.
      setFiles([])
    }
  }, [activeCompany])

  useEffect(() => {
    void reloadFiles()
  }, [reloadFiles])

  // The OAuth tab signals back via BroadcastChannel / localStorage the moment
  // a connector connects (see /connectors/return), so the just-connected row
  // flips to Active immediately — no tab switch needed.
  useConnectorConnectedSignal(() => {
    oauthInFlight.current = false
    void reload()
  })

  // Belt-and-suspenders fallback: when the user returns from authorizing in
  // the sibling tab, pull the fresh connection list. Gated on the in-flight
  // flag so we don't reload on every tab focus.
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible" && oauthInFlight.current) {
        oauthInFlight.current = false
        void reload()
      }
    }
    document.addEventListener("visibilitychange", onVisible)
    return () => document.removeEventListener("visibilitychange", onVisible)
  }, [reload])

  const connectionByProvider = new Map<string, ConnectionSummary>()
  for (const c of connections) {
    connectionByProvider.set(c.provider, c)
  }

  // Settings shows only connectors with a working integration (OAuth / API
  // key), plus any provider that already has a live connection — so we never
  // surface "Coming soon" rows the user can't act on. Categories (and their
  // upload strips) are preserved even when they end up empty.
  const displayedCategories = connectableCatalog(
    new Set(connectionByProvider.keys()),
  )

  const onConnect = useCallback(
    async (providerId: string) => {
      // Find the catalog row so we know which auth flow to take.
      const item = CONNECTOR_CATALOG
        .flatMap((c) => c.items)
        .find((i) => i.id === providerId)

      if (item?.authType === "apikey") {
        // Open the API-key paste modal instead of an OAuth redirect.
        setApiKeyConnectingItem(item)
        return
      }

      if (!CONNECTOR_IDS_WITH_OAUTH.has(providerId)) return
      // Open the provider in a new tab so the user keeps their place in
      // Settings. Pre-open synchronously (before the startOauth await) so the
      // popup blocker treats it as part of the click gesture; mark the connect
      // in flight so we reload connections when the user switches back.
      const oauthTab = openOauthTab()
      oauthInFlight.current = true
      // Go through the fetch-then-navigate path so the auth check runs
      // with the Supabase Bearer header before we hand control to the
      // browser's URL bar.
      try {
        const dataset =
          providerId === "google_drive" ? activeCompany : undefined
        const r = await connectorsApi.startOauth(providerId, dataset)
        if (r.authorize_url) {
          oauthTab.finish(r.authorize_url)
        } else {
          oauthTab.abort()
          oauthInFlight.current = false
        }
      } catch (e) {
        oauthTab.abort()
        oauthInFlight.current = false
        const msg = e instanceof Error ? e.message : String(e)
        setLoadError(`Could not start ${providerId} connect: ${msg}`)
      }
    },
    [activeCompany],
  )

  const handleApiKeyConnect = useCallback(
    async (apiKey: string) => {
      if (!apiKeyConnectingItem) return
      if (apiKeyConnectingItem.id === "fireflies") {
        await connectorsApi.connectFirefliesWithApiKey(apiKey)
        await reload()
      } else {
        throw new Error(
          `API-key connect not wired for provider: ${apiKeyConnectingItem.id}`,
        )
      }
    },
    [apiKeyConnectingItem, reload],
  )

  const onConfigure = useCallback((providerId: string) => {
    setConfiguringProviderId(providerId)
  }, [])

  const onUpload = useCallback(
    async (categoryKey: string, picked: FileList) => {
      const list = Array.from(picked)
      if (list.length === 0) return
      try {
        const r = await companiesApi.uploadFiles(activeCompany, list)
        // Refresh the company-wide uploaded-files list so the new file shows
        // up in the connectors pane immediately.
        await reloadFiles()
        if (r.ingested.length > 0) {
          const title =
            r.ingested.length === 1
              ? `${r.ingested[0].filename} uploaded`
              : `${r.ingested.length} files uploaded`
          showToast(title, "Added to your sources.")
        }
        if (r.errors.length > 0) {
          showToast(
            "Some files failed",
            r.errors.map((e) => `${e.filename}: ${e.error}`).join("; "),
          )
        }
      } catch (e) {
        if (typeof window !== "undefined") {
          window.console.error(
            "[connectors] Manual upload failed for",
            categoryKey,
            e,
          )
        }
        const msg = e instanceof Error ? e.message : String(e)
        showToast("Upload failed", msg)
      }
    },
    [activeCompany, reloadFiles, showToast],
  )

  const configuringConnection =
    configuringProviderId != null
      ? (connectionByProvider.get(configuringProviderId) ?? null)
      : null

  return (
    <>
      <ConnectorsSettingsView
        categories={displayedCategories}
        connectionByProvider={connectionByProvider}
        loading={loading}
        loadError={loadError}
        onConnect={onConnect}
        onConfigure={onConfigure}
        onUpload={onUpload}
        files={files}
      />
      <ConfigureConnectorDrawer
        providerId={configuringProviderId}
        connection={configuringConnection}
        activeCompany={activeCompany}
        onClose={() => setConfiguringProviderId(null)}
        onDisconnected={() => void reload()}
      />
      <ApiKeyPromptModal
        open={apiKeyConnectingItem != null}
        connectorName={apiKeyConnectingItem?.name ?? ""}
        helpText={
          apiKeyConnectingItem
            ? APIKEY_HELP[apiKeyConnectingItem.id] ?? null
            : null
        }
        onConnect={handleApiKeyConnect}
        onClose={() => setApiKeyConnectingItem(null)}
      />
    </>
  )
}
