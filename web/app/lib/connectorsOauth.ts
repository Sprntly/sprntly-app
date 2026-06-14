/**
 * Open a connector's OAuth authorize URL in a NEW browser tab (same window)
 * rather than navigating the current tab away.
 *
 * Why: connecting Slack/GitHub/etc. during onboarding (or from Settings) used
 * to do `window.location.href = authorize_url`, which yanks the user out of
 * the flow they were in. Opening the provider in a sibling tab lets them
 * authorize and come back to exactly where they left off.
 *
 * The catch: popup blockers reject `window.open()` once the originating user
 * gesture has "expired" — and we only learn the authorize URL *after* an
 * awaited `startOauth` fetch. So we open a blank tab synchronously inside the
 * click handler (while the gesture is still live) and point it at the real URL
 * once the fetch resolves. If the tab couldn't be opened (blocker, or
 * non-browser env), we fall back to a same-tab navigation so the connection
 * can still be made.
 */
export type PendingOauthTab = {
  /** Point the pre-opened tab at the resolved authorize URL. */
  finish: (authorizeUrl: string) => void
  /** Close the pre-opened tab — call when startOauth fails before a URL. */
  abort: () => void
}

export function openOauthTab(): PendingOauthTab {
  const tab =
    typeof window !== "undefined" ? window.open("about:blank", "_blank") : null

  // Sever the opener link — the security half of `noopener,noreferrer`. We
  // can't pass that feature string to window.open() here because it makes the
  // call return null, and we need the handle to point the pre-opened tab at
  // the authorize URL once startOauth resolves. Nulling `opener` after the
  // fact gives the same reverse-tabnabbing protection without losing the
  // handle.
  if (tab) {
    try {
      tab.opener = null
    } catch {
      // Some environments expose `opener` as read-only — best-effort only.
    }
  }

  return {
    finish: (authorizeUrl: string) => {
      if (tab && !tab.closed) {
        tab.location.href = authorizeUrl
      } else if (typeof window !== "undefined") {
        // Popup blocked or tab already gone — don't strand the user; complete
        // the connect in the current tab as the old behaviour did.
        window.location.href = authorizeUrl
      }
    },
    abort: () => {
      if (tab && !tab.closed) tab.close()
    },
  }
}
