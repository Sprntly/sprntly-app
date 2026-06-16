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

import { useCallback, useEffect, useRef, useState } from "react"
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
import type {
  ConnectorCategoryRow,
  ConnectorItemRow,
} from "../../../../types/content"
import { openOauthTab } from "../../../../lib/connectorsOauth"
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
  slack:
    "Get your Bot User OAuth Token from api.slack.com/apps → your app → Install App → Bot User OAuth Token (starts with xoxb-).",
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

  // When the user returns from authorizing in the sibling tab, pull the fresh
  // connection list so the just-connected row flips to Active without a manual
  // refresh. Gated on the in-flight flag so we don't reload on every tab focus.
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
      } else if (apiKeyConnectingItem.id === "slack") {
        await connectorsApi.connectSlackWithBotToken(apiKey)
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

  const configuringConnection =
    configuringProviderId != null
      ? (connectionByProvider.get(configuringProviderId) ?? null)
      : null

  return (
    <>
      <ConnectorsSettingsView
        categories={CONNECTOR_CATALOG}
        connectionByProvider={connectionByProvider}
        loading={loading}
        loadError={loadError}
        onConnect={onConnect}
        onConfigure={onConfigure}
        onUpload={onUpload}
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
