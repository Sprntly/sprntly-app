"use client"

/**
 * Shared handoff-action helpers + hook. Extracted from CompletionBar.tsx so
 * DaControlBar (in-tab surface) and CompletionBar (launcher/overlay surface)
 * share one source of truth for the Mark Complete / Resume / Export / Copy logic.
 */

import { useState } from "react"
import { designAgentApi } from "../../lib/api"

/** Shown when a handoff was reopened after a Mark Complete (F15). */
export const STALE_MESSAGE =
  "This prototype was reopened after a handoff. The export bundle may be out of date."

export function toMessage(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback
}

// ---- orchestration helpers (pure, dependency-injected, SSR-free) ------------

/** F14 — mark the prototype complete. */
export async function runMarkComplete({
  prototypeId,
  api,
}: {
  prototypeId: number
  api: Pick<typeof designAgentApi, "complete">
}) {
  return api.complete(prototypeId)
}

/** F15 — resume iteration on a completed prototype. */
export async function runResume({
  prototypeId,
  api,
}: {
  prototypeId: number
  api: Pick<typeof designAgentApi, "resume">
}) {
  return api.resume(prototypeId)
}

/**
 * F16 — fetch the markdown export and trigger a browser download.
 */
export async function runDownloadMarkdown({
  prototypeId,
  api,
}: {
  prototypeId: number
  api: Pick<typeof designAgentApi, "exportMarkdown">
}): Promise<string> {
  const md = await api.exportMarkdown(prototypeId)
  const blob = new Blob([md], { type: "text/markdown;charset=utf-8" })
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  a.download = `prototype-${prototypeId}-design-brief.md`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
  return md
}

/** F16 — fetch the markdown export and copy it to the clipboard. */
export async function runCopyMarkdown({
  prototypeId,
  api,
  clipboard,
}: {
  prototypeId: number
  api: Pick<typeof designAgentApi, "exportMarkdown">
  clipboard: Pick<Clipboard, "writeText">
}): Promise<string> {
  const md = await api.exportMarkdown(prototypeId)
  await clipboard.writeText(md)
  return md
}

// ---- shared hook ------------------------------------------------------------

/**
 * Encapsulates busy/error state + the four handoff actions for both
 * DaControlBar (in-tab) and CompletionBar (launcher/overlay).
 *
 * markComplete and resume return the API result so callers can read
 * is_complete / handoffs_flagged_stale; download/copy return void.
 */
export function useHandoffActions({
  prototypeId,
  onStateChange,
}: {
  prototypeId?: number
  onStateChange?: (s: { isComplete: boolean; staleHandoff: boolean }) => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const canMutate = prototypeId != null

  async function markComplete() {
    if (!canMutate) return undefined
    setBusy(true)
    setError(null)
    try {
      const res = await runMarkComplete({ prototypeId: prototypeId!, api: designAgentApi })
      onStateChange?.({ isComplete: res.is_complete, staleHandoff: false })
      return res
    } catch (e) {
      setError(toMessage(e, "Failed to mark complete"))
      return undefined
    } finally {
      setBusy(false)
    }
  }

  async function resume() {
    if (!canMutate) return undefined
    setBusy(true)
    setError(null)
    try {
      const res = await runResume({ prototypeId: prototypeId!, api: designAgentApi })
      onStateChange?.({ isComplete: res.is_complete, staleHandoff: false })
      return res
    } catch (e) {
      setError(toMessage(e, "Failed to resume"))
      return undefined
    } finally {
      setBusy(false)
    }
  }

  async function download() {
    if (!canMutate) return
    setBusy(true)
    setError(null)
    try {
      await runDownloadMarkdown({ prototypeId: prototypeId!, api: designAgentApi })
    } catch (e) {
      setError(toMessage(e, "Failed to download"))
    } finally {
      setBusy(false)
    }
  }

  async function copy() {
    if (!canMutate) return
    setBusy(true)
    setError(null)
    try {
      await runCopyMarkdown({
        prototypeId: prototypeId!,
        api: designAgentApi,
        clipboard: navigator.clipboard,
      })
    } catch (e) {
      setError(toMessage(e, "Failed to copy"))
    } finally {
      setBusy(false)
    }
  }

  return { busy, error, markComplete, resume, download, copy }
}
