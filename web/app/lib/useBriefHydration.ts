"use client"

import { useEffect, useRef, useState } from "react"
import { useContent } from "../context/ContentContext"
import { briefApi, ApiError, type Brief } from "./api"
import { briefToContentPatch } from "./brief-adapter"
import { sleepUntilNextPoll } from "./poll"

type HydrationState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready"; brief: Brief }
  | { kind: "generating" }
  | { kind: "failed"; error: string }
  | { kind: "empty" }

const POLL_MS = 5000
const MAX_POLL_MS = 5 * 60 * 1000 // 5 min ceiling on polling

/**
 * On mount, fetch the current brief and push it into ContentContext.
 * If the backend is still generating, poll /v1/brief/status until ready
 * (or for at most 5 minutes), then re-fetch.
 *
 * Safe to call from anywhere inside the AuthGate. Re-runs on company change.
 */
export function useBriefHydration(company: string = "asurion"): HydrationState {
  const { setContent } = useContent()
  const [state, setState] = useState<HydrationState>({ kind: "idle" })
  const cancelled = useRef(false)

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

  return state
}
