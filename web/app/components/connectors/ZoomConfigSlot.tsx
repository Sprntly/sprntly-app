/**
 * Zoom config slot — the whole Zoom panel, mounted from BOTH the Configure
 * drawer and the post-connect modal so the two cannot drift. Composes, in
 * order:
 *
 *   1. the auth-expired block (conditional) — the connection stopped syncing
 *   2. the sync summary — last sync, meetings found, transcripts read
 *   3. the no-transcripts warning (conditional)
 *   4. the host picker
 *
 * EVERY SUMMARY ROW ALWAYS RENDERS. A blank value is not a signal to drop the
 * line: "Transcripts read: —" tells a user we have not counted yet, while a
 * missing row tells them nothing and looks like a bug. The rule matters most
 * for the transcripts row, because the GAP between meetings and transcripts is
 * the single most useful diagnostic this connector has — it is how somebody
 * learns that Zoom's *Audio transcript* setting is switched off, which is a
 * thing they can go and fix.
 *
 * The counters are absent (undefined), not zero, until a sync completes. Those
 * are different states and the warning notice only fires on the first: a run
 * that has never happened must not be reported as a run that found nothing.
 *
 * Pure View pattern (props in, JSX out) for unit testing via
 * renderToStaticMarkup, plus a hooks-wired wrapper.
 */
"use client"

import { useCallback, useState } from "react"
import { connectorsApi, type ConnectionSummary } from "../../lib/api"
import { openOauthTab } from "../../lib/connectorsOauth"
import { formatRelativeDate } from "../../lib/sources-helpers"
import { useWorkspace } from "../../context/WorkspaceContext"
import { ZoomHostsPicker } from "./ZoomHostsPicker"

/** How long after a connect we still describe the first sync as running. The
 *  backfill walks three months across every licensed host, so it is minutes,
 *  not seconds — and a flat "Never" during that window reads as broken. */
const FIRST_SYNC_GRACE_MS = 10 * 60 * 1000

// ─────────────────────────── Pure View ───────────────────────────

export type ZoomConfigSlotViewProps = {
  /** "never" | "running" | "done" | "failed" — resolved by lastSyncState(). */
  syncState: "never" | "running" | "done" | "failed"
  /** Relative phrase for a completed sync, e.g. "2 hours ago". */
  lastSyncPhrase: string | null
  /** Mapped, human failure reason — never a raw provider or HTTP string. */
  failureReason: string | null
  /** undefined = never counted. Rendered as "—", never as 0. */
  meetings?: number
  transcripts?: number
  authExpired: boolean
  canManage: boolean
  isReconnecting: boolean
  onReconnect: () => void
  children?: React.ReactNode
}

function countText(n: number | undefined): string {
  return typeof n === "number" ? String(n) : "—"
}

export function ZoomConfigSlotView({
  syncState,
  lastSyncPhrase,
  failureReason,
  meetings,
  transcripts,
  authExpired,
  canManage,
  isReconnecting,
  onReconnect,
  children,
}: ZoomConfigSlotViewProps) {
  // Only after a run that actually completed, and only when both numbers are
  // real. Absent counters mean "never counted", which must never be presented
  // as "found nothing" — that is the confident-false-conclusion failure this
  // codebase keeps hitting.
  const showNoTranscripts =
    typeof meetings === "number" &&
    typeof transcripts === "number" &&
    meetings > 0 &&
    transcripts === 0

  let lastSyncValue: string
  if (syncState === "running") {
    lastSyncValue = "First sync running — backfilling the last 3 months."
  } else if (syncState === "never") {
    lastSyncValue = "Never — the first sync starts within a few minutes."
  } else if (syncState === "failed") {
    lastSyncValue = `Failed — ${failureReason ?? "Sprntly could not reach Zoom"}. Sprntly will try again at the next refresh.`
  } else {
    lastSyncValue = lastSyncPhrase ?? "—"
  }

  return (
    <div className="conn-slack-setup">
      {authExpired ? (
        <div className="conn-zoom-notice conn-zoom-notice--danger" role="alert">
          <strong>Zoom stopped syncing.</strong> Sprntly&rsquo;s access to Zoom
          expired or was revoked, so new recordings aren&rsquo;t coming in.
          Reconnect to pick up where the last sync left off.
          {canManage ? (
            <div className="conn-zoom-notice-actions">
              <button
                type="button"
                className="btn btn-sm btn-primary"
                aria-busy={isReconnecting}
                disabled={isReconnecting}
                onClick={onReconnect}
              >
                {isReconnecting ? "Reconnecting…" : "Reconnect Zoom"}
              </button>
            </div>
          ) : (
            <> Ask a workspace admin to reconnect Zoom.</>
          )}
        </div>
      ) : null}

      <div className="conn-zoom-summary">
        <div className="conn-zoom-summary-row">
          <span className="conn-zoom-summary-k">Last sync</span>
          <span
            className="conn-zoom-summary-v"
            {...(syncState === "running"
              ? { role: "status", "aria-live": "polite" as const }
              : {})}
          >
            {lastSyncValue}
          </span>
        </div>
        <div className="conn-zoom-summary-row">
          <span className="conn-zoom-summary-k">Meetings found</span>
          <span className="conn-zoom-summary-v">{countText(meetings)}</span>
        </div>
        <div className="conn-zoom-summary-row">
          <span className="conn-zoom-summary-k">Transcripts read</span>
          <span className="conn-zoom-summary-v">{countText(transcripts)}</span>
        </div>
      </div>

      {showNoTranscripts ? (
        <p className="conn-zoom-notice conn-zoom-notice--warn" role="status">
          <strong>Recordings found, but no transcripts.</strong> Sprntly synced{" "}
          {meetings} meetings from Zoom and found no transcript files. Turn on{" "}
          <strong>Audio transcript</strong> in Zoom&rsquo;s Recording settings —
          Zoom only creates transcripts for calls recorded after it&rsquo;s
          switched on.
        </p>
      ) : null}

      {children}
    </div>
  )
}

