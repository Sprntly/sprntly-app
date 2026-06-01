"use client"

/**
 * F2 launcher — the "Generate Prototype" entry point that lives inside the
 * PRD's Design section (rendered by PrdSections' `prd-design` block). It owns
 * the drawer open/close state locally with `useState` (Path A): the
 * `'design-agent'` member in NavigationContext's drawer-kind union stays as
 * forward-compat for a future Cmd+K palette entry but is NOT driven from here.
 *
 * The `contentEditable={false}` wrapper is load-bearing. The Design section
 * renders inside the PRD's contentEditable region; without it the button is
 * swallowed by the editable focus and clicks misbehave.
 *
 * Testability split mirrors DesignAgentDrawer: the container owns `useState`,
 * the pure `DesignAgentLauncherView` holds the SSR-renderable markup, and the
 * drawer is injected via `renderDrawer` (defaulting to the real
 * `DesignAgentDrawer`). The default drawer wires `useNavigation`, so injecting
 * a stub keeps the launcher renderable under the repo's node-env vitest (no
 * NavigationContext provider, no @testing-library).
 */

import { useState, type ReactNode } from "react"
import { DesignAgentDrawer } from "./DesignAgentDrawer"
import { PostGenerationResult } from "./PostGenerationResult"
import { CommentsPanel } from "./CommentsPanel"
import { IterateComposer } from "./IterateComposer"
import { ClarifyingQuestionSurface } from "./ClarifyingQuestionSurface"
import type { CommentRecord, PrototypeRecord } from "../../lib/api"
import type { DesignAgentGenResult } from "../../lib/runDesignAgentGeneration"

export type DesignAgentLauncherProps = {
  prdId: number
  figmaFileKey?: string | null
}

/** Props the launcher hands to whatever drawer it mounts. Mirrors
 *  DesignAgentDrawerProps so the default renderer and any test stub agree. */
export type LauncherDrawerProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  prdId: number
  figmaFileKey?: string | null
  /** P2-12: drawer reports the terminal generation outcome here so the
   *  container can mount the post-generation result view. */
  onGenerated?: (result: DesignAgentGenResult) => void
}

/** P2-12: maps a generation outcome to launcher result state — the prototype
 *  on success, null on failure (the drawer's existing toast surfaces the error;
 *  AC5: no result view on failure). Pure → unit-testable without a DOM. */
export function resultFromGeneration(
  result: DesignAgentGenResult,
): PrototypeRecord | null {
  return result.ok ? result.prototype : null
}

/** Default drawer renderer: the real, NavigationContext-wired DesignAgentDrawer. */
export const defaultRenderDrawer = (props: LauncherDrawerProps): ReactNode => (
  <DesignAgentDrawer {...props} />
)

type LauncherViewProps = DesignAgentLauncherProps & {
  open: boolean
  setOpen: (open: boolean) => void
  /** P2-12: the generated prototype to show post-generation. Null → no result
   *  view yet (the Generate button is the only chrome). Optional/defaulted so
   *  existing direct-view test calls keep typechecking. */
  result?: PrototypeRecord | null
  /** P2-12: handed to the drawer so a successful generation populates `result`. */
  onGenerated?: (result: DesignAgentGenResult) => void
  /** P3-14 (F10): the comment selected for Apply, lifted to the container so
   *  CommentsPanel's Apply action sets it and IterateComposer reads it. Optional
   *  so existing direct-view test calls keep typechecking. */
  applyTarget?: CommentRecord | null
  /** P3-14 (F10): setter for `applyTarget` (CommentsPanel onApply → set;
   *  IterateComposer onClearApply → clear). */
  setApplyTarget?: (comment: CommentRecord | null) => void
  /** Injected in tests so the view renders without NavigationContext. */
  renderDrawer?: (props: LauncherDrawerProps) => ReactNode
}

/**
 * Pure, SSR-renderable view: the `contentEditable={false}` wrapper, the
 * "Generate Prototype" button, the (closed-by-default) drawer, and — once a
 * generation has succeeded — the editable `PostGenerationResult` chrome. The
 * result mounts INSIDE the same `contentEditable={false}` boundary so it never
 * interferes with the PRD body's `contentEditable` (PrdScreen antipattern guard).
 */
