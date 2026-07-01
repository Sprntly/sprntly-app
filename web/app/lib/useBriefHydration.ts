"use client"

import { useEffect, useRef, useState } from "react"
import { useContent } from "../context/ContentContext"
import { briefApi, ApiError, type Brief } from "./api"
import { briefToContentPatch } from "./brief-adapter"
import { sleepUntilNextPoll } from "./poll"
import { useConnectorConnectedSignal } from "./useConnectorConnectedSignal"

type HydrationState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready"; brief: Brief }
  | { kind: "generating" }
  | { kind: "failed"; error: string }
  | { kind: "empty" }

export type BriefHydration = {
  /** The initial-load lifecycle: idle → loading → ready | generating | failed | empty. */
  state: HydrationState
  /** A fresh brief is being built *over* a still-cached one (e.g. after a
   *  connector was added and the workspace is regenerating). The current brief
   *  keeps rendering; the home surface shows a lightweight "refreshing" banner. */
  regenerating: boolean
}

const POLL_MS = 5000
const MAX_POLL_MS = 5 * 60 * 1000 // 5 min ceiling on polling

/**
 * Same-tab event a caller dispatches the moment it kicks off a brief
 * regeneration (e.g. the "Regenerate brief" button, shipped in a parallel PR),
 * so the brief surface starts watching for the in-flight regen immediately —
 * independent of connector-connect timing. Fire it via {@link notifyBriefRegenerating}.
 */
export const BRIEF_REGENERATING_EVENT = "sprntly:brief-regenerating"

/**
 * Signal that a brief regeneration was just triggered. Any mounted
 * `useBriefHydration` (i.e. the home surface) then starts its bounded watch and
 * shows the "refreshing your brief" banner while it runs. No-op during SSR.
 *
 * Integration point for the parallel regenerate-button work: call this right
 * after the regenerate request is accepted.
 */
export function notifyBriefRegenerating(): void {
  if (typeof window === "undefined") return
  window.dispatchEvent(new Event(BRIEF_REGENERATING_EVENT))
}

/**
 * On mount, fetch the current brief and push it into ContentContext.
 * If the backend has no brief yet, poll /v1/brief/status until ready
 * (or for at most 5 minutes), then re-fetch.
 *
 * Separately, watch for a *regeneration running over an already-cached brief*.
 * The backend keeps `status: "ready"` in that case (so the current brief stays
 * on screen) but flags `regenerating: true`. We surface that as `regenerating`
 * and, when it finishes, re-fetch /current to swap in the fresh brief. The watch
 * (re)starts whenever a connector is added (the connector-connected signal) or a
 * regeneration is kicked off ({@link BRIEF_REGENERATING_EVENT}), and also does a
 * one-shot check on mount to catch a reload mid-regeneration.
 *
 * Safe to call from anywhere inside the AuthGate. Re-runs on company change.
 */
