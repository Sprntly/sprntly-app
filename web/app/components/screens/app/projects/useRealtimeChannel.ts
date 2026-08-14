"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import type { RealtimeChannel } from "@supabase/supabase-js"
import { getSupabase, isSupabaseConfigured } from "../../../../lib/supabase/client"

export type ChannelStatus = "connecting" | "live" | "degraded"

/** Minimal identity a presence/typing participant carries — no other field
 *  is tracked or rendered (AD-P24: ephemeral, nothing persisted). */
export interface PresenceIdentity {
  userId: string
  name: string
}

/** A raw Realtime Presence state map, loosely typed — supabase-js does not
 *  export `RealtimePresenceState` from its top-level package entry, and the
 *  hook only ever reads the two fields it tracks (AD-P24: verify the exact
 *  Presence API against the installed SDK, not from memory). */
type RawPresenceState = Record<string, Array<{ userId?: unknown; name?: unknown }>>

export interface RealtimeChannelHandlers {
  /** A broadcast event fired on the channel; `event` is the AD-P21 event name
   *  (e.g. "turn.created"), `payload` is the backend-shaped DTO. */
  onEvent?: (event: string, payload: unknown) => void
  /** Called once on every (re)subscribe so the consumer can run ONE
   *  since-cursor reconcile (AD-P22 — closes any at-most-once Broadcast gap).
   *  A consumer that mounts AFTER another consumer already has the same
   *  topic live gets its own replayed call the instant it registers, so it
   *  never misses its "just joined" reconcile (AD-P26). */
  onReconcile?: () => void
  /** When provided, the hook `track()`s this identity on the channel's
   *  Presence and exposes the live roster via `presenceMembers` (AD-P24 —
   *  ephemeral Presence, no table). Omit to skip presence entirely. */
  presence?: { self: PresenceIdentity }
}

export interface UseRealtimeChannelResult {
  status: ChannelStatus
  degraded: boolean
  /** Live Presence roster (self included), deduped by `userId` — reflects
   *  join/leave/hard-disconnect with no polling and no persisted row. Empty
   *  when degraded or when `presence` was not provided. */
  presenceMembers: PresenceIdentity[]
  /** Sends a `typing` Broadcast event carrying `self`, throttled to at most
   *  once per ~1s so a fast typist doesn't flood the channel. A no-op while
   *  degraded/unconfigured. */
  sendTyping: (self: PresenceIdentity) => void
  /** Other participants currently typing (self always excluded), each entry
   *  expiring ~3s after its last `typing` event — no "stopped typing" event
   *  needed, nothing persisted. Empty when degraded. */
  typers: PresenceIdentity[]
}

/** Client-side expiry for a `typers` entry — refreshed on every new `typing`
 *  event from that sender; no server-side "stopped typing" signal exists. */
const TYPING_EXPIRY_MS = 3000
/** Outgoing `typing` broadcast throttle — a composer firing on every
 *  keystroke must not flood the channel. */
const TYPING_SEND_THROTTLE_MS = 1000

function flattenPresenceState(state: RawPresenceState): PresenceIdentity[] {
  const members = new Map<string, PresenceIdentity>()
  for (const key of Object.keys(state)) {
    for (const entry of state[key]) {
      if (typeof entry?.userId !== "string") continue
      members.set(entry.userId, { userId: entry.userId, name: typeof entry.name === "string" ? entry.name : "" })
    }
  }
  return Array.from(members.values())
}

/** One hook instance's fan-out callbacks, registered against the shared
 *  per-topic channel entry below. Kept intentionally tiny — everything
 *  stateful (typers, throttle timers) stays inside the hook instance; the
 *  registry only needs a way to reach each live consumer. */
interface SharedChannelConsumer {
  onBroadcast: (event: string, payload: unknown) => void
  onPresenceSync: () => void
  /** `subscribeStatus` is the raw string a supabase-js `subscribe()`
   *  callback receives (`"SUBSCRIBED"`, `"CHANNEL_ERROR"`, ...). */
  onStatus: (subscribeStatus: string) => void
}

/** One shared Realtime channel for a topic, plus every consumer currently
 *  attached to it. Multiple `useRealtimeChannel(sameTopic, ...)` mounts
 *  (e.g. a parent screen's own unread-badge subscription and a child tab's
 *  chat subscription on the identical private topic) share exactly ONE
 *  underlying `RealtimeChannel` — creating a second channel object for a
 *  topic that's already joined/joining is what previously tripped
 *  realtime-js's "cannot add presence callbacks after subscribe()"
 *  invariant and crashed the whole screen. */
