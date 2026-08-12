"use client"

import { useEffect, useRef, useState } from "react"
import type { RealtimeChannel } from "@supabase/supabase-js"
import { getSupabase, isSupabaseConfigured } from "../../../../lib/supabase/client"

export type ChannelStatus = "connecting" | "live" | "degraded"

export interface RealtimeChannelHandlers {
  /** A broadcast event fired on the channel; `event` is the AD-P21 event name
   *  (e.g. "turn.created"), `payload` is the backend-shaped DTO. */
  onEvent?: (event: string, payload: unknown) => void
  /** Called once on every (re)subscribe so the consumer can run ONE
   *  since-cursor reconcile (AD-P22 — closes any at-most-once Broadcast gap). */
  onReconcile?: () => void
}

export interface UseRealtimeChannelResult {
  status: ChannelStatus
  degraded: boolean
}

/**
 * The shared client subscription primitive every realtime surface (group
 * turns, briefs, unread, presence, typing) needs: join a private Broadcast
 * channel, fan broadcast events out to the caller, fire one reconcile per
 * (re)connect, and signal `degraded` so the caller's existing poll can keep
 * running as the fallback (AD-P22). Built once here so no consumer
 * re-implements channel/reconnect/fallback logic.
 *
 * `topic === null` (flag off / no id yet) or an unconfigured Supabase client
 * are both treated as the SAME degraded-by-default case, not an error path
 * — the hook never throws, it only ever reports `degraded: true` and leaves
 * the consumer's poll to carry the surface.
 */
export function useRealtimeChannel(
  topic: string | null,
  handlers: RealtimeChannelHandlers,
): UseRealtimeChannelResult {
  const [status, setStatus] = useState<ChannelStatus>(() =>
    topic && isSupabaseConfigured() ? "connecting" : "degraded",
  )

  // Handlers live in a ref so a consumer passing fresh inline callbacks on
  // every render does NOT tear down + rebuild the channel — channel identity
  // keys on `topic` only (AC-8).
  const handlersRef = useRef(handlers)
  handlersRef.current = handlers

  useEffect(() => {
    if (!topic || !isSupabaseConfigured()) {
      setStatus("degraded")
      return
    }

    setStatus("connecting")

    const supabase = getSupabase()
    let cancelled = false

    // Private channels require the Realtime client to carry the user's JWT
    // before the join is attempted, so the channel-auth RLS policy can see
    // who's asking. No-arg setAuth() adopts the session already on the
    // client (see supabase-js RealtimeClient.setAuth).
    void supabase.realtime.setAuth()

    const channel: RealtimeChannel = supabase.channel(topic, {
      config: { private: true },
    })

    channel.on("broadcast", { event: "*" }, ({ event, payload }) => {
      if (cancelled) return
      handlersRef.current.onEvent?.(event, payload)
    })

    channel.subscribe((subscribeStatus) => {
      if (cancelled) return
      if (subscribeStatus === "SUBSCRIBED") {
        setStatus("live")
        // Exactly one reconcile per (re)subscribe transition — never on a
        // plain broadcast event.
        handlersRef.current.onReconcile?.()
        return
      }
      // CHANNEL_ERROR / TIMED_OUT / CLOSED all fall back to the same
      // degraded state — the consumer's existing poll takes over.
      setStatus("degraded")
    })

    return () => {
      cancelled = true
      void supabase.removeChannel(channel)
    }
  }, [topic])

  return { status, degraded: status !== "live" }
}
