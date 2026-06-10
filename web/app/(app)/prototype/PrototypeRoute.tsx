"use client"

// Client surface for the dedicated /prototype route. This page is the prototype
// tab's landing: it reads the PRD context from the URL (?prd=<id>) via
// useSearchParams and renders the prototype canvas IN-TAB, inside the app shell
// (the sidebar stays visible). This is the refresh-stable surface: a static
// /prototype route + a ?prd query param, with no per-id dynamic segment.
//
// Three render states for ?prd=<id>:
//   1. resolving  — getByPrd(prdId) in flight → an in-tab loading view.
//   2. ready      — the PRD already has a ready prototype → the in-tab canvas
//                   (<PostGenerationResult>) wired exactly like the modal canvas:
//                   a local useIterateRun, the PRD context pulled by prd_id, the
//                   iterate + comments slots, share re-fetch, and reload nonce.
//   3. no proto   — no ready prototype yet → the always-open generate panel; a
//                   successful generation reveals the new prototype IN-TAB (no
//                   navigation to a full-screen overlay).
//
// Bare /prototype (no ?prd=) shows an empty state prompting the user to choose a
// PRD first.
//
// Co-located with the page exactly like web/app/p/[token]/PublicTokenViewer.tsx
// and web/app/(app)/onboarding/[step]/OnboardingStep.tsx — the server shell
// (page.tsx) satisfies static export; this owns the runtime behaviour. The PRD
// context is read from the URL client-side so no per-id dynamic segment is needed.
//
// The generation surface reuses the same GenerateModal as the approve flow (real
// connector/figma/repo wiring, the shared runGenerateFlow via
// designAgentApi.generate), rendered as the always-open panel. The
// GenerationLoadingScreen overlay provides kickoff-to-ready feedback. The
// figma_file_key is pulled from ContentContext when the loaded PRD matches the
// URL's prd id; it degrades to null otherwise.
//
// Lives in the (app) group → behind AuthGate, matching the canvas route: this is
// an authed internal authoring surface.
import { useCallback, useEffect, useRef, useState } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { useNavigation } from "../../context/NavigationContext"
import { useContent } from "../../context/ContentContext"
import { prdIdFromPrototypeSearch } from "../../lib/routes"
import { AppLayout } from "../../components/screens/app/AppLayout"
import { GenerateModal } from "../../components/design-agent/GenerateModal"
import { GenerationLoadingScreen } from "../../components/design-agent/GenerationLoadingScreen"
import { PostGenerationResult } from "../../components/design-agent/PostGenerationResult"
import { CommentsPanel } from "../../components/design-agent/CommentsPanel"
import { IterateComposer } from "../../components/design-agent/IterateComposer"
import { useIterateRun } from "../../components/design-agent/useIterateRun"
import {
  designAgentApi,
  prdApi,
  type CommentRecord,
  type PrototypeRecord,
} from "../../lib/api"
import { markdownToPrdState } from "../../lib/prd-adapter"
import type { PrdSection } from "../../types/content"
import type { DesignAgentGenResult } from "../../lib/runDesignAgentGeneration"

/** Pure: build the modal onClose handler that is safe to capture as a closure.
 *  Navigation only fires when no generation is in flight. The loading state is
 *  read via a getter (in the real component: `() => genLoadingRef.current`) so
 *  the closure always sees the live value rather than the stale value captured at
 *  render time — a ref read is immune to React's closure-over-state timing.
 *  Exported for unit testing without a DOM (Node env, no jsdom needed).
 */
export function buildGatedOnClose(
  getLoading: () => boolean,
  navigate: () => void,
): () => void {
  return () => {
    if (!getLoading()) navigate()
  }
}

/** Pure: resolve the figma_file_key to seed the generate panel with, given the
 *  URL's prd id and the PRD currently loaded in ContentContext. Returns the
 *  content PRD's figma_file_key ONLY when its prd_id matches the URL (so a stale
 *  PRD from a prior screen never leaks its source), else null. Extracted +
 *  exported so it is unit-testable without a DOM (the repo's vitest env is node).
 */
export function figmaKeyForPrototype(
  urlPrdId: number | null,
  contentPrd: { prd_id: number; figma_file_key?: string | null } | null,
): string | null {
  if (urlPrdId == null || !contentPrd) return null
  if (contentPrd.prd_id !== urlPrdId) return null
  return contentPrd.figma_file_key ?? null
}

/** Pure: classify the in-tab render state for a given prd context. Extracted so
 *  the route's three-way branch is unit-testable without a DOM. */
