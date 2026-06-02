"use client"

/**
 * P2-10 — Completion + handoff chrome for a generated prototype (F14 Mark
 * Complete, F15 Resume Iteration, F16 Export Download/Copy, F17 export gating).
 *
 * Mounts into the `<PrototypeViewer>` chrome slot (P2-05). Two flavours, gated
 * by `editable`:
 *   - Public viewer (`/p/<token>`): read-only status badge, NO mutating buttons,
 *     NO prototypeId required (the public resolver is minimum-disclosure).
 *   - Signed-in surface (post-P2 follow-up): full button surface; `prototypeId`
 *     required to address the mutation routes.
 *
 * Testability split mirrors `DesignAgentDrawer.tsx`: the repo's vitest runs in a
 * `node` env with no jsdom / @testing-library, so the pure markup lives in
 * `CompletionBarView` (SSR-renderable via `renderToStaticMarkup`) and the submit
 * orchestration lives in exported pure async helpers (`runMarkComplete`,
 * `runResume`, `runDownloadMarkdown`, `runCopyMarkdown`) that take their deps as
 * arguments. The container wires React state to those units.
 *
 * Per BUILD.md §6 this file adds NO CSS to the hot `globals.css`; it uses repo
 * class names (`btn`, `btn-accent`) + component-scoped class strings only.
 */

import { useState } from "react"
import { designAgentApi } from "../../lib/api"

export type CompletionBarProps = {
  /** Required only when `editable` is true (the mutation routes are addressed
   *  by prototype id). Omitted on the read-only public viewer. */
  prototypeId?: number
  isComplete: boolean
  isStaleHandoff?: boolean
  /** Defaults to true — the signed-in surface is the primary call site. The
   *  public viewer passes false to render the read-only badge. */
  editable?: boolean
  onStateChange?: (state: { isComplete: boolean; staleHandoff: boolean }) => void
}

export type CompletionBarViewProps = {
  prototypeId?: number
  isComplete: boolean
  editable?: boolean
  isStaleHandoff?: boolean
  busy?: boolean
  error?: string | null
  onMarkComplete?: () => void
  onResume?: () => void
  onDownload?: () => void
  onCopy?: () => void
}

/** Shown in both editable + read-only branches when a handoff was reopened
 *  (F15). The "out of date" wording is asserted by the stale-handoff AC. */
const STALE_MESSAGE =
  "This prototype was reopened after a handoff. The export bundle may be out of date."

function toMessage(err: unknown, fallback: string): string {
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
 * F16 — fetch the markdown export and trigger a browser download. Uses the
 * global `document` / `URL` / `Blob` so tests can stub `document.createElement`
 * and `URL.createObjectURL` directly. Resolves with the markdown body; rejects
 * (propagating the underlying error, e.g. a 409 WIP) if the export fails.
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

// ---- pure view --------------------------------------------------------------

/** Pure presentational view — no hooks, no I/O → SSR-renderable in node-env
 *  vitest. The container threads live state + handlers into it. */
export function CompletionBarView({
  isComplete,
  editable = true,
  isStaleHandoff = false,
  busy = false,
  error = null,
  onMarkComplete,
  onResume,
  onDownload,
  onCopy,
}: CompletionBarViewProps) {
  if (!editable) {
    return (
      <div
        className="completion-bar completion-bar--readonly"
        data-testid="completion-bar-readonly"
      >
        <span className="completion-bar-status">
          {isComplete ? "Marked Complete" : "Work in progress"}
        </span>
        {isStaleHandoff && (
          <div className="stale-banner" data-testid="stale-banner">
            {STALE_MESSAGE}
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="completion-bar" data-testid="completion-bar">
      {isStaleHandoff && (
        <div className="stale-banner" data-testid="stale-banner">
          {STALE_MESSAGE}
        </div>
      )}
      {!isComplete ? (
        <>
          <button
            type="button"
            className="btn btn-accent"
            onClick={onMarkComplete}
            disabled={busy}
            data-testid="mark-complete-btn"
          >
            Mark Complete
          </button>
          <span
            className="export-disabled-tooltip"
            title="Mark prototype complete first"
          >
            Export disabled
          </span>
        </>
      ) : (
        <>
          <button
            type="button"
            className="btn"
            onClick={onResume}
            disabled={busy}
            data-testid="resume-btn"
          >
            Resume Iteration
          </button>
          <button
            type="button"
            className="btn"
            onClick={onDownload}
            disabled={busy}
            data-testid="download-md-btn"
          >
            Download .md
          </button>
          <button
            type="button"
            className="btn"
            onClick={onCopy}
            disabled={busy}
            data-testid="copy-md-btn"
          >
            Copy to clipboard
          </button>
        </>
      )}
      {error && (
        <p className="error" data-testid="completion-bar-error">
          {error}
        </p>
      )}
    </div>
  )
}

// ---- container --------------------------------------------------------------

/** Public component. Wires React state to the orchestration helpers and the
 *  canonical `designAgentApi`, then delegates rendering to the pure view. */
export function CompletionBar({
  prototypeId,
  isComplete,
  isStaleHandoff = false,
  editable = true,
  onStateChange,
}: CompletionBarProps) {
  const [busy, setBusy] = useState(false)
  const [localComplete, setLocalComplete] = useState(isComplete)
  const [error, setError] = useState<string | null>(null)

  if (
    editable &&
    prototypeId == null &&
    process.env.NODE_ENV !== "production"
  ) {
    // Dev-only guard: editable controls mutate a specific prototype, so a
    // missing id is a wiring bug rather than a silent no-op.
    console.warn(
      "CompletionBar: editable=true requires a prototypeId; controls are inert.",
    )
  }

  const canMutate = prototypeId != null

  async function handleMarkComplete() {
    if (!canMutate) return
    setBusy(true)
    setError(null)
    try {
      const res = await runMarkComplete({ prototypeId: prototypeId!, api: designAgentApi })
      setLocalComplete(res.is_complete)
      onStateChange?.({ isComplete: res.is_complete, staleHandoff: false })
    } catch (e) {
      setError(toMessage(e, "Failed to mark complete"))
    } finally {
      setBusy(false)
    }
  }

  async function handleResume() {
    if (!canMutate) return
    setBusy(true)
    setError(null)
    try {
      const res = await runResume({ prototypeId: prototypeId!, api: designAgentApi })
      setLocalComplete(res.is_complete)
      onStateChange?.({ isComplete: res.is_complete, staleHandoff: false })
    } catch (e) {
      setError(toMessage(e, "Failed to resume"))
    } finally {
      setBusy(false)
    }
  }

  async function handleDownload() {
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

  async function handleCopy() {
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

  return (
    <CompletionBarView
      prototypeId={prototypeId}
      isComplete={localComplete}
      editable={editable}
      isStaleHandoff={isStaleHandoff}
      busy={busy}
      error={error}
      onMarkComplete={handleMarkComplete}
      onResume={handleResume}
      onDownload={handleDownload}
      onCopy={handleCopy}
    />
  )
}
