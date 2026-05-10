"use client"

import { useEffect, useMemo, useState } from "react"
import { buildAnswerStreamChunks } from "./buildAnswerStreamChunks"

function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false)
  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)")
    setReduced(mq.matches)
    const on = () => setReduced(mq.matches)
    mq.addEventListener("change", on)
    return () => mq.removeEventListener("change", on)
  }, [])
  return reduced
}

const STREAM_START_MS = 140

function delayForChunk(prev: string, next: string): number {
  const delta = Math.max(1, next.length - prev.length)
  return Math.min(1200, Math.max(48, 32 * Math.sqrt(delta)))
}

export function useAnswerSimulatedStream(markdown: string, enabled: boolean) {
  const reduced = usePrefersReducedMotion()
  const chunks = useMemo(() => buildAnswerStreamChunks(markdown), [markdown])
  const [index, setIndex] = useState(0)

  const active = enabled && !reduced && chunks.length > 1
  const done = !active || index >= chunks.length - 1
  const visible = active ? chunks[Math.min(index, chunks.length - 1)] : markdown
  const isStreaming = active && !done

  useEffect(() => {
    if (!enabled || reduced) {
      setIndex(chunks.length - 1)
      return
    }
    if (chunks.length <= 1) {
      setIndex(0)
      return
    }

    let i = 0
    setIndex(0)
    let cancelled = false
    let timeoutId: ReturnType<typeof setTimeout> | undefined

    const tick = () => {
      if (cancelled) return
      if (i >= chunks.length - 1) {
        setIndex(chunks.length - 1)
        return
      }
      const ms = delayForChunk(chunks[i], chunks[i + 1])
      timeoutId = setTimeout(() => {
        if (cancelled) return
        i += 1
        setIndex(i)
        tick()
      }, ms)
    }

    timeoutId = setTimeout(() => {
      if (cancelled) return
      tick()
    }, STREAM_START_MS)

    return () => {
      cancelled = true
      if (timeoutId !== undefined) clearTimeout(timeoutId)
    }
  }, [enabled, reduced, chunks])

  return { visible, done, isStreaming }
}
