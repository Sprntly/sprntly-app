"use client"

import { useEffect, useRef } from "react"
import { designAgentApi, getAccessToken } from "../../lib/api"

/**
 * Tracks a prototype's generation to real completion via SSE, independent of
 * whether any loading-overlay UI is currently mounted for it. Exists because
 * a host whose GenerationLoadingScreen instance sits behind an early-return
 * chain (rather than always mounted) loses that instance's SSE connection the
 * moment its own branch stops rendering — this hook is the thing that keeps
 * listening after that overlay is gone.
 */
export function useDesignAgentLiveTerminal(
  prototypeId: number | null,
  onTerminal: (kind: "done" | "error") => void,
) {
  const onTerminalRef = useRef(onTerminal)
  useEffect(() => {
    onTerminalRef.current = onTerminal
  })

  useEffect(() => {
    if (!prototypeId) return
    let cancelled = false
    let es: EventSource | null = null

    const open = async () => {
      let token: string | null = null
      try {
        token = await getAccessToken()
      } catch {
        return
      }
      if (cancelled || !token) return
      try {
        es = new EventSource(designAgentApi.eventsUrl(prototypeId, token))
        es.onmessage = (e: MessageEvent) => {
          if (cancelled) return
          try {
            const event = JSON.parse(e.data as string) as { kind: string }
            if (event.kind === "done" || event.kind === "error") {
              onTerminalRef.current(event.kind)
              es?.close()
            }
          } catch {
            // ignore parse errors
          }
        }
        // No eager close on transient error — same reasoning as
        // GenerationLoadingScreen.tsx: let the browser's native reconnect run.
        es.onerror = () => {}
      } catch {
        // degrade silently — the sessionStorage recovery path (markPending /
        // resumePendingNotifications) still covers this prototype on next load
      }
    }

    void open()
    return () => {
      cancelled = true
      es?.close()
    }
  }, [prototypeId])
}
