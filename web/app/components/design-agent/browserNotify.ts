/**
 * Browser (OS-level) "prototype ready" notifications via the Web Notifications
 * API — the cross-tab/backgrounded-tab complement to the in-app toast +
 * sessionStorage replay in `notificationStore.ts`.
 *
 * A prototype generation runs minutes (codegen + Vite build + stage tail), so a
 * user very often navigates away or backgrounds the tab before it finishes. The
 * in-app toast only lands if they happen to be looking; a browser Notification
 * reaches them anywhere in the OS. We request permission at kickoff (inside the
 * Generate click's user-gesture, where browsers allow the prompt) and fire the
 * notification from the same completion path that records the toast.
 *
 * Scope: this is the Notification-API path (works while the app's JS context is
 * alive — i.e. across SPA navigations and backgrounded tabs in the same load).
 * Surviving a full page reload / closed tab needs a Service Worker + Web Push
 * (VAPID + a backend push send); that is a deliberate follow-up, not this fix.
 *
 * Every function is SSR-safe (`typeof window`, `"Notification" in window`) and
 * best-effort (try/catch → no-op), so it never throws into the generation flow
 * and stays unit-testable under the repo's `node` vitest env (tests stub
 * `window.Notification`).
 */

export type NotifyPermission = NotificationPermission | "unsupported"

/** True only in a browser that exposes the Notifications API. */
export function notificationsSupported(): boolean {
  return typeof window !== "undefined" && "Notification" in window
}

/** Current permission, or "unsupported" off-browser / when the API is absent. */
export function notifyPermission(): NotifyPermission {
  if (!notificationsSupported()) return "unsupported"
  try {
    return window.Notification.permission
  } catch {
    return "unsupported"
  }
}

/**
 * Ask for Notification permission iff it has not been decided yet ("default").
 * Best-effort and non-blocking-by-contract: callers should NOT await this on the
 * critical path — invoke it (unawaited) inside the kickoff user-gesture so the
 * browser allows the prompt, then continue. Returns the resulting permission
 * (or "unsupported"). Never throws.
 */
export async function ensureNotifyPermission(): Promise<NotifyPermission> {
  if (!notificationsSupported()) return "unsupported"
  try {
    if (window.Notification.permission === "default") {
      return await window.Notification.requestPermission()
    }
    return window.Notification.permission
  } catch {
    return "unsupported"
  }
}

/**
 * Fire the "prototype ready" OS notification when permission is granted. Tagged
 * per prd so a re-notify replaces rather than stacks. Clicking it focuses the
 * app and (when a prdId is known) routes to that prototype. No-op when
 * unsupported / not granted; never throws.
 */
export function fireReadyNotification(opts: {
  title: string
  body: string
  prdId?: number | null
}): void {
  if (!notificationsSupported()) return
  try {
    if (window.Notification.permission !== "granted") return
    const tag =
      opts.prdId != null ? `da-prototype-ready-${opts.prdId}` : "da-prototype-ready"
    const n = new window.Notification(opts.title, { body: opts.body, tag })
    n.onclick = () => {
      try {
        window.focus()
        if (opts.prdId != null) {
          window.location.assign(`/prototype?prd=${encodeURIComponent(String(opts.prdId))}`)
        }
      } catch {
        // best-effort focus/navigate
      }
      n.close()
    }
  } catch {
    // permission race / construction failure — drop silently.
  }
}
