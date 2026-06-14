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
// Re-export pure helpers for test compat — CompletionBar.test.tsx imports
// runMarkComplete, runResume, runDownloadMarkdown, runCopyMarkdown from this module.
export {
  runMarkComplete,
  runResume,
  runDownloadMarkdown,
  runCopyMarkdown,
  STALE_MESSAGE,
} from "./handoff-actions"
import {
  STALE_MESSAGE,
  toMessage,
  useHandoffActions,
} from "./handoff-actions"

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

// ---- helpers and hook delegated to ./handoff-actions (see re-export above) --

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
          {/* P6-14 (UX-4) — distinct "hand this to my coding agent" action,
              gated to Complete/locked (F16/F17). Disabled until complete; the
              server /export also 409s on WIP (defence-in-depth). */}
          <button
            type="button"
            className="btn-export"
            disabled
            data-testid="export-claude-code-btn"
          >
            Export to Claude Code
          </button>
          <span className="export-claude-code-caption">
            Available once the prototype is marked Complete.
          </span>
        </>
      ) : (
        <>
          {/* UX-EXPLORE (throwaway — REVERT, CHANGE 1): the "Resume Iteration"
              button is removed — the left composer is now usable by default, so
              there is no manual resume gate to click. `onResume` / `runResume`
              are kept on the component for the actual backend semantics, just no
              longer surfaced as a button here. */}
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
          {/* P6-14 (UX-4) — the distinct primary handoff action. REUSES the
              existing onDownload prop (→ runDownloadMarkdown → exportMarkdown);
              no parallel onExportClaudeCode prop (Check-25 reuse). The
              distinction from "Download .md" is the label + btn-export ink. */}
          <button
            type="button"
            className="btn-export"
            onClick={onDownload}
            disabled={busy}
            data-testid="export-claude-code-btn"
          >
            Export to Claude Code
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
  const [localComplete, setLocalComplete] = useState(isComplete)

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

  const { busy, error, markComplete, resume, download, copy } = useHandoffActions({
    prototypeId,
    onStateChange: (s) => { setLocalComplete(s.isComplete); onStateChange?.(s) },
  })

  return (
    <CompletionBarView
      prototypeId={prototypeId}
      isComplete={localComplete}
      editable={editable}
      isStaleHandoff={isStaleHandoff}
      busy={busy}
      error={error}
      onMarkComplete={markComplete}
      onResume={resume}
      onDownload={download}
      onCopy={copy}
    />
  )
}