interface SharedChannelEntry {
  channel: RealtimeChannel
  refCount: number
  consumers: Map<number, SharedChannelConsumer>
  /** The most recent raw `subscribe()` status the shared channel reported,
   *  or `null` before the first one arrives. Replayed to a consumer that
   *  registers AFTER the channel is already live/degraded, so a late
   *  joiner still gets its own status + one reconcile instead of hanging
   *  in "connecting" forever. */
  lastStatus: string | null
}

/** Module-scope registry: topic -> the one shared channel entry for it.
 *  Every `useRealtimeChannel` mount for the same topic (regardless of which
 *  component or how many are alive at once) reads/writes this map instead
 *  of ever creating its own `supabase.channel(topic, ...)`. */
const sharedChannels = new Map<string, SharedChannelEntry>()
let nextConsumerId = 0

export function useRealtimeChannel(
  topic: string | null,
  handlers: RealtimeChannelHandlers,
): UseRealtimeChannelResult {
  const [status, setStatus] = useState<ChannelStatus>(() =>
    topic && isSupabaseConfigured() ? "connecting" : "degraded",
  )
  const [presenceMembers, setPresenceMembers] = useState<PresenceIdentity[]>([])
  const [typers, setTypers] = useState<PresenceIdentity[]>([])

  // Handlers live in a ref so a consumer passing fresh inline callbacks on
  // every render does NOT tear down + rebuild the channel — channel identity
  // keys on `topic` only (AC-8).
  const handlersRef = useRef(handlers)
  handlersRef.current = handlers

  const channelRef = useRef<RealtimeChannel | null>(null)
  // One expiry timer per typing sender, keyed by userId — refreshed on every
  // new `typing` event, cleared when the entry expires or the channel tears
  // down. No server "stopped typing" signal exists, so expiry is purely
  // client-side (AD-P24).
  const typerTimersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map())
  const lastTypingSentAtRef = useRef(0)
  // Stable per-mount identity this hook instance registers itself under in
  // the shared registry — one id for the whole mount lifetime, regardless
  // of topic changes (a topic change just re-registers under the new key).
  const consumerIdRef = useRef<number | null>(null)
  if (consumerIdRef.current === null) consumerIdRef.current = ++nextConsumerId

  const clearAllTypers = () => {
    for (const timer of typerTimersRef.current.values()) clearTimeout(timer)
    typerTimersRef.current.clear()
  }

  useEffect(() => {
    // Fresh presence/typing state per topic — a prior project's roster/typers
    // must never leak into the next one.
    clearAllTypers()
    setPresenceMembers([])
    setTypers([])

    if (!topic || !isSupabaseConfigured()) {
      setStatus("degraded")
      channelRef.current = null
      return
    }

    setStatus("connecting")

    const supabase = getSupabase()
    let cancelled = false
    const consumerId = consumerIdRef.current as number

    const upsertTyper = (member: PresenceIdentity) => {
      const existing = typerTimersRef.current.get(member.userId)
      if (existing) clearTimeout(existing)
      setTypers((prev) => [...prev.filter((t) => t.userId !== member.userId), member])
      const timer = setTimeout(() => {
        typerTimersRef.current.delete(member.userId)
        setTypers((prev) => prev.filter((t) => t.userId !== member.userId))
      }, TYPING_EXPIRY_MS)
      typerTimersRef.current.set(member.userId, timer)
    }

    let entry = sharedChannels.get(topic)
    // Only an ALREADY-live entry has anything worth replaying — a brand
    // new entry's channel hasn't synced presence or reached a subscribe
    // status yet, so its first consumer follows the exact same path this
    // hook always has.
    const isExistingEntry = Boolean(entry)

    if (!entry) {
      // First consumer for this topic — create the ONE channel it (and any
      // later consumer of the same topic) will share. Private channels
      // require the Realtime client to carry the user's JWT before the
      // join is attempted, so the channel-auth RLS policy can see who's
      // asking. No-arg setAuth() adopts the session already on the client
      // (see supabase-js RealtimeClient.setAuth).
      void supabase.realtime.setAuth()

      const channel: RealtimeChannel = supabase.channel(topic, {
        config: { private: true },
      })

      const newEntry: SharedChannelEntry = {
        channel,
        refCount: 0,
        consumers: new Map(),
        lastStatus: null,
      }
      entry = newEntry
      sharedChannels.set(topic, newEntry)

      // Registered exactly ONCE per topic, before `subscribe()` is ever
      // called — every later consumer of this topic is fanned events out
      // of these same two listeners, never adds its own (that second
      // `.on()` after the channel is already joined/joining is exactly the
      // crash this refactor fixes).
      channel.on("broadcast", { event: "*" }, ({ event, payload }) => {
        for (const consumer of newEntry.consumers.values()) consumer.onBroadcast(event, payload)
      })

      // Presence `sync` is the fully-reconciled roster after every
      // join/leave (incl. a hard tab close reaping the dropped connection)
      // — reading `presenceState()` here is sufficient, no separate
      // join/leave handling needed (AD-P24).
      channel.on("presence", { event: "sync" }, () => {
        for (const consumer of newEntry.consumers.values()) consumer.onPresenceSync()
      })

      channel.subscribe((subscribeStatus) => {
        newEntry.lastStatus = subscribeStatus
        for (const consumer of newEntry.consumers.values()) consumer.onStatus(subscribeStatus)
      })
    }

    const sharedEntry = entry

    const consumer: SharedChannelConsumer = {
      onBroadcast: (event, payload) => {
        if (cancelled) return
        if (event === "typing" && payload && typeof (payload as { userId?: unknown }).userId === "string") {
          const typer = payload as PresenceIdentity
          const selfId = handlersRef.current.presence?.self.userId
          if (typer.userId !== selfId) upsertTyper(typer)
        }
        handlersRef.current.onEvent?.(event, payload)
      },
      onPresenceSync: () => {
        if (cancelled) return
        setPresenceMembers(flattenPresenceState(sharedEntry.channel.presenceState() as RawPresenceState))
      },
      onStatus: (subscribeStatus) => {
        if (cancelled) return
        if (subscribeStatus === "SUBSCRIBED") {
          setStatus("live")
          // Exactly one reconcile per (re)subscribe transition THIS
          // consumer observes — never on a plain broadcast event. A
          // consumer that attaches to an already-live shared channel
          // observes its own "just joined" transition via the replay
          // below, so it still gets its one reconcile.
          handlersRef.current.onReconcile?.()
          const self = handlersRef.current.presence?.self
          if (self) void sharedEntry.channel.track({ userId: self.userId, name: self.name })
          return
        }
        // CHANNEL_ERROR / TIMED_OUT / CLOSED all fall back to the same
        // degraded state — the consumer's existing poll takes over.
        // Presence and typing are delight, not load-bearing (RR7): they
        // simply go empty, no error, no fallback poll of their own.
        setStatus("degraded")
        setPresenceMembers([])
        clearAllTypers()
        setTypers([])
      },
    }

    sharedEntry.refCount++
    sharedEntry.consumers.set(consumerId, consumer)
    channelRef.current = sharedEntry.channel

    // A consumer that mounts AFTER this topic's channel already reached a
    // status (live or degraded) must not sit in "connecting" forever —
    // replay the current state (and current presence roster) to THIS
    // consumer alone, without touching any already-attached consumer.
    if (isExistingEntry) {
      consumer.onPresenceSync()
      if (sharedEntry.lastStatus !== null) consumer.onStatus(sharedEntry.lastStatus)
    }

    return () => {
      cancelled = true
      channelRef.current = null
      clearAllTypers()
      const liveEntry = sharedChannels.get(topic)
      if (!liveEntry || liveEntry !== sharedEntry) return
      liveEntry.consumers.delete(consumerId)
      liveEntry.refCount--
      if (liveEntry.refCount <= 0) {
        sharedChannels.delete(topic)
        void supabase.removeChannel(liveEntry.channel)
      }
    }
  }, [topic])

  const sendTyping = useCallback((self: PresenceIdentity) => {
    const channel = channelRef.current
    if (!channel) return
    const now = Date.now()
    if (now - lastTypingSentAtRef.current < TYPING_SEND_THROTTLE_MS) return
    lastTypingSentAtRef.current = now
    void channel.send({ type: "broadcast", event: "typing", payload: self })
  }, [])

  return { status, degraded: status !== "live", presenceMembers, sendTyping, typers }
}