export function DesignAgentLauncherView({
  prdId,
  figmaFileKey,
  open,
  setOpen,
  result = null,
  onGenerated,
  applyTarget = null,
  setApplyTarget,
  renderDrawer = defaultRenderDrawer,
}: LauncherViewProps) {
  return (
    <div className="prd-design-launcher" contentEditable={false}>
      <button
        type="button"
        className="btn btn-accent"
        onClick={() => setOpen(true)}
      >
        Generate Prototype
      </button>
      {/* `key` forces a clean remount per prototype id: PostGenerationResult
          (and the CompletionBar it mounts) seed state from props at mount only,
          so regenerating a second prototype in the same launcher instance must
          remount to avoid carrying the prior prototype's is_complete. */}
      {result && <PostGenerationResult key={result.id} prototype={result} />}
      {/* P3-14 (F10): signed-in CommentsPanel mount — the public mount lives in
          PublicTokenViewer (P3-03). Comments are addressed by the share token,
          so this mounts only once the prototype is shared. `onApply` enables the
          Apply→IterateComposer handoff (absent on the public mount → no Apply). */}
      {result && result.share_token && (
        <CommentsPanel
          key={`comments-${result.id}`}
          token={result.share_token}
          prototypeId={result.id}
          onApply={(comment) => setApplyTarget?.(comment)}
        />
      )}
      {/* P3-14 (F9/F10): the iterate trigger surface — re-prompt always available
          (when unlocked); Apply pre-fills from `applyTarget`. Mounted ONLY here
          (authed surface), never in the public route. */}
      {result && (
        <IterateComposer
          key={`iterate-${result.id}`}
          prototypeId={result.id}
          isComplete={result.is_complete ?? false}
          applyTarget={applyTarget}
          onClearApply={() => setApplyTarget?.(null)}
        />
      )}
      {/* P3-16 (F12): the clarifying-question answer surface — rendered ONLY when
          the agent has paused with a pending question and the prototype is not
          locked. The answer routes through the reused P3-14 iterate (no new
          method). Mounted ONLY here (authed surface), never in the public route
          (external viewers cannot answer/iterate). */}
      {result &&
        result.pending_question != null &&
        !(result.is_complete ?? false) && (
          <ClarifyingQuestionSurface
            key={`clarify-${result.id}`}
            prototype={result}
          />
        )}
      {renderDrawer({
        open,
        onOpenChange: setOpen,
        prdId,
        figmaFileKey,
        onGenerated,
      })}
    </div>
  )
}

/**
 * Public component. Owns the drawer open/close state locally and delegates
 * rendering to the pure view. `renderDrawer` is optional (defaults to the real
 * drawer) — production callers pass only `prdId` / `figmaFileKey`.
 */
export function DesignAgentLauncher({
  prdId,
  figmaFileKey,
  renderDrawer,
}: DesignAgentLauncherProps & {
  renderDrawer?: (props: LauncherDrawerProps) => ReactNode
}) {
  const [open, setOpen] = useState(false)
  const [result, setResult] = useState<PrototypeRecord | null>(null)
  // P3-14 (F10): lifted so CommentsPanel's Apply sets it and IterateComposer
  // reads it as its pre-fill.
  const [applyTarget, setApplyTarget] = useState<CommentRecord | null>(null)

  // On a successful generation, mount the result view. On failure, leave the
  // current state intact — the drawer's existing toast surfaces the error and
  // no result view renders (AC5).
  const handleGenerated = (outcome: DesignAgentGenResult) => {
    const next = resultFromGeneration(outcome)
    if (next) setResult(next)
  }

  return (
    <DesignAgentLauncherView
      prdId={prdId}
      figmaFileKey={figmaFileKey}
      open={open}
      setOpen={setOpen}
      result={result}
      onGenerated={handleGenerated}
      applyTarget={applyTarget}
      setApplyTarget={setApplyTarget}
      renderDrawer={renderDrawer}
    />
  )
}
