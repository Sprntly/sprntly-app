/**
 * Settings → Connectors pane.
 *
 * Renders connectors GROUPED BY CATEGORY — one card per catalog category
 * (Analytics · required, Project Management, …), each with its connector
 * rows and a per-category upload strip at the card's foot. Only connectors
 * with a working integration (OAuth / API key) are shown
 * (`connectableCatalog`) — "Coming soon" connectors are hidden so we don't
 * surface things the user can't use.
 * Each row shows the connector's real brand logo (a locally bundled SVG via
 * the shared ConnectorLogo), falling back to a single-letter glyph if it
 * can't load.
 * Connection state (Active vs Off) and the per-row "Configure"/"Connect"
 * action come from `connectorsApi.list()`.
 *
 * The exported View component is pure (no hooks, no IO) and unit-tested
 * via renderToStaticMarkup per the design-agent test convention. The
 * default-exported ConnectorsSettings hooks-component wires state and
 * navigation callbacks into the View.
 */
"use client"

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react"
import { useCompany } from "../../../../context/CompanyContext"
import { useContent } from "../../../../context/ContentContext"
import { useNavigation } from "../../../../context/NavigationContext"
import {
  CONNECTOR_CATALOG,
  CONNECTOR_IDS_WITH_OAUTH,
  CONNECTOR_TYPE_LABELS,
  connectableCatalog,
} from "../../../../lib/connectorsCatalog"
import {
  ApiError,
  briefApi,
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
  UPLOAD_ACCEPT_HINT,
  UPLOAD_EXTENSIONS,
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
import { notifyBriefRegenerating } from "../../../../lib/useBriefHydration"
import { ApiKeyPromptModal } from "../../../connectors/ApiKeyPromptModal"
import {
  CredentialsPromptModal,
  type CredentialsValues,
} from "../../../connectors/CredentialsPromptModal"
import { ConfigureConnectorDrawer } from "../../../connectors/ConfigureConnectorDrawer"
import { ConnectorLogo } from "../../../connectors/ConnectorLogo"

/**
 * Provider page (keyed by connector id) where the user can view and copy
 * their API key. Rather than telling them to hunt through menus, the modal
 * links straight here. Omit a connector to render no help link.
 */
const APIKEY_PAGE_URL: Record<string, string> = {
  fireflies: "https://app.fireflies.ai/integrations/custom/fireflies",
}

/** Builds the "open your … API settings" help copy for the api-key modal. */
export function apiKeyHelp(connectorId: string, connectorName: string): ReactNode {
  const url = APIKEY_PAGE_URL[connectorId]
  if (!url) return null
  return (
    <>
      Open your{" "}
      <a href={url} target="_blank" rel="noopener noreferrer">
        {connectorName} API settings
      </a>
      {" "}and copy your API key.
    </>
  )
}

/**
 * Friendly message shown when a non-admin tries to connect an org-wide
 * source (Google Docs/Drive, GitHub, etc.). The backend correctly returns
 * 403 with "Only admins can manage org-wide connectors" — surfacing that as
 * the raw "Could not start <provider> connect: …" string looks like a bug.
 * Map the admin gate to a clear, actionable line and fall back to the raw
 * (already-readable) error for everything else.
 */
export const ADMIN_GATE_CONNECT_MESSAGE =
  "Only a workspace admin can connect org-wide sources like Google Drive. " +
  "Ask an admin to set this up."

/** True when an error is the org-connector admin gate (403 + its message). */
export function isAdminGateError(err: unknown): boolean {
  if (err instanceof ApiError && err.status === 403) return true
  const msg = err instanceof Error ? err.message : String(err)
  return msg.toLowerCase().includes("only admins can manage org-wide connectors")
}

/**
 * Message to show when starting an OAuth connect fails. Admin-gate failures
 * get the clear, friendly line; all other errors keep the diagnostic
 * "Could not start <provider> connect: <reason>" form.
 */
export function connectStartErrorMessage(
  providerId: string,
  err: unknown,
): string {
  if (isAdminGateError(err)) return ADMIN_GATE_CONNECT_MESSAGE
  const msg = err instanceof Error ? err.message : String(err)
  return `Could not start ${providerId} connect: ${msg}`
}

/**
 * Filter the grouped catalog by a search query. Matching rules:
 *  - CATEGORY title matches (e.g. "management", "analytics") → the whole
 *    category is kept with all its connectors;
 *  - otherwise each category keeps only connectors whose NAME, id, or type
 *    label matches (e.g. "jira", "clickup", "task management");
 *  - categories left with no matches are dropped.
 * Empty/whitespace query returns the catalog unchanged. Pure — unit-testable.
 */
export function filterConnectorCategories(
  categories: ConnectorCategoryRow[],
  query: string,
): ConnectorCategoryRow[] {
  const q = query.trim().toLowerCase()
  if (!q) return categories
  return categories
    .map((cat) => {
      if (cat.title.toLowerCase().includes(q)) return cat
      const items = cat.items.filter((i) => {
        if (i.name.toLowerCase().includes(q)) return true
        if (i.id.toLowerCase().includes(q)) return true
        return (i.types ?? []).some((tp) =>
          CONNECTOR_TYPE_LABELS[tp].toLowerCase().includes(q),
        )
      })
      return { ...cat, items }
    })
    .filter((cat) => cat.items.length > 0)
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
   * True while a manual upload is in flight (files are being converted +
   * persisted server-side). Drives the upload control's "Uploading…" busy
   * state so the click produces immediate, visible feedback during the
   * multi-second conversion instead of appearing to do nothing.
   */
  uploading?: boolean
  /**
   * Fired when the "Regenerate brief" button is clicked. Kicks off the full
   * pipeline (KG ingest → brief → PRD → evidence) from the latest sources.
   */
  onRegenerateBrief: () => void
  /** True while the full regeneration pipeline is in flight. */
  regenerating: boolean
  /** Inline error from the regenerate trigger, or null. */
  regenerateError: string | null
  /** Live search query — filters categories/connectors (see
   *  filterConnectorCategories). Optional so existing callers stay valid. */
  searchQuery?: string
  onSearchChange?: (value: string) => void
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
  uploading = false,
  files,
  onRegenerateBrief,
  regenerating,
  regenerateError,
  searchQuery = "",
  onSearchChange,
}: ConnectorsSettingsViewProps) {
  const visibleCategories = filterConnectorCategories(categories, searchQuery)
  return (
    <div className="set-pane sp-connectors">
      <div className="set-h">Connectors</div>
      <div className="set-sub">
        Every source feeding your agents, grouped by category. Connect a tool
        or upload files directly to any category.
      </div>

      {/* Search — matches a category name (shows the whole group) or a
          connector name/type (shows just those rows in their groups). */}
      <div className="set-conn-search">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
        </svg>
        <input
          type="search"
          value={searchQuery}
          onChange={(e) => onSearchChange?.(e.target.value)}
          placeholder="Search connectors or categories — e.g. Jira, analytics…"
          aria-label="Search connectors"
        />
      </div>

      {/* Rebuild the weekly brief (and its PRDs + evidence) from the latest
          sources. Prominent primary action right under the intro copy so it's
          the obvious next step after connecting a tool or uploading files. */}
      <div className="set-conn-regen">
        <div className="set-conn-regen-copy">
          <div className="set-conn-regen-t">Regenerate brief</div>
          <div className="set-conn-regen-s">
            Digest new sources and rebuild your weekly brief, PRDs, and evidence.
          </div>
        </div>
        <button
          type="button"
          className="btn btn-primary set-conn-regen-btn"
          disabled={regenerating}
          aria-busy={regenerating}
          onClick={onRegenerateBrief}
        >
          {regenerating ? (
            <>
              <span className="spinner" aria-hidden /> Regenerating…
            </>
          ) : (
            "Regenerate brief"
          )}
        </button>
      </div>
      {regenerateError ? (
        <p className="settings-msg settings-msg-error" role="alert">
          Could not regenerate brief: {regenerateError}
        </p>
      ) : null}

      {loadError ? (
        <p className="settings-msg settings-msg-error" role="alert">
          Could not load connections: {loadError}
        </p>
      ) : null}
      {loading ? <p className="settings-loading">Loading connectors…</p> : null}

      {/* One card per category (the design's grouped layout): serif category
          title + optional "· required" hint, the category's connector rows,
          and a per-category upload strip at the card's foot. Uploads are still
          stored company-wide server-side — the category key just labels the
          gesture. */}
      {/* No matches for the active search — say so instead of a blank pane. */}
      {searchQuery.trim() !== "" && visibleCategories.length === 0 ? (
        <p className="settings-placeholder" data-testid="conn-search-empty">
          No connectors or categories match &quot;{searchQuery.trim()}&quot;.
        </p>
      ) : null}

      {visibleCategories.map((cat) => (
        <section key={cat.key} className="set-block sp-conn-cat" data-category={cat.key}>
          <div className="pset-card-head">
            <h3 className="pset-card-title">{cat.title}</h3>
            {cat.subLabel ? (
              <span className="pset-card-hint">· {cat.subLabel}</span>
            ) : null}
          </div>

          {cat.items.map((item) => {
            const conn = connectionByProvider.get(item.id) ?? null
            const state = getConnectorRowState(item, conn)
            return (
              <div key={item.id} className="set-conn-row">
                <ConnectorLogo item={item} className="logo" />
                <div className="nm">
                  <div className="t">{item.name}</div>
                  <div className={`s${state.disconnected ? " is-disconnected" : ""}`}>
                    {state.statsString}
                  </div>
                </div>
                <span
                  className={`st ${
                    state.disconnected
                      ? "down"
                      : state.status === "active"
                        ? "on"
                        : "off"
                  }`}
                >
                  {state.disconnected
                    ? "Disconnected"
                    : state.status === "active"
                      ? "Active"
                      : "Off"}
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
            className={`set-conn-upload${uploading ? " is-uploading" : ""}`}
            title={uploading ? "Uploading…" : `Upload ${cat.title.toLowerCase()} files`}
            aria-busy={uploading}
          >
            <i
              className={`ti ${uploading ? "ti-loader-2 ti-spin" : "ti-cloud-upload"}`}
              aria-hidden
            />
            {uploading ? "Uploading…" : `Upload ${cat.title.toLowerCase()} export`}
            <span className="muted">{cat.uploadAccept ?? UPLOAD_ACCEPT_HINT}</span>
            <input
              type="file"
              multiple
              accept={(cat.uploadExtensions ?? UPLOAD_EXTENSIONS).join(",")}
              // Block re-selection while a previous batch is still ingesting so
              // the user can't fire overlapping uploads mid-flight.
              disabled={uploading}
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
        </section>
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
  const { showToast, goTo } = useNavigation()

  const [connections, setConnections] = useState<ConnectionSummary[]>([])
  const [files, setFiles] = useState<SourceFile[]>([])
  const [searchQuery, setSearchQuery] = useState("")
  const [loading, setLoading] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [regenerating, setRegenerating] = useState(false)
  const [regenerateError, setRegenerateError] = useState<string | null>(null)
  const [configuringProviderId, setConfiguringProviderId] = useState<
    string | null
  >(null)
  const [apiKeyConnectingItem, setApiKeyConnectingItem] =
    useState<ConnectorItemRow | null>(null)
  const [credentialsConnectingItem, setCredentialsConnectingItem] =
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
  // surface "Coming soon" rows the user can't act on. Categories left with no
  // connectors are dropped entirely.
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

      if (item?.authType === "credentials") {
        // Self-hosted tool: open the URL + username + password form.
        setCredentialsConnectingItem(item)
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
        // Non-admins hit a 403 admin gate for org-wide connectors (e.g.
        // Google Drive). Surface a clear, friendly explanation instead of
        // the raw "Could not start … connect" diagnostic.
        setLoadError(connectStartErrorMessage(providerId, e))
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

  const handleCredentialsConnect = useCallback(
    async (values: CredentialsValues) => {
      if (!credentialsConnectingItem) return
      if (credentialsConnectingItem.id === "superset") {
        await connectorsApi.connectSupersetWithCredentials(
          values.baseUrl, values.username, values.password,
        )
        await reload()
      } else {
        throw new Error(
          `Credentials connect not wired for provider: ${credentialsConnectingItem.id}`,
        )
      }
    },
    [credentialsConnectingItem, reload],
  )

  const onConfigure = useCallback((providerId: string) => {
    setConfiguringProviderId(providerId)
  }, [])

  // Trigger the full regeneration pipeline (KG ingest → brief → PRD →
  // evidence) from the latest connected sources and uploads. The endpoint is
  // fire-and-forget: it returns as soon as the background chain is scheduled,
  // so a resolved promise means "started", not "finished". We surface a toast
  // and send the user to the Weekly brief, which polls itself to `ready`.
  const handleRegenerateBrief = useCallback(async () => {
    if (regenerating) return
    setRegenerating(true)
    setRegenerateError(null)
    try {
      await briefApi.regenerateAll(activeCompany)
      // Tell the home surface a regen is underway so it starts watching the
      // brief `regenerating` flag immediately — even when the user lands on the
      // brief directly here, with no preceding connector-connect signal. The
      // banner + fresh-brief swap are owned by useBriefHydration / BriefChat.
      notifyBriefRegenerating()
      showToast(
        "Regenerating brief",
        "Digesting your latest sources — your brief, PRDs, and evidence will refresh shortly.",
      )
      goTo("brief")
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      setRegenerateError(msg)
      showToast("Regenerate failed", msg)
    } finally {
      setRegenerating(false)
    }
  }, [activeCompany, regenerating, showToast, goTo])

  const onUpload = useCallback(
    async (categoryKey: string, picked: FileList) => {
      const list = Array.from(picked)
      if (list.length === 0) return
      setUploading(true)
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
      } finally {
        setUploading(false)
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
        uploading={uploading}
        files={files}
        onRegenerateBrief={handleRegenerateBrief}
        regenerating={regenerating}
        regenerateError={regenerateError}
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
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
            ? apiKeyHelp(apiKeyConnectingItem.id, apiKeyConnectingItem.name)
            : null
        }
        onConnect={handleApiKeyConnect}
        onClose={() => setApiKeyConnectingItem(null)}
      />
      <CredentialsPromptModal
        open={credentialsConnectingItem != null}
        connectorName={credentialsConnectingItem?.name ?? ""}
        helpText={
          credentialsConnectingItem?.id === "superset"
            ? "Enter your Superset instance URL and a service account — ideally a dedicated read-only (Gamma) user created just for Sprntly."
            : null
        }
        onConnect={handleCredentialsConnect}
        onClose={() => setCredentialsConnectingItem(null)}
      />
    </>
  )
}
