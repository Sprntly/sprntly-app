"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { useContent } from "../context/ContentContext"
import { pipelineApi, briefApi, type PipelineRunStatus } from "./api"
import { briefToContentPatch } from "./brief-adapter"

// Polling intervals
const RUNNING_POLL_MS = 3_000
const IDLE_POLL_MS = 60_000
const COMPLETED_VISIBLE_MS = 4_000

export type PipelineHookResult = {
  runStatus: (PipelineRunStatus & { status: string }) | null
  isTriggering: boolean
  showCompleted: boolean
  triggerRun: () => Promise<void>
}

/**
 * Polls /v1/pipeline/{dataset}/status and keeps the UI in sync.
 *
 * - Polls every 3 s while the pipeline is running; every 60 s when idle.
 * - Detects the running → completed transition, silently reloads the brief
 *   into ContentContext, and surfaces a 4-second "refreshed" flash.
 * - Exposes triggerRun() for the "Run now" button.
 */
export function usePipelineStatus(company: string): PipelineHookResult {
  const { setContent } = useContent()
  const [runStatus, setRunStatus] = useState<(PipelineRunStatus & { status: string }) | null>(null)
  const [isTriggering, setIsTriggering] = useState(false)
  const [showCompleted, setShowCompleted] = useState(false)

  const prevStatusRef = useRef<string | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const cancelledRef = useRef(false)
  // Keep a stable ref so poll() doesn't close over stale state
  const companyRef = useRef(company)
  companyRef.current = company

  const schedulePoll = useCallback((delayMs: number, fn: () => Promise<void>) => {
    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => { void fn() }, delayMs)
  }, [])

  const poll = useCallback(async () => {
    if (cancelledRef.current) return
    const slug = companyRef.current

    let status: (PipelineRunStatus & { status: string }) | null = null
    try {
      // The backend may return { status: "no_runs" } which doesn't match the
      // full PipelineRunStatus shape — cast via unknown to handle both.
      status = (await pipelineApi.status(slug)) as unknown as typeof status
    } catch {
      // Network error — retry slowly
      schedulePoll(IDLE_POLL_MS, poll)
      return
    }

    if (cancelledRef.current) return

    setRunStatus(status)

    // Detect running → completed: reload the brief silently then flash
    if (prevStatusRef.current === "running" && status?.status === "completed") {
      try {
        const brief = await briefApi.current(slug)
        if (!cancelledRef.current) setContent(briefToContentPatch(brief))
      } catch {
        // Brief reload failed — the next useBriefHydration cycle will catch it
      }
      setShowCompleted(true)
      setTimeout(() => { if (!cancelledRef.current) setShowCompleted(false) }, COMPLETED_VISIBLE_MS)
    }

    prevStatusRef.current = status?.status ?? null

    const nextPoll = status?.status === "running" ? RUNNING_POLL_MS : IDLE_POLL_MS
    schedulePoll(nextPoll, poll)
  }, [schedulePoll, setContent])

  // Start polling when company changes
  useEffect(() => {
    cancelledRef.current = false
    prevStatusRef.current = null
    void poll()

    return () => {
      cancelledRef.current = true
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [company, poll])

  const triggerRun = useCallback(async () => {
    if (isTriggering) return
    setIsTriggering(true)
    try {
      await pipelineApi.run(company)
      // Kick an immediate poll to show the "running" state without waiting
      if (timerRef.current) clearTimeout(timerRef.current)
      schedulePoll(600, poll)
    } catch {
      // Silently ignore — user will see no state change
    } finally {
      setIsTriggering(false)
    }
  }, [company, isTriggering, poll, schedulePoll])

  return { runStatus, isTriggering, showCompleted, triggerRun }
}