export type PrototypeTabState = "no-prd" | "resolving" | "ready" | "generate"
export function prototypeTabState(
  prdId: number | null,
  resolving: boolean,
  proto: PrototypeRecord | null,
): PrototypeTabState {
  if (prdId == null) return "no-prd"
  if (proto) return "ready"
  if (resolving) return "resolving"
  return "generate"
}

/**
 * In-tab prototype canvas. Mounts <PostGenerationResult> inside the app shell
 * (NOT a fixed-position overlay) and wires it exactly like the modal canvas:
 *   - a local useIterateRun drives the composer Submit, a comment's Apply, and a
 *     pin's Apply down one fixed iterate path; onComplete swaps the fresh row +
 *     bumps the reload nonce so the center iframe reloads the rebuilt bundle.
 *   - PRD sections/title are pulled by the prototype's own prd_id (fed by
 *     markdownToPrdState) when ContentContext doesn't already hold the right PRD.
 *   - the iterate slot is <IterateComposer>; the comments slot is <CommentsPanel>
 *     gated on share_token.
 *   - onShared / onIterated re-fetch the row so the share-gated comments column
 *     and viewer reflect the latest checkpoint.
 *
 * Mounted only when a prototype exists, so useIterateRun always has a real id
 * (hooks stay unconditional inside this child). `key` off the prototype id (set
 * by the parent) forces a clean remount per prototype.
 */
function InTabCanvas({
  proto,
  onProtoChange,
  onDone,
}: {
  proto: PrototypeRecord
  /** Replace the parent's held prototype with a fresher row (after iterate /
   *  share / a state toggle) so the in-tab canvas reflects the latest checkpoint. */
  onProtoChange: (next: PrototypeRecord) => void
  /** Return the tab to its empty/landing state (clears the in-tab prototype). */
  onDone: () => void
}) {
  const { content } = useContent()
  const prd = content.prd

  // PRD context pulled by the prototype's own prd_id when ContentContext doesn't
  // already hold the matching PRD (mirrors ApproveModal's supplemental PRD pull).
  const [urlPrdSections, setUrlPrdSections] = useState<PrdSection[] | undefined>(undefined)
  const [urlPrdTitle, setUrlPrdTitle] = useState<string | null>(null)
  const [loadedPrdId, setLoadedPrdId] = useState<number | null>(null)

  // A reload nonce bumped on every completed iterate. The center iframe reads the
  // bundle url; when the backend OVERWRITES the bundle at the SAME url, threading
  // this nonce as `?v=<nonce>` forces a fresh src → the iframe reloads.
  const [bundleReloadNonce, setBundleReloadNonce] = useState(0)
  // The comment lifted from CommentsPanel's Apply into IterateComposer's pre-fill.
  const [applyTarget, setApplyTarget] = useState<CommentRecord | null>(null)

  const protoPrdId = (proto as PrototypeRecord & { prd_id?: number }).prd_id ?? null

  // Shared iterate runner — drives the composer Submit, a comment's Apply, and a
  // pin's Apply through one fixed path: POST → poll-to-completion → left-panel
  // activity → reload the canvas. onComplete swaps in the fresh row + bumps the
  // reload nonce so the iframe reloads.
  const iterateRun = useIterateRun({
    prototypeId: proto.id,
    onComplete: (fresh) => {
      onProtoChange(fresh)
      setBundleReloadNonce((n) => n + 1)
    },
  })

  // The single fixed entry the composer and both Apply paths call.
  const runCanvasIterate = useCallback(
    (instruction: string, appliedCommentId?: number | null) => {
      void iterateRun.runIterate(instruction, appliedCommentId)
    },
    [iterateRun],
  )

  // A comment's Apply → run its body through the iterate runner, linking the
  // comment id. The agent decides applicability; the client fabricates no change.
  const runCommentIterate = useCallback(
    (comment: CommentRecord) => {
      runCanvasIterate(comment.body, comment.id)
    },
    [runCanvasIterate],
  )

  // After a Share or an iterate advances the SAME prototype, re-fetch the record
  // so the share-gated CommentsPanel / viewer reflect it.
  const refreshCanvas = useCallback(async () => {
    try {
      const fresh = await designAgentApi.get(proto.id)
      if (fresh) onProtoChange(fresh)
    } catch {
      /* degrade silently — the local ShareMenu token already shows the link */
    }
  }, [proto.id, onProtoChange])

  // Supplemental PRD pull. When ContentContext lacks the right PRD, fetch it by
  // the prototype's prd_id → parse → sections/title for the left panel. Loads
  // once per prd_id (the loadedPrdId guard makes it a no-op afterwards).
  useEffect(() => {
    if (protoPrdId == null) return
    if (prd?.prd_id === protoPrdId) return
    if (loadedPrdId === protoPrdId) return
    let cancelled = false
    prdApi
      .get(protoPrdId)
      .then((fetchedPrd) => {
        if (cancelled) return
        const parsed = markdownToPrdState(fetchedPrd.payload_md)
        setUrlPrdSections(parsed.sections)
        setUrlPrdTitle(fetchedPrd.title ?? null)
        setLoadedPrdId(protoPrdId)
      })
      .catch(() => {
        /* best-effort — left panel simply omits sections on error */
      })
    return () => {
      cancelled = true
    }
  }, [protoPrdId, loadedPrdId, prd?.prd_id])

  return (
    <PostGenerationResult
      prototype={proto}
      onStateChange={(state) =>
        onProtoChange({ ...proto, is_complete: state.isComplete })
      }
      prdSections={prd?.sections ?? urlPrdSections}
      prdTitle={prd?.title ?? urlPrdTitle}
      prdMetaLine={prd?.metaLine ?? null}
      onPinIterate={runCanvasIterate}
      onDone={onDone}
      iterateActivity={iterateRun.activity}
      iterateRunning={iterateRun.running}
      iterateError={iterateRun.error}
      iteratePendingQuestion={iterateRun.pendingQuestion}
      onAnswerQuestion={iterateRun.answerQuestion}
      bundleReloadNonce={bundleReloadNonce}
      comments={
        proto.share_token ? (
          <CommentsPanel
            key={`comments-${proto.id}`}
            token={proto.share_token}
            prototypeId={proto.id}
            onIterateComment={runCommentIterate}
            iterateBusy={iterateRun.running}
          />
        ) : null
      }
      iterate={
        <IterateComposer
          key={`iterate-${proto.id}`}
          prototypeId={proto.id}
          isComplete={proto.is_complete ?? false}
          applyTarget={applyTarget}
          onClearApply={() => setApplyTarget(null)}
          onIterated={refreshCanvas}
          // The iterate path intentionally skips the pre-flight cost-estimate
          // confirmation modal. The per-generation soft/hard spend caps remain
          // the guardrail, and the generate-path estimate is unchanged. The
          // default (`skipCostConfirm = false`) preserves the confirmation modal
          // for any non-iterate caller.
          skipCostConfirm
          runIterateExternal={runCanvasIterate}
          externalBusy={iterateRun.running}
        />
      }
      onShared={refreshCanvas}
    />
  )
}

