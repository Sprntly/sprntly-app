"use client"

import { useCallback, useEffect, useRef } from "react"
import { designAgentApi } from "../../lib/api"
import type { PrototypeRecord } from "../../lib/api"
import { prototypePath } from "../../lib/routes"
import { useNavigation } from "../../context/NavigationContext"
import { reasonCopy } from "../../components/design-agent/GenerationErrorBanner"
import { markPending } from "../../components/design-agent/notificationStore"

const TICK_MS = 4000
const MAX_MS = 6 * 60 * 1000

export type GenerationNotifyDeps = {
  getByPrd?: (prdId: number) => Promise<PrototypeRecord | null>
  sleep?: (ms: number) => Promise<void>
  now?: () => number
  deadlineMs?: number
}

/**
 * Shell-mounted hook. Listens for `da:notify-generation` events and for each
 * unique prototype id runs a completion poll. On `ready`, fires a persistent
 * actionable toast. On `failed`, fires a persistent failure toast. In both
 * terminal cases dispatches `da:generating-done`. Idempotent per prototype id.
 */
export function useGenerationNotify(deps: GenerationNotifyDeps = {}) {
  const { showToast } = useNavigation()
  const pollingIds = useRef<Set<number>>(new Set())

  const runPoll = useCallback(
    async (prototypeId: number, prdId: number) => {
      const getByPrd = deps.getByPrd ?? designAgentApi.getByPrd.bind(designAgentApi)
      const sleep = deps.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)))
      const now = deps.now ?? (() => Date.now())
      const deadline = now() + (deps.deadlineMs ?? MAX_MS)

      try {
        while (now() < deadline) {
          const proto = await getByPrd(prdId)
          if (proto == null) {
            await sleep(TICK_MS)
            continue
          }
          if (proto.status === "ready") {
            showToast(
              "Prototype ready",
              "Your prototype finished generating.",
              "Open",
              {
                persist: true,
                onAction: () => {
                  window.location.assign(prototypePath(prdId))
                },
              },
            )
            window.dispatchEvent(new CustomEvent("da:generating-done"))
            return
          }
          if (proto.status === "failed") {
            showToast(
              "Generation failed",
              reasonCopy(proto.error ?? "Generation failed", proto.id),
              undefined,
              { persist: true },
            )
            window.dispatchEvent(new CustomEvent("da:generating-done"))
            return
          }
          await sleep(TICK_MS)
        }
        // Deadline expired. The run may well still be going (the backend
        // routinely outlives this window). The user explicitly asked to be
        // notified, so hand off rather than dropping: re-arm the
        // sessionStorage recovery path, which resumePendingNotifications
        // retries on the next navigation/reload.
        markPending(prototypeId, prdId)
      } finally {
        pollingIds.current.delete(prototypeId)
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [showToast],
  )

  useEffect(() => {
    const onHandoff = (e: Event) => {
      const detail = (e as CustomEvent<{ prototypeId: number; prdId: number }>).detail
      if (!detail || typeof detail.prototypeId !== "number" || typeof detail.prdId !== "number") return
      const { prototypeId, prdId } = detail
      if (pollingIds.current.has(prototypeId)) return // idempotent
      pollingIds.current.add(prototypeId)
      void runPoll(prototypeId, prdId)
    }
    window.addEventListener("da:notify-generation", onHandoff)
    return () => window.removeEventListener("da:notify-generation", onHandoff)
  }, [runPoll])
}
