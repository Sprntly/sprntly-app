/**
 * Logic for the lightweight `/connectors/return` page — the tab a connector's
 * OAuth callback lands in. Its job: tell the *original* Sprntly tab that a
 * connector just connected, then close itself so the user is left back where
 * they started (with the connector showing connected).
 *
 * Signalling happens two ways for maximum cross-browser reach:
 *   1. BroadcastChannel("sprntly-connectors") — instant, same-origin pub/sub.
 *   2. A short-lived localStorage key — the `storage` event fires in *other*
 *      same-origin tabs, covering browsers without BroadcastChannel.
 *
 * Closing: tabs opened via `window.open` are script-closable in major
 * browsers even after the opener link is severed. But it can be blocked (or
 * we may have fallen back to a same-tab navigation), so if the tab is still
 * around shortly after, we navigate to a sanitised `return_to` (appending
 * `?connected=<provider>`) so the app still reflects the new connection.
 *
 * Everything is guarded for non-browser / restricted environments.
 */

export const CONNECTOR_CHANNEL = "sprntly-connectors"
export const CONNECTOR_STORAGE_KEY = "sprntly_connector_connected"
export const CONNECTOR_CONNECTED_MESSAGE = "connector-connected"

const DEFAULT_RETURN_TO = "/onboarding/connectors"
/** How long to wait for window.close() to take effect before falling back. */
const CLOSE_FALLBACK_MS = 400

export type ConnectorConnectedMessage = {
  type: typeof CONNECTOR_CONNECTED_MESSAGE
  provider: string
}

/** Only relative, single-leading-slash paths are honoured (open-redirect
 *  guard mirrors the backend's `_is_safe_return_to`). Anything else → null. */
export function sanitizeReturnTo(value: string | null | undefined): string | null {
  if (!value || typeof value !== "string") return null
  if (value.length > 1024) return null
  if (!value.startsWith("/") || value.startsWith("//")) return null
  if (value.includes("\\")) return null
  return value
}

/** Append `connected=<provider>` to a path, respecting an existing query. */
function withConnected(path: string, provider: string): string {
  const sep = path.includes("?") ? "&" : "?"
  return `${path}${sep}connected=${encodeURIComponent(provider)}`
}

/** Broadcast on BroadcastChannel (guarded) — returns true if it fired. */
export function broadcastConnected(provider: string): boolean {
  if (typeof BroadcastChannel === "undefined") return false
  let channel: BroadcastChannel | null = null
  try {
    channel = new BroadcastChannel(CONNECTOR_CHANNEL)
    const msg: ConnectorConnectedMessage = {
      type: CONNECTOR_CONNECTED_MESSAGE,
      provider,
    }
    channel.postMessage(msg)
    // Defer close to a macrotask: closing the channel synchronously can drop
    // the just-queued message before it's delivered to other tabs (observed in
    // jsdom and not guaranteed across browsers). The return page calls
    // window.close() right after, so the channel won't outlive the tab anyway.
    const ch = channel
    setTimeout(() => {
      try {
        ch.close()
      } catch {
        /* best-effort */
      }
    }, 0)
    return true
  } catch {
    try {
      channel?.close()
    } catch {
      /* best-effort */
    }
    return false
  }
}

/** Write the short-lived localStorage fallback signal (storage event fires in
 *  other tabs). Returns true if it was written. */
export function writeStorageSignal(provider: string): boolean {
  if (typeof window === "undefined") return false
  try {
    const storage = window.localStorage
    if (!storage) return false
    storage.setItem(
      CONNECTOR_STORAGE_KEY,
      JSON.stringify({ provider, t: Date.now() }),
    )
    return true
  } catch {
    // private mode / disabled storage — non-fatal, BroadcastChannel may cover.
    return false
  }
}

export type HandleConnectorReturnOptions = {
  provider: string
  returnTo?: string | null
  /** Override the close-fallback delay (tests pass 0 for synchronous flow). */
  closeFallbackMs?: number
  /** Injectable for tests. Defaults to setTimeout. */
  schedule?: (fn: () => void, ms: number) => void
}

/**
 * Run the full return-tab routine: signal the other tabs, then try to close
 * this tab, falling back to a navigation if close is blocked.
 */
export function handleConnectorReturn({
  provider,
  returnTo,
  closeFallbackMs = CLOSE_FALLBACK_MS,
  schedule,
}: HandleConnectorReturnOptions): void {
  if (typeof window === "undefined" || !provider) return

  broadcastConnected(provider)
  writeStorageSignal(provider)

  const runSchedule =
    schedule ?? ((fn: () => void, ms: number) => window.setTimeout(fn, ms))

  // Attempt to self-close. Even if window.close() is a no-op (close blocked,
  // or this is a same-tab fallback load), the timeout below catches it.
  try {
    window.close()
  } catch {
    /* close may throw in some environments — handled by the fallback below */
  }

  runSchedule(() => {
    // If we're still here, close didn't take — navigate so the app reflects
    // the connection rather than stranding the user on this stub page.
    if (typeof window === "undefined" || window.closed) return
    const safe = sanitizeReturnTo(returnTo) ?? DEFAULT_RETURN_TO
    try {
      window.location.replace(withConnected(safe, provider))
    } catch {
      /* nothing more we can do */
    }
  }, closeFallbackMs)
}
