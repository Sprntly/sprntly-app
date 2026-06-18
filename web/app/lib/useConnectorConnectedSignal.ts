"use client"

import { useEffect, useRef } from "react"
import {
  CONNECTOR_CHANNEL,
  CONNECTOR_CONNECTED_MESSAGE,
  CONNECTOR_STORAGE_KEY,
  type ConnectorConnectedMessage,
} from "./connectorReturn"

/**
 * Subscribe the current (original) Sprntly tab to "a connector just
 * connected" signals fired by the `/connectors/return` page in the OAuth tab.
 *
 * Listens on two channels for cross-browser reach:
 *   1. BroadcastChannel("sprntly-connectors") — the primary signal.
 *   2. `storage` events for CONNECTOR_STORAGE_KEY — fallback for browsers
 *      without BroadcastChannel (the event only fires in *other* tabs).
 *
 * `onConnected(provider)` is invoked with the connected provider id. The
 * callback is held in a ref so the subscription doesn't churn when callers
 * pass an inline function.
 */
export function useConnectorConnectedSignal(
  onConnected: (provider: string) => void,
): void {
  const cb = useRef(onConnected)
  cb.current = onConnected

  useEffect(() => {
    if (typeof window === "undefined") return

    let channel: BroadcastChannel | null = null
    if (typeof BroadcastChannel !== "undefined") {
      try {
        channel = new BroadcastChannel(CONNECTOR_CHANNEL)
        channel.onmessage = (ev: MessageEvent) => {
          const data = ev.data as ConnectorConnectedMessage | undefined
          if (data?.type === CONNECTOR_CONNECTED_MESSAGE && data.provider) {
            cb.current(data.provider)
          }
        }
      } catch {
        channel = null
      }
    }

    const onStorage = (ev: StorageEvent) => {
      if (ev.key !== CONNECTOR_STORAGE_KEY || !ev.newValue) return
      try {
        const parsed = JSON.parse(ev.newValue) as { provider?: string }
        if (parsed?.provider) cb.current(parsed.provider)
      } catch {
        /* malformed payload — ignore */
      }
    }
    window.addEventListener("storage", onStorage)

    return () => {
      window.removeEventListener("storage", onStorage)
      if (channel) {
        try {
          channel.onmessage = null
          channel.close()
        } catch {
          /* best-effort */
        }
      }
    }
  }, [])
}