export function useBriefHydration(company: string = "asurion"): BriefHydration {
  const { setContent } = useContent()
  const [state, setState] = useState<HydrationState>({ kind: "idle" })
  const [regenerating, setRegenerating] = useState(false)
  const cancelled = useRef(false)
  // Bumped to (re)start the regen watch: >0 means "actively wait for a regen to
  // appear" (a connector was just added, or a regeneration was kicked off); the
  // initial 0 value only does a one-shot mount check.
  const [regenWatchNonce, setRegenWatchNonce] = useState(0)

  // ── Initial load + first-run generation polling ────────────────────────────
  useEffect(() => {
    cancelled.current = false
    setState({ kind: "loading" })

    const start = Date.now()

    async function loadBrief(): Promise<void> {
      try {
        const brief = await briefApi.current(company)
        if (cancelled.current) return
        setContent(briefToContentPatch(brief))
        setState({ kind: "ready", brief })
      } catch (e) {
        if (cancelled.current) return
        if (e instanceof ApiError && e.status === 404) {
          // Backend has no brief yet — check status
          await pollUntilReady()
        } else if (e instanceof ApiError && e.status === 401) {
          // Not signed in; AuthGate handles redirect, just stop here.
          setState({ kind: "failed", error: "Not signed in" })
        } else {
          const msg = e instanceof Error ? e.message : String(e)
          setState({ kind: "failed", error: msg })
        }
      }
    }

    async function pollUntilReady(): Promise<void> {
      let autoTriggered = false
      while (!cancelled.current && Date.now() - start < MAX_POLL_MS) {
        try {
          const s = await briefApi.status(company)
          if (cancelled.current) return
          if (s.status === "ready") {
            // Brief is now cached — fetch it
            return loadBrief()
          }
          if (s.status === "failed") {
            setState({ kind: "failed", error: s.error || "Brief generation failed" })
            return
          }
          if (s.status === "empty" && !autoTriggered) {
            // No brief and nothing in progress — kick off background
            // generation so the user doesn't have to do it manually.
            autoTriggered = true
            setState({ kind: "generating" })
            try {
              await briefApi.regenerate(company)
            } catch {
              // regenerate failed (e.g. 404 dataset not found) — stop polling
              setState({ kind: "empty" })
              return
            }
            await sleepUntilNextPoll(POLL_MS)
            continue
          }
          if (s.status === "generating" || s.status === "empty") {
            setState({ kind: "generating" })
            await sleepUntilNextPoll(POLL_MS)
            continue
          }
          setState({ kind: "empty" })
          return
        } catch (e) {
          const msg = e instanceof Error ? e.message : String(e)
          setState({ kind: "failed", error: msg })
          return
        }
      }
    }

    void loadBrief()

    return () => {
      cancelled.current = true
    }
  }, [company, setContent])

  // ── Regeneration-over-existing-brief watch ─────────────────────────────────
  // On mount (nonce 0) this is a cheap one-shot: if a regen is already in flight
  // it watches to completion, otherwise it stops. After a connector is added
  // (nonce > 0) it actively polls for up to 5 min, waiting for the resulting
  // regen to appear and then finish.
  useEffect(() => {
    let stopped = false
    const activelyWaiting = regenWatchNonce > 0
    const start = Date.now()

    async function watch(): Promise<void> {
      let sawRegen = false
      while (!stopped && Date.now() - start < MAX_POLL_MS) {
        let s
        try {
          s = await briefApi.status(company)
        } catch {
          return // transient — a later trigger can restart the watch
        }
        if (stopped) return

        if (s.regenerating) {
          sawRegen = true
          setRegenerating(true)
        } else {
          if (sawRegen) {
            // Regen just finished — swap in the fresh brief, then stop.
            try {
              const brief = await briefApi.current(company)
              if (!stopped) setContent(briefToContentPatch(brief))
            } catch {
              /* keep the existing brief on any fetch hiccup */
            }
            if (!stopped) setRegenerating(false)
            return
          }
          if (!activelyWaiting) {
            // One-shot mount check: nothing in flight, nothing to watch.
            return
          }
          // Actively waiting for a regen to start — keep polling.
        }
        await sleepUntilNextPoll(POLL_MS)
      }
      // Window elapsed (or stopped): clear any lingering indicator.
      if (!stopped) setRegenerating(false)
    }

    void watch()

    return () => {
      stopped = true
    }
  }, [company, regenWatchNonce, setContent])

  // Restart the regen watch every time a connector is added, so the "refreshing
  // your brief" indicator appears while the resulting regeneration runs.
  useConnectorConnectedSignal(() => setRegenWatchNonce((n) => n + 1))

  // …and whenever a regeneration is explicitly kicked off (e.g. the parallel
  // "Regenerate brief" button calling notifyBriefRegenerating), so the banner
  // shows even without a preceding connector-connect.
  useEffect(() => {
    if (typeof window === "undefined") return
    const onRegen = () => setRegenWatchNonce((n) => n + 1)
    window.addEventListener(BRIEF_REGENERATING_EVENT, onRegen)
    return () => window.removeEventListener(BRIEF_REGENERATING_EVENT, onRegen)
  }, [])

  return { state, regenerating }
}