export function PrototypeRoute() {
  const router = useRouter()
  const search = useSearchParams()
  const { goTo } = useNavigation()
  const { content } = useContent()

  const prdId = prdIdFromPrototypeSearch(search.get("prd"))
  const figmaFileKey = figmaKeyForPrototype(prdId, content.prd)

  // The PRD's resolved ready prototype (read-only via getByPrd), or null. When a
  // generation kicked off from this tab completes, this is set to the new
  // prototype so the in-tab canvas reveals it WITHOUT navigating to an overlay.
  const [proto, setProto] = useState<PrototypeRecord | null>(null)
  const [resolving, setResolving] = useState(false)

  // Ref-backed loading flag: read live inside the onClose closure so the
  // callback never captures a stale false from the render before kickoff.
  const genLoadingRef = useRef(false)

  // Full-screen loading-overlay visibility + the prototype_id known once the
  // generate POST returns (lets the loading screen subscribe to the SSE stream).
  const [genLoading, setGenLoading] = useState(false)
  const [genFigmaKey, setGenFigmaKey] = useState<string | null>(null)
  const [genGithubRepo, setGenGithubRepo] = useState<string | null>(null)
  const [genProtoId, setGenProtoId] = useState<number | null>(null)

  // Resolve the PRD's ready prototype read-only on prd change. getByPrd swallows
  // a 404 → null, so this never kicks a generation; a null result drops the tab
  // to the generate-landing path. Skipped once a prototype is already held (e.g.
  // freshly revealed after a generation) so the reveal isn't clobbered.
  useEffect(() => {
    if (prdId == null) {
      setProto(null)
      setResolving(false)
      return
    }
    let cancelled = false
    setResolving(true)
    designAgentApi
      .getByPrd(prdId)
      .then((found) => {
        if (cancelled) return
        setProto(
          found && found.status === "ready" && found.bundle_url ? found : null,
        )
      })
      .catch(() => {
        if (!cancelled) setProto(null)
      })
      .finally(() => {
        if (!cancelled) setResolving(false)
      })
    return () => {
      cancelled = true
    }
  }, [prdId])

  // Show the overlay the instant generation kicks off; capture the source context
  // for the loading screen's source-aware steps.
  const handleGenStart = (ctx?: {
    figmaFileKey?: string | null
    githubRepo?: string | null
  }) => {
    setGenFigmaKey(ctx?.figmaFileKey ?? null)
    setGenGithubRepo(ctx?.githubRepo ?? null)
    setGenProtoId(null)
    genLoadingRef.current = true
    setGenLoading(true)
  }

  // Terminal generation outcome. On SUCCESS reveal the new prototype IN-TAB:
  // stash the completed row in local state so the canvas branch renders it,
  // keeping the URL on /prototype?prd={prdId} (no overlay navigation). When the
  // result lacks the full row, fall back to a getByPrd re-fetch so the ready
  // prototype still reveals in-tab. On FAILURE / no result, just dismiss the
  // overlay; the panel stays so the user can retry (runGenerateFlow toasted it).
  const handleGenDone = (result?: DesignAgentGenResult) => {
    genLoadingRef.current = false
    setGenLoading(false)
    if (result?.ok && result.prototype) {
      setProto(result.prototype)
    } else if (result?.ok && prdId != null) {
      // Reveal-by-refetch fallback: the kickoff succeeded but no full row came
      // back on the result — re-resolve the PRD's ready prototype in-tab.
      designAgentApi
        .getByPrd(prdId)
        .then((found) => {
          if (found && found.status === "ready" && found.bundle_url) {
            setProto(found)
          }
        })
        .catch(() => {
          /* degrade — the tab stays on the generate panel for a retry */
        })
    }
  }

  // No PRD context (bare /prototype): there is nothing to generate from. Send the
  // user to the PRD screen to pick/approve a PRD first.
  if (prdId == null) {
    return (
      <AppLayout>
        <div className="design-agent-surface da-prototype-empty" data-testid="prototype-route-empty">
          <h2 className="da-prototype-empty-title">No PRD selected</h2>
          <p className="da-prototype-empty-sub">
            Open a PRD and choose "Generate Prototype" to start a prototype here.
          </p>
          <button type="button" className="btn btn-accent" onClick={() => goTo("prd")}>
            Go to PRD
          </button>
        </div>
      </AppLayout>
    )
  }

  // Ready prototype → render the in-tab canvas inside the app shell. `key` off the
  // prototype id forces a clean remount (fresh iterate runner) per prototype.
  if (proto) {
    return (
      <AppLayout>
        <div className="design-agent-surface da-prototype-page" data-testid="prototype-route">
          <InTabCanvas
            key={proto.id}
            proto={proto}
            onProtoChange={setProto}
            onDone={() => setProto(null)}
          />
        </div>
      </AppLayout>
    )
  }

  // Resolving the PRD's prototype → in-tab loading view (route content area, not a
  // full-screen overlay). genLoading=false here: the GenerationLoadingScreen's own
  // overlay is reserved for an active generation kicked off from the panel below.
  if (resolving) {
    return (
      <AppLayout>
        <div
          className="design-agent-surface da-prototype-page"
          data-testid="prototype-route-loading"
          aria-busy="true"
        />
      </AppLayout>
    )
  }

  // No ready prototype yet → the always-open generate panel. A successful
  // generation reveals the new prototype IN-TAB via handleGenDone (no overlay
  // navigation). The GenerationLoadingScreen covers kickoff-to-ready feedback.
  return (
    <AppLayout>
      <div className="design-agent-surface da-prototype-page" data-testid="prototype-route">
        <GenerateModal
          open
          onClose={buildGatedOnClose(
            () => genLoadingRef.current,
            () => router.push("/prd"),
          )}
          prdId={prdId}
          figmaFileKey={figmaFileKey}
          onGenStart={handleGenStart}
          onKickoff={(id) => setGenProtoId(id)}
          onGenDone={handleGenDone}
        />
        <GenerationLoadingScreen
          open={genLoading}
          figmaFileKey={genFigmaKey}
          githubRepo={genGithubRepo}
          prototypeId={genProtoId}
        />
      </div>
    </AppLayout>
  )
}
