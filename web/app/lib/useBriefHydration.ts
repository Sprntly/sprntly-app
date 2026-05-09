"use client"

import { useEffect, useRef, useState } from "react"
import { useContent } from "../context/ContentContext"
import { briefApi, ApiError, type Brief } from "./api"
import { briefToContentPatch } from "./brief-adapter"

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
 * Safe to call from anywhere inside the AuthGate. Re-runs on dataset change.
 */
export function useBriefHydration(dataset: string = "asurion"): HydrationState {
  const { setContent } = useContent()
  const [state, setState] = useState<HydrationState>({ kind: "idle" })
  const cancelled = useRef(false)

  useEffect(() => {
    cancelled.current = false
    setState({ kind: "loading" })

    const start = Date.now()

    async function loadBrief(): Promise<void> {
      try {
        const brief = await briefApi.current(dataset)
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
      while (!cancelled.current && Date.now() - start < MAX_POLL_MS) {
        try {
          const s = await briefApi.status(dataset)
          if (cancelled.current) return
          if (s.status === "ready") {
            // Brief is now cached — fetch it
            return loadBrief()
          }
          if (s.status === "failed") {
            setState({ kind: "failed", error: s.error || "Brief generation failed" })
            return
          }
          if (s.status === "generating" || s.status === "empty") {
            setState({ kind: "generating" })
            await sleep(POLL_MS)
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
  }, [dataset, setContent])

  return state
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms))
}
