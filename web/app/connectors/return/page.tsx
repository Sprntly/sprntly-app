"use client"

import { useEffect, useState } from "react"
import { handleConnectorReturn } from "../../lib/connectorReturn"

/**
 * Lightweight landing page for connector OAuth callbacks (`/connectors/return`).
 *
 * The backend bounces the OAuth tab here (instead of re-loading the whole app)
 * with `?connected=<provider>` and an optional relative `?return_to=`. On
 * mount we signal the original Sprntly tab — via BroadcastChannel + a
 * localStorage fallback — that the connector connected, then close this tab.
 * If the tab can't self-close we navigate to `return_to` so the app still
 * reflects the new connection.
 *
 * Deliberately NOT wrapped in the app shell — it's a transient stub the user
 * should never really see (the tab closes immediately).
 */
export default function ConnectorReturnPage() {
  const [provider, setProvider] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (typeof window === "undefined") return
    const params = new URLSearchParams(window.location.search)
    const connected = params.get("connected")
    const err = params.get("error")
    const returnTo = params.get("return_to")

    if (err) {
      setError(err)
      return
    }
    if (!connected) return

    setProvider(connected)
    handleConnectorReturn({ provider: connected, returnTo })
  }, [])

  return (
    <div
      className="ob-shell"
      style={{ justifyContent: "center", textAlign: "center" }}
    >
      {error ? (
        <p style={{ color: "var(--muted)", fontSize: 14 }}>
          Connection failed: {error}. You can close this tab and try again.
        </p>
      ) : (
        <p style={{ color: "var(--muted)", fontSize: 14 }}>
          {provider ? "✓ Connected — " : ""}returning to Sprntly… (you can close
          this tab)
        </p>
      )}
    </div>
  )
}