// ───────────────────── Sync-state resolution ─────────────────────

/**
 * Which of the four last-sync states a connection is in.
 *
 * "running" is the one worth explaining: a connection with no `last_sync_at`
 * is either brand new (the backfill is under way, minutes long) or genuinely
 * idle. The connect timestamp is the only signal available without polling,
 * and the drawer refetches on open — which is enough, because the state
 * resolves itself and nobody sits watching a drawer for ten minutes.
 */
export function lastSyncState(
  connection: Pick<
    ConnectionSummary,
    "last_sync_at" | "last_sync_error" | "created_at"
  > | null,
  now: Date = new Date(),
): "never" | "running" | "done" | "failed" {
  if (!connection) return "never"
  if (connection.last_sync_error) return "failed"
  if (connection.last_sync_at) return "done"
  const created = Date.parse(connection.created_at || "")
  if (Number.isFinite(created) && now.getTime() - created < FIRST_SYNC_GRACE_MS) {
    return "running"
  }
  return "never"
}

/**
 * A stamped sync error → a sentence a person can act on.
 *
 * The stored string is whatever the puller or the provider produced, and it is
 * not copy: it can be a stack-shaped fragment or a raw provider message. Two
 * known shapes get real guidance and everything else degrades to a neutral
 * phrase rather than being printed verbatim.
 */
export function mapSyncFailure(raw: string | null | undefined): string {
  const text = (raw || "").toLowerCase()
  if (!text) return "Sprntly could not reach Zoom"
  if (text.includes("reconnect") || text.includes("authorization expired")) {
    return "Zoom access expired and needs reconnecting"
  }
  if (text.includes("rate") || text.includes("429")) {
    return "Zoom rate-limited the sync"
  }
  return "Sprntly could not reach Zoom"
}

// ───────────────────── Hooks-wired wrapper ─────────────────────

type Props = {
  connection: ConnectionSummary | null
  /** Fired after a successful save so the parent can reload connections. */
  onSaved: () => void
}

export function ZoomConfigSlot({ connection, onSaved }: Props) {
  const { orgRole } = useWorkspace()
  const canManage = orgRole === "owner" || orgRole === "admin"
  const [isReconnecting, setIsReconnecting] = useState(false)

  // "disconnected" is what the scheduled health monitor and the on-open probe
  // both write, so this one flag covers both routes into the reconnect state.
  const authExpired = connection?.health === "disconnected"

  const handleReconnect = useCallback(async () => {
    setIsReconnecting(true)
    // Open the tab synchronously, while the click gesture is still live — a
    // popup blocker rejects window.open() after the awaited startOauth.
    const oauthTab = openOauthTab()
    try {
      const r = await connectorsApi.startOauth("zoom")
      oauthTab.finish(r.authorize_url)
    } catch {
      oauthTab.abort()
    } finally {
      setIsReconnecting(false)
    }
  }, [])

  const state = lastSyncState(connection)

  return (
    <ZoomConfigSlotView
      syncState={state}
      lastSyncPhrase={
        connection?.last_sync_at
          ? formatRelativeDate(connection.last_sync_at)
          : null
      }
      failureReason={
        state === "failed" ? mapSyncFailure(connection?.last_sync_error) : null
      }
      meetings={connection?.config?.last_sync_meetings}
      transcripts={connection?.config?.last_sync_transcripts}
      authExpired={Boolean(authExpired)}
      canManage={canManage}
      isReconnecting={isReconnecting}
      onReconnect={() => void handleReconnect()}
    >
      <ZoomHostsPicker
        savedUserIds={connection?.config?.sync_user_ids}
        savedUserNames={connection?.config?.sync_user_names}
        authExpired={Boolean(authExpired)}
        onSaved={onSaved}
      />
    </ZoomConfigSlotView>
  )
}
