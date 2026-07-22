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
//   3. no proto   — no ready prototype yet → an empty state with a "Generate
//                   prototype" button (NOT an auto-open panel). The GenerateModal
//                   mounts only on explicit click (generateRequested), so the
//                   locate pipeline never fires without user intent; a successful
//                   generation then reveals the new prototype IN-TAB (no
//                   navigation to a full-screen overlay).
//   4. failed     — the PRD's LATEST prototype row is `failed` (cold load onto a
//                   PRD whose last generation failed) → a concise error + Retry,
//                   NOT the bare CTA. getActiveByPrd excludes 'failed', so the
//                   none-branch consults getLatestByPrd to detect it; the failed
//                   row routes into the same genError surface an in-session
//                   failure uses. Raw backend error never reaches the DOM.
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
// designAgentApi.generate), opened on explicit request (generateRequested). The
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
import { useWorkspace } from "../../context/WorkspaceContext"
import { prdIdFromPrototypeSearch, prototypePath } from "../../lib/routes"
import { AppLayout } from "../../components/screens/app/AppLayout"
import { GenerateModal } from "../../components/design-agent/GenerateModal"
import {
  acknowledge,
  markCancelled,
  wasCancelled,
} from "../../components/design-agent/notificationStore"
import { GenerationErrorBanner, reasonCopy, isRetryableFailure } from "../../components/design-agent/GenerationErrorBanner"
import { GenerateSurfaceErrorBoundary } from "../../components/design-agent/GenerateSurfaceErrorBoundary"
import { GenerationLoadingScreen } from "../../components/design-agent/GenerationLoadingScreen"
import { PostGenerationResult } from "../../components/design-agent/PostGenerationResult"
import { PrototypeEmptyState } from "../../components/design-agent/PrototypeEmptyState"
import { CommentsPanel } from "../../components/design-agent/CommentsPanel"
import { IterateComposer } from "../../components/design-agent/IterateComposer"
import { useIterateRun } from "../../components/design-agent/useIterateRun"
import {
  IconGrid,
  IconPin,
  IconCopy,
  IconShare,
  IconTerminalPrompt,
  IconChevronLeft,
} from "../../components/shared/app-icons"
import {
  designAgentApi,
  prdApi,
  type CommentRecord,
  type PrototypeRecord,
} from "../../lib/api"
import {
  runDesignAgentGeneration,
  type DesignAgentGenResult,
} from "../../lib/runDesignAgentGeneration"
import styles from "./PrototypeRoute.module.css"

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

/** Pure: resolve the PRD's declared platform hint (the parsed :::design block's
 *  platform_hint) to seed the generate panel's platform DEFAULT, given the URL's
 *  prd id and the PRD currently loaded in ContentContext. Mirrors
 *  figmaKeyForPrototype's stale-PRD guard: the hint is read ONLY when the
 *  content PRD's prd_id matches the URL (a stale PRD from a prior screen never
 *  leaks its hint), else null. Direct-nav/refresh (no PRD in ContentContext)
 *  yields null — no supplemental fetch is added for the hint. */
export function platformHintForPrototype(
  urlPrdId: number | null,
  contentPrd: {
    prd_id: number
    sections?: { type: string; platformHint?: "desktop" | "mobile" | "both" }[]
  } | null,
): "desktop" | "mobile" | "both" | null {
  if (urlPrdId == null || !contentPrd) return null
  if (contentPrd.prd_id !== urlPrdId) return null
  const design = contentPrd.sections?.find((s) => s.type === "prd-design")
  return design?.platformHint ?? null
}

/** Pure: resolve the PRD TITLE for the breadcrumb / in-tab title bar / left
 *  header. Prefers ContentContext when it holds the matching PRD; on direct-nav /
 *  refresh ContentContext is empty (no PRD loaded for `?prd=<id>`), so we fall
 *  back to the minimal title-only supplemental fetch — but ONLY when that fetch
 *  resolved for the SAME prd_id (guards a stale fetched title from a prior id).
 *  Title only — no PRD body / sections / panel is involved. Exported so the
 *  direct-nav title contract is unit-testable without a DOM. */
export function resolvePrdTitle(
  protoPrdId: number | null,
  contentTitle: string | null,
  fetchedPrdId: number | null,
  fetchedTitle: string | null,
): string | null {
  if (contentTitle != null) return contentTitle
  if (protoPrdId != null && fetchedPrdId === protoPrdId) return fetchedTitle
  return null
}

/** Pure: decide whether the title-only supplemental fetch should fire. True when
 *  there is a prd_id to fetch, ContentContext does NOT already supply the title,
 *  and this prd_id's title has not already been fetched in this mount. Exported
 *  for unit testing the refetch guard without a DOM. */
export function needsTitleFetch(
  protoPrdId: number | null,
  contentTitle: string | null,
  fetchedPrdId: number | null,
): boolean {
  if (protoPrdId == null) return false
  if (contentTitle != null) return false
  if (fetchedPrdId === protoPrdId) return false
  return true
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

/** Pure: derive the initial `generateRequested` gate for a no-prototype PRD.
 *  The generate panel is GATED behind an explicit click by default — a plain
 *  navigation / refresh to `/prototype?prd=<id>` for a PRD with no prototype must
 *  land on the empty state, NOT auto-open the panel (which would auto-fire the
 *  locate pipeline with no user intent, worsened by the savedPreference auto-skip).
 *
 *  An explicit-generate-intent signal — the `?generate=1` query param a
 *  "Generate Prototype" navigation carries (built by `prototypePath(id, {
 *  generate: true })`) — honors a direct open: pass it as `intent` and the panel
 *  opens on mount with no extra click. The route reads the param via
 *  `generateIntentFromSearch`, seeds the gate with it, then CONSUMES it (strips
 *  it from the URL via router.replace) so a later refresh after dismiss does not
 *  re-open the panel. A plain `?prd=<id>` nav carries no signal → `intent=false`
 *  → the empty state stays the default. Extracted + exported so the gate's
 *  default-closed contract is unit-testable without a DOM. */
export function initialGenerateRequested(intent: boolean): boolean {
  return intent
}

/** Pure: read the explicit-generate-intent signal from the URL's `generate`
 *  query param. Only the exact string "1" is the intent signal (matches the
 *  `&generate=1` prototypePath builds); anything else (absent, "0", garbage) is
 *  no-intent. Accepts the raw value from `useSearchParams().get` (string | null).
 *  Extracted + exported so the intent read is unit-testable without a DOM. */
export function generateIntentFromSearch(raw: string | null): boolean {
  return raw === "1"
}

// `?pid=` handoff (prototypeHintFromSearch / handoffPrototypeId below): its
// only in-app producer (PrdPanelContent.tsx's old onKickoff nav) was removed
// during a generation-flow consolidation pass (2026-07-08). KEPT deliberately
// as defensive handling of a URL shape that could still arrive via an old
// bookmark or a shared link — not deleted just because its one producer is
// gone. Re-evaluate only if this becomes a genuine maintenance burden.
/** Pure: read the transient prototype loading hint from the URL's `pid` query
 *  param. Only positive integers are accepted; malformed values are ignored so
 *  the route falls back to resolving by PRD alone. */
export function prototypeHintFromSearch(raw: string | null): number | null {
  if (raw == null || raw === "" || !/^\d+$/.test(raw)) return null
  const id = Number(raw)
  return Number.isSafeInteger(id) && id > 0 ? id : null
}

/** Pure: derive the initial fullscreen state from the `fs` URL query param.
 *  Absent or any value other than "0" → fullscreen (default-open). Only "0"
 *  suppresses fullscreen. Extracted for unit-testability without a DOM.
 *  Exported so the prototype-route tests can cover the derivation logic.
 */
export function fsParamToFullscreen(fsParam: string | null): boolean {
  return fsParam !== "0"
}

/** Pure: decide what the route should do with the active-prototype lookup result
 *  on (re)load. `reveal` a ready+bundled row (the canvas), `resume` an in-flight
 *  generating row (overlay + poll-to-ready), or do `none` (drop to the generate
 *  panel). Extracted so the resume decision is unit-testable without mounting the
 *  client component (the repo's vitest env is `node`, no jsdom). This is the
 *  reachability fix: a generating row is resumed instead of stranded during the
 *  readiness lag between the SSE 'done' and complete_prototype(). */
export type ActiveProtoAction =
  | { kind: "reveal"; proto: PrototypeRecord }
  | { kind: "resume"; prototypeId: number }
  | { kind: "none" }

export function actionForActiveProto(
  found: PrototypeRecord | null,
): ActiveProtoAction {
  if (found && (found.status === "ready" || found.status === "failed") && found.bundle_url) {
    return { kind: "reveal", proto: found }
  }
  if (found && found.status === "generating") {
    return { kind: "resume", prototypeId: found.id }
  }
  return { kind: "none" }
}

/** Pure: decide whether the LATEST prototype row (any status) is a terminal
 *  FAILURE that should surface an error+retry instead of the bare generate CTA.
 *  Consulted ONLY on the active-lookup none-branch (no ready/generating row):
 *  getActiveByPrd excludes 'failed', so a failed latest row otherwise collapses
 *  to the empty CTA. Returns `failed` (with the row id) only when the latest row
 *  is `failed`; null / no row / any other status → `none` (the existing empty
 *  state). The raw `error` string is intentionally NOT captured here — only the
 *  id travels; the failed UI renders curated copy, never the backend error.
 *  Extracted + exported so the failed decision is unit-testable without a DOM
 *  (the repo's vitest env is `node`, no jsdom). */
export type LatestProtoAction =
  | { kind: "failed"; prototypeId: number }
  | { kind: "none" }

export function actionForLatestProto(
  latest: PrototypeRecord | null,
): LatestProtoAction {
  if (latest && latest.status === "failed") {
    return { kind: "failed", prototypeId: latest.id }
  }
  return { kind: "none" }
}

/**
 * In-tab prototype canvas. Mounts <PostGenerationResult> inside the app shell
 * (NOT a fixed-position overlay) and wires it exactly like the modal canvas:
 *   - a local useIterateRun drives the composer Submit, a comment's Apply, and a
 *     pin's Apply down one fixed iterate path; onComplete swaps the fresh row +
 *     bumps the reload nonce so the center iframe reloads the rebuilt bundle.
 *   - the left column is a LIVE-ONLY agent-conversation thread (named turns +
 *     composer); only the PRD TITLE survives (breadcrumb / title bar / header).
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
  onBack,
  searchParams,
  router,
  seedV1,
}: {
  proto: PrototypeRecord
  /** Replace the parent's held prototype with a fresher row (after iterate /
   *  share / a state toggle) so the in-tab canvas reflects the latest checkpoint. */
  onProtoChange: (next: PrototypeRecord) => void
  /** Return the tab to its empty/landing state (clears the in-tab prototype). */
  onDone: () => void
  /** Navigate back to the previous page (the in-tab title bar back button). */
  onBack?: () => void
  /** The current URL search params — threaded from PrototypeRoute so InTabCanvas
   *  reads the same snapshot that seeded the parent render (SSR-safe: the parent
   *  already called useSearchParams). */
  searchParams: ReturnType<typeof useSearchParams>
  /** The Next.js router — threaded from PrototypeRoute so InTabCanvas can call
   *  router.replace without a second useRouter call inside the child. */
  router: ReturnType<typeof useRouter>
  /** True only when this canvas mounted because a generation just completed IN
   *  THIS SESSION (not when an existing prototype was loaded). Drives the opening
   *  agent "Generated v1…" seed turn. Live-only — never persisted/refetched. */
  seedV1: boolean
}) {
  const { content } = useContent()
  const prd = content.prd
  const protoPrdId = (proto as PrototypeRecord & { prd_id?: number }).prd_id ?? null
  // PRD title for the breadcrumb / in-tab title bar / left-column header. Only the
  // TITLE survives the PRD-panel removal: prefer ContentContext when it holds the
  // matching PRD, else fall back to a minimal title-only supplemental fetch below.
  const contentTitle = prd?.prd_id === protoPrdId ? (prd?.title ?? null) : null

  // Supplemental TITLE-ONLY fetch. On direct-nav / refresh, ContentContext is
  // empty (no PRD loaded for `?prd=<id>`), so `contentTitle` is null and the
  // breadcrumb/titlebar would render "Untitled prototype". We re-source ONLY the
  // PRD title (NOT the body, sections, or any panel) by fetching the prototype's
  // own prd_id once. Guarded so it never refetches when a title is already
  // available (from ContentContext or a prior fetch for this same prd_id).
  const [fetchedTitle, setFetchedTitle] = useState<string | null>(null)
  const [fetchedPrdId, setFetchedPrdId] = useState<number | null>(null)
  const prdTitle = resolvePrdTitle(protoPrdId, contentTitle, fetchedPrdId, fetchedTitle)
  useEffect(() => {
    // Skip when there's nothing to fetch, when ContentContext already supplies the
    // title, or when this prd_id's title was already fetched in this mount.
    if (!needsTitleFetch(protoPrdId, contentTitle, fetchedPrdId)) return
    // Narrow for TS: needsTitleFetch already returns false for a null id, but the
    // compiler can't see through the helper boundary, so re-assert before .get().
    if (protoPrdId == null) return
    let cancelled = false
    prdApi
      .get(protoPrdId)
      .then((fetchedPrd) => {
        if (cancelled) return
        setFetchedTitle(fetchedPrd.title ?? null)
        setFetchedPrdId(protoPrdId)
      })
      .catch(() => {
        /* best-effort — titlebar falls back to "Untitled prototype" on error */
      })
    return () => {
      cancelled = true
    }
  }, [protoPrdId, contentTitle, fetchedPrdId])
  // The signed-in user's display name for user-turn labels in the live thread.
  const userName = content.userName ?? null

  // A reload nonce bumped on every completed iterate. The center iframe reads the
  // bundle url; when the backend OVERWRITES the bundle at the SAME url, threading
  // this nonce as `?v=<nonce>` forces a fresh src → the iframe reloads.
  const [bundleReloadNonce, setBundleReloadNonce] = useState(0)
  // The comment lifted from CommentsPanel's Apply into IterateComposer's pre-fill.
  const [applyTarget, setApplyTarget] = useState<CommentRecord | null>(null)

  // Shared iterate runner — drives the composer Submit, a comment's Apply, and a
  // pin's Apply through one fixed path: POST → poll-to-completion → left-panel
  // activity → reload the canvas. onComplete swaps in the fresh row + bumps the
  // reload nonce so the iframe reloads.
  const iterateRun = useIterateRun({
    prototypeId: proto.id,
    onComplete: (fresh, opts) => {
      onProtoChange(fresh)
      // Only bump the reload nonce (force a fresh iframe load) when the run
      // actually advanced the bundle. A clarifying-question pause passes
      // `reloadBundle: false` — keep the current preview, don't re-fetch a bundle
      // that didn't change (avoids a transient 404 window). Any caller that omits
      // opts still reloads, preserving the prior behaviour.
      if (opts?.reloadBundle !== false) setBundleReloadNonce((n) => n + 1)
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

  // Within-session v1 seed: when this canvas mounted because a generation just
  // completed IN THIS SESSION (seedV1), append an opening AGENT turn so the live
  // thread opens with the agent introducing v1. Fires once on mount (the `key` off
  // the prototype id forces a fresh mount per prototype, so the ref-guard is a
  // belt-and-braces against a same-mount re-run). LIVE-ONLY: not persisted, not
  // refetched — a refresh starts the thread empty.
  const seededRef = useRef(false)
  const { appendActivity } = iterateRun
  useEffect(() => {
    if (!seedV1 || seededRef.current) return
    seededRef.current = true
    const title = prdTitle ?? "the PRD"
    appendActivity({
      kind: "done",
      text: `Generated v1 from PRD '${title}'. Describe a change below to iterate.`,
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <PostGenerationResult
      prototype={proto}
      hideBreadcrumb
      isInTab
      onBack={onBack}
      defaultFullscreen={fsParamToFullscreen(searchParams.get("fs"))}
      onFullscreenChange={(open) => {
        const next = new URLSearchParams(searchParams.toString())
        if (open) {
          next.delete("fs")
        } else {
          next.set("fs", "0")
        }
        router.replace(`/prototype?${next.toString()}`)
      }}
      onStateChange={(state) =>
        onProtoChange({ ...proto, is_complete: state.isComplete })
      }
      prdTitle={prdTitle}
      userName={userName}
      onPinIterate={runCanvasIterate}
      onDone={onDone}
      iterateActivity={iterateRun.activity}
      iterateRunning={iterateRun.running}
      iterateError={iterateRun.error}
      iteratePendingQuestion={iterateRun.pendingQuestion}
      onAnswerQuestion={iterateRun.answerQuestion}
      onSkipQuestion={async () => {
        await iterateRun.dismissQuestion()
        // Optimistically clear the held row's question too, so the prop-driven
        // ClarifyingQuestionSurface (which self-gates on `pending_question`) can't
        // un-suppress and re-render the just-skipped question. No bundle reload:
        // onProtoChange patches only this field, never bumps bundleReloadNonce.
        onProtoChange({ ...proto, pending_question: null })
      }}
      bundleReloadNonce={bundleReloadNonce}
      // Manual "Refresh preview" — reuses the SAME reload signal an iterate-complete
      // uses (the `bundleReloadNonce` bump above), so it cascades to the iframe
      // remount + view-grant re-mint with no second reload/poll loop.
      onRefreshBundle={() => setBundleReloadNonce((n) => n + 1)}
      comments={
        proto.share_token ? (
          <CommentsPanel
            key={`comments-${proto.id}`}
            token={proto.share_token}
            prototypeId={proto.id}
            onIterateComment={runCommentIterate}
            iterateBusy={iterateRun.running}
            showGeneralSection
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
  const { goTo, showToast } = useNavigation()
  const { content } = useContent()
  const { workspace } = useWorkspace()

  const prdId = prdIdFromPrototypeSearch(search.get("prd"))
  const handoffPrototypeId = prototypeHintFromSearch(search.get("pid"))
  const figmaFileKey = figmaKeyForPrototype(prdId, content.prd)
  const platformHint = platformHintForPrototype(prdId, content.prd)
  const savedPreference = workspace?.design_source ?? null

  // Explicit-generate-intent signal carried by a "Generate Prototype" navigation
  // (`prototypePath(id, { generate: true })` → `?generate=1`). Captured ONCE at
  // mount into a ref, BEFORE the consume effect strips the param: the ref is the
  // single source of truth for "did this mount arrive with intent", so the later
  // url-stripping render (which makes search.get("generate") read null) cannot
  // flip the gate back. The live `search` read is used only for the initial ref
  // seed and to decide whether a consume/strip is still pending.
  const initialGenerateIntentRef = useRef(generateIntentFromSearch(search.get("generate")))

  // The PRD's resolved ready prototype (read-only via getByPrd), or null. When a
  // generation kicked off from this tab completes, this is set to the new
  // prototype so the in-tab canvas reveals it WITHOUT navigating to an overlay.
  const [proto, setProto] = useState<PrototypeRecord | null>(null)
  const [resolving, setResolving] = useState(false)
  // Gate for the generate panel on a no-prototype PRD. Default-closed: a plain
  // `?prd=` navigation / refresh lands on the empty state, and the GenerateModal
  // opens ONLY when the empty-state "Generate prototype" button sets this true.
  // EXCEPTION — an explicit `?generate=1` intent nav (a "Generate Prototype"
  // action) seeds the gate OPEN on mount so the panel opens directly with no
  // second click; that intent is then consumed (the param stripped) by the effect
  // below so a refresh after dismiss does not re-open it.
  const [generateRequested, setGenerateRequested] = useState(() =>
    initialGenerateRequested(initialGenerateIntentRef.current),
  )
  // The prototype id whose canvas should open with the within-session "Generated
  // v1…" seed turn — set only when a generation completes in this session, never
  // on the read-only load path. Drives InTabCanvas's one-shot seed. Live-only.
  const [seedProtoId, setSeedProtoId] = useState<number | null>(null)

  // Ref-backed loading flag: read live inside the onClose closure so the
  // callback never captures a stale false from the render before kickoff.
  const genLoadingRef = useRef(false)

  // (2026-07-08) DELIBERATELY NOT migrated onto useGeneratePrototype() (see the
  // generate/view-prototype consolidation work elsewhere for the shared hook's
  // contract). This surface's loading overlay is driven by THREE triggers
  // (user-click-generate via GenerateModal, resume-mid-generation-on-reload via
  // a direct runDesignAgentGeneration poll, cold-load onto a failed-latest row)
  // sharing this one state machine; only the first goes through GenerateModal's
  // callback contract the shared hook wraps. This file is already the reference
  // implementation (full overlay + cancel + notify + resume + failed-retry) the
  // other 3 (already-migrated) entry points were unified toward.
  // Full-screen loading-overlay visibility + the prototype_id known once the
  // generate POST returns (lets the loading screen subscribe to the SSE stream).
  const [genLoading, setGenLoading] = useState(false)
  // Terminal-failure recovery: set to the failure message when a generation
  // resolves `{ ok: false, message }` (failed / invalidated / timeout / throw —
  // see runDesignAgentGeneration.ts), so the chokepoint no longer silently
  // collapses to the bare empty/PRD state. Drives a dedicated error+Retry render
  // branch below. Null on the success path (success stays byte-identical).
  const [genError, setGenError] = useState<string | null>(null)
  const [genFigmaKey, setGenFigmaKey] = useState<string | null>(null)
  const [genGithubRepo, setGenGithubRepo] = useState<string | null>(null)
  const [genProtoId, setGenProtoId] = useState<number | null>(null)

  // A valid `pid` is a transient handoff hint from a just-started generation.
  // Seed the build loader immediately, before the PRD-level active lookup
  // resolves. The lookup below remains authoritative and clears or replaces this
  // hint when it discovers a ready, failed, generating, or absent row.
  useEffect(() => {
    if (handoffPrototypeId == null) return
    setProto(null)
    setGenError(null)
    setGenFigmaKey(null)
    setGenGithubRepo(null)
    setGenProtoId(handoffPrototypeId)
    genLoadingRef.current = true
    setGenLoading(true)
  }, [handoffPrototypeId])

  // Deep-link resolution for the `pid` hint: the prototype-ready notification
  // links `/prototype?pid=<id>` (no `?prd=`), and that link arrives AFTER the
  // row is ready — not the just-started shape the seed effect above serves.
  // Resolve the row by id and, when it is READY, select it directly (the
  // ready render branch below wins even without a `?prd=` context). Any other
  // status, or a failed/foreign lookup, changes nothing: with `?prd=` present
  // the PRD-level lookup stays authoritative, and a pid-only URL falls back to
  // the existing no-PRD empty state.
  useEffect(() => {
    if (handoffPrototypeId == null) return
    let cancelled = false
    designAgentApi
      .get(handoffPrototypeId)
      .then((row) => {
        if (cancelled || !row || row.status !== "ready") return
        setProto(row)
        genLoadingRef.current = false
        setGenLoading(false)
      })
      .catch(() => {
        /* degrade — the seed/lookup effects own every non-ready path. */
      })
    return () => {
      cancelled = true
    }
  }, [handoffPrototypeId])

  // Resolve the PRD's prototype read-only on prd change, and RE-ATTACH to an
  // in-flight generation. getActiveByPrd returns the newest ready-OR-generating
  // row (swallows 404→null), so a (re)load mid-generation no longer strands the
  // finished bundle: the SSE 'done' fires at codegen-complete but the row is not
  // marked ready until the end of the build/stage/preview tail (~minutes later),
  // and the kickoff poll is page-bound — it dies on reload. Here we resume:
  //   • ready  → reveal the canvas (existing behaviour),
  //   • generating → show the loader overlay (genProtoId re-subscribes the SSE)
  //     and poll to terminal, then handleGenDone reveals it,
  //   • none/failed → drop to the generate panel.
  useEffect(() => {
    if (prdId == null) {
      setProto(null)
      genLoadingRef.current = false
      setGenLoading(false)
      setResolving(false)
      return
    }
    let cancelled = false
    setResolving(true)
    designAgentApi
      .getActiveByPrd(prdId)
      .then((found) => {
        if (cancelled) return
        const action = actionForActiveProto(found)
        if (action.kind === "reveal") {
          setProto(action.proto)
          genLoadingRef.current = false
          setGenLoading(false)
          return
        }
        setProto(null)
        if (action.kind === "resume") {
          // Re-attach to the running generation: overlay + resume poll.
          setGenFigmaKey(null)
          setGenGithubRepo(null)
          setGenProtoId(action.prototypeId)
          genLoadingRef.current = true
          setGenLoading(true)
          void runDesignAgentGeneration({
            prototypeId: action.prototypeId,
          }).then((result) => {
            if (cancelled) return
            handleGenDone(result)
          })
          return
        }
        // action.kind === "none": no ready/generating row. getActiveByPrd
        // EXCLUDES 'failed', so a FAILED latest row reaches here looking like
        // "never generated" → the bare generate CTA. Consult getLatestByPrd
        // (any status) to distinguish a failed latest row from a true no-row
        // PRD. A failed row routes into the EXISTING genError failed surface
        // (curated copy + Retry); the raw `error` string is never captured.
        // Any other case (no row / non-failed latest) leaves genError null →
        // the existing empty state. Swallows its own 404→null.
        designAgentApi
          .getLatestByPrd(prdId)
          .then((latest) => {
            if (cancelled) return
            const latestAction = actionForLatestProto(latest)
            if (latestAction.kind === "failed") {
              // A generic marker — NOT the raw backend error. reasonCopy maps it
              // to a curated line; the raw `error` never reaches the DOM.
              setGenError("Generation failed")
            }
            genLoadingRef.current = false
            setGenLoading(false)
          })
          .catch(() => {
            genLoadingRef.current = false
            setGenLoading(false)
            /* degrade — leave genError null → the existing empty state. */
          })
      })
      .catch(() => {
        if (!cancelled) {
          setProto(null)
          genLoadingRef.current = false
          setGenLoading(false)
        }
      })
      .finally(() => {
        if (!cancelled) setResolving(false)
      })
    return () => {
      cancelled = true
    }
    // handleGenDone is a stable closure over setters/refs (no reactive deps).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prdId])

  // Tracks whether the mount's `?generate=1` intent (if any) has been honored +
  // consumed. The consume effect below flips it; the prd-reset effect reads it to
  // know whether its first run should preserve the seeded-open gate.
  const intentConsumedRef = useRef(false)

  // One-shot consume of the `?generate=1` intent. The gate was already seeded OPEN
  // from the ref at useState init, so here we only STRIP the param from the URL —
  // via router.replace to the param-less `?prd=<id>` path — so that a later HARD
  // REFRESH (after the user dismisses the panel) does NOT re-open it: the signal is
  // gone from the URL. Guarded by the ref so it fires exactly once and never loops;
  // stripping the param does not itself flip generateRequested (the gate is state,
  // not derived from the live search read), so there is no re-set cycle. If there
  // was no intent, this is a no-op that simply marks the intent "consumed" so the
  // prd-reset effect resumes its normal default-closed re-gate from the first run.
  useEffect(() => {
    if (intentConsumedRef.current) return
    intentConsumedRef.current = true
    if (initialGenerateIntentRef.current) {
      // Drop `generate` from the URL, preserving the prd context. prdId is the
      // mount's parsed id; prototypePath(prdId) rebuilds the bare `?prd=` form.
      router.replace(prototypePath(prdId))
    }
    // Runs once on mount; deliberately not keyed on prdId/router so a later prd
    // switch (handled by the reset effect) cannot re-trigger a strip.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Re-gate the generate panel on every prd change. Navigating between PRDs must
  // never carry an open panel across: a PRD whose panel was opened by a click,
  // then a switch to a different no-prototype PRD, drops back to that PRD's empty
  // state rather than auto-opening (and auto-firing locate) for the new id.
  //
  // FIRST RUN GUARD: on mount this effect also fires. When the mount arrived with
  // `?generate=1` intent, the gate was seeded OPEN and must be PRESERVED on the
  // first run (otherwise this would immediately clobber the intent to closed). We
  // skip exactly the first run in that case; every subsequent prdId change resets
  // to the default-closed gate as before. With no intent, the first run resets to
  // false (a harmless no-op, since the gate was already seeded false).
  const prdResetFirstRunRef = useRef(true)
  useEffect(() => {
    if (prdResetFirstRunRef.current) {
      prdResetFirstRunRef.current = false
      if (initialGenerateIntentRef.current) return
    }
    setGenerateRequested(initialGenerateRequested(false))
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

  // Escape hatch from the full-screen generating overlay: true abort. Best-effort
  // cancels the in-flight generation server-side (stops further LLM spend +
  // deletes the row + resets the PRD to draft), then ALWAYS clears the loading
  // state and returns the user to the PRD (brief) screen — a failed cancel must
  // never trap the user, so the navigation is unconditional. Mirrors the
  // generate-config Cancel target (`goTo("brief")`, the GenerateModal onClose).
  const handleGenCancel = async () => {
    const id = genProtoId
    if (id != null) {
      markCancelled(id) // suppress the false "Generation failed" toast for this id
      acknowledge(id) // drop any pending ready entry so no stale replay
      try {
        await designAgentApi.cancel(id)
      } catch {
        /* best-effort — a failed cancel must never trap the user */
      }
    }
    genLoadingRef.current = false
    setGenLoading(false)
    goTo("brief")
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
      // SUCCESS — clear any prior failure and reveal the new prototype in-tab.
      setGenError(null)
      setProto(result.prototype)
      setSeedProtoId(result.prototype.id)
    } else if (result?.ok && prdId != null) {
      // Reveal-by-refetch fallback: the kickoff succeeded but no full row came
      // back on the result — re-resolve the PRD's ready prototype in-tab.
      setGenError(null)
      designAgentApi
        .getByPrd(prdId)
        .then((found) => {
          if (found && found.status === "ready" && found.bundle_url) {
            setProto(found)
            setSeedProtoId(found.id)
          }
        })
        .catch(() => {
          /* degrade — the tab stays on the generate panel for a retry */
        })
    } else {
      // FAILURE / no result (failed / invalidated / timeout / throw, OR the
      // GenerateModal's `.catch(() => onGenDone())` no-arg path). Surface a loud,
      // persistent error + Retry instead of silently falling through to the bare
      // empty/PRD state. runDesignAgentGeneration always carries a `message` on
      // the `{ ok: false }` shape; the no-arg path falls back to a generic line.
      if (genProtoId != null && wasCancelled(genProtoId)) {
        // User cancelled — the deleted row's terminal 404 is not a genuine
        // failure, so do not surface the in-panel error.
      } else {
        setGenError(result && !result.ok ? result.message : "Generation failed")
      }
    }
  }

  // Retry from the generation-error state. Mirrors GenerateModal's locate Retry:
  // clear the failure and re-open the generate panel so the user re-initiates the
  // run explicitly (re-using the existing kickoff path — the empty-state →
  // GenerateModal flow). Does NOT auto-re-POST (matches DesignAgentLauncher's
  // onRetry contract). genError is also cleared on a successful subsequent run.
  const handleGenErrorRetry = () => {
    setGenError(null)
    setGenerateRequested(true)
  }

  // "Notify me when ready" — dismiss the full-screen loading overlay, show a
  // processing toast, signal the shell's PrototypeGeneratingCard (da:generating),
  // hand off the completion poll to the shell owner (da:notify-generation), and
  // navigate away so the user can keep working. Guarded: no-op when no in-flight
  // prototype id exists yet (the generate POST hasn't returned). On navigate, prefer
  // history.back(); fall back to the bare prototype path when there is no history
  // entry to return to (fresh tab / direct open).
  const handleNotifyWhenReady = useCallback(() => {
    if (genProtoId == null) return
    showToast("Prototype is processing", "We'll let you know when it's ready.")
    window.dispatchEvent(new CustomEvent("da:generating", { detail: { prototypeId: genProtoId } }))
    if (prdId != null) {
      window.dispatchEvent(new CustomEvent("da:notify-generation", { detail: { prototypeId: genProtoId, prdId } }))
    }
    if (window.history.length > 1) {
      router.back()
    } else {
      router.push(prototypePath(prdId!))
    }
  }, [showToast, genProtoId, prdId, router])

  // No PRD context (bare /prototype): there is nothing to generate from. Send the
  // user to the weekly brief, where a PRD opens in the right-rail card and offers
  // "Generate Prototype". EXCEPT when a resolved prototype exists — a pid-only
  // deep link (the prototype-ready notification) selects a ready prototype with
  // no `?prd=` in the URL, so the ready branch below must win over this one.
  if (prdId == null && proto == null) {
    return (
      <AppLayout>
        <PrototypeEmptyState
          testid="prototype-route-empty"
          title="No PRD selected"
          sub={'Open a PRD and choose "Generate Prototype" to start a prototype here.'}
          action={
            <button type="button" className="btn btn-accent" onClick={() => goTo("brief")}>
              Go to brief
            </button>
          }
        />
      </AppLayout>
    )
  }

  // Ready prototype → render the in-tab canvas inside the app shell. `key` off the
  // prototype id forces a clean remount (fresh iterate runner) per prototype.
  if (proto) {
    return (
      <AppLayout mainClassName="main--flush" mainColumnClassName="main-column--flush" hideChromeStrip>
        <div className="design-agent-surface da-prototype-page" data-testid="prototype-route">
          <InTabCanvas
            key={proto.id}
            proto={proto}
            onProtoChange={setProto}
            onDone={() => setProto(null)}
            onBack={() => router.back()}
            searchParams={search}
            router={router}
            seedV1={proto.id === seedProtoId}
          />
        </div>
      </AppLayout>
    )
  }

  // Generation FAILED → a loud, persistent error + Retry. Sits ABOVE the
  // resolving / empty / generate branches so a failed run NEVER silently
  // collapses to the bare empty/PRD state. Reuses the EXISTING
  // GenerationErrorBanner (the same component + reasonCopy mapping the
  // DesignAgentLauncher uses): the raw backend message is mapped to curated copy
  // (raw error never hits the DOM), and Retry re-opens the generate
  // panel via the existing kickoff path. A "Back to brief" affordance gives a
  // deliberate exit (mirrors locate's Switch-source: a user choice, not a silent
  // collapse). genError is set in TWO places, both reaching this same surface:
  //   (1) the failure branch of handleGenDone (an in-session { ok: false } run);
  //   (2) the resolve effect's none-branch, when getLatestByPrd reports the
  //       latest row is `failed` (a COLD load onto a PRD whose last generation
  //       failed — getActiveByPrd excludes 'failed' so it returned null).
  // Both pass a generic marker mapped by reasonCopy, never the raw row error.
  if (genError) {
    return (
      <AppLayout>
        <div
          className="design-agent-surface da-prototype-empty da-gen-error-stage"
          data-testid="prototype-route-gen-error"
        >
          <GenerationErrorBanner
            reason={reasonCopy(genError, genProtoId ?? undefined)}
            retryable={isRetryableFailure(genError)}
            onRetry={handleGenErrorRetry}
            onBack={() => goTo("brief")}
          />
        </div>
      </AppLayout>
    )
  }

  // A generation is actively being handed off or resumed. Keep this above the
  // resolving branch so a valid `pid` shows the build loader immediately instead
  // of the generic route-loading state while the PRD-level lookup is in flight.
  if (genLoading) {
    return (
      <AppLayout>
        <div className="design-agent-surface da-prototype-page" data-testid="prototype-route">
          <GenerateSurfaceErrorBoundary onReset={handleGenErrorRetry}>
            <GenerationLoadingScreen
              open={genLoading}
              figmaFileKey={genFigmaKey}
              githubRepo={genGithubRepo}
              prototypeId={genProtoId}
              onCancel={handleGenCancel}
              onNotifyWhenReady={genProtoId != null ? handleNotifyWhenReady : undefined}
            />
          </GenerateSurfaceErrorBoundary>
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
          className={`design-agent-surface da-prototype-page ${styles.resolving}`}
          data-testid="prototype-route-loading"
          aria-busy="true"
        >
          {/* Back affordance so the transient resolving state is never a
              dead-end — mirrors the `ready` branch's title-bar back
              (router.back()). */}
          <button
            type="button"
            className={`da-ctl-back ${styles.resolvingBack}`}
            data-testid="prototype-route-loading-back"
            title="Back"
            aria-label="Back"
            onClick={() => router.back()}
          >
            <IconChevronLeft size={16} />
          </button>
          {/* Minimal loading indicator — reuses the shared .da-spinner SVG
              pattern (DesignAgentLauncher / da-prototype-generating) so this is no
              longer a blank flash while getActiveByPrd is in flight. */}
          <svg
            width="20"
            height="20"
            viewBox="0 0 16 16"
            fill="none"
            aria-hidden="true"
            className="da-spinner"
          >
            <circle cx="8" cy="8" r="6" stroke="var(--accent-alpha-28)" strokeWidth="2" />
            <path d="M8 2a6 6 0 0 1 6 6" stroke="var(--accent)" strokeWidth="2" strokeLinecap="round" />
          </svg>
          <span className={styles.resolvingLabel}>Loading prototype…</span>
        </div>
      </AppLayout>
    )
  }

  // No ready prototype yet, and generation NOT yet requested → the empty state
  // with an explicit "Generate prototype" button. The GenerateModal is NOT mounted
  // here: not mounting it (open=false) keeps its connector-load + savedPreference
  // auto-skip / locate-on-mount effects from firing, so the locate pipeline never
  // runs without user intent. Reuses the native da-prototype-empty block (same
  // classes / control as the no-PRD empty state above) so styling stays consistent.
  if (!generateRequested) {
    return (
      <AppLayout>
        <PrototypeEmptyState
          variant="hero"
          testid="prototype-route-empty"
          art={<IconGrid size={48} />}
          title="Bring this PRD to life"
          sub="Generate an interactive, clickable prototype straight from your PRD — grounded in your connected codebase, so it looks like your real app. Share it and refine it with comments."
          action={
            <button
              type="button"
              className="fc-btn-secondary"
              onClick={() => setGenerateRequested(true)}
            >
              <IconTerminalPrompt size={13} />
              Generate prototype
            </button>
          }
          meta={["scoped against your codebase or design source"]}
          chips={[
            { icon: <IconPin size={15} />, label: "Interactive & clickable" },
            { icon: <IconCopy size={15} />, label: "Matches your app's UI" },
            { icon: <IconShare size={15} />, label: "Shareable + comments" },
          ]}
        />
      </AppLayout>
    )
  }

  // Generation requested via the empty-state button → mount the generate panel.
  // open is gated on generateRequested (never a hardcoded literal): the panel
  // opens only after an explicit click, and the prd-keyed reset effect re-gates it
  // on navigation between PRDs. A successful generation reveals the new prototype
  // IN-TAB via handleGenDone (no overlay navigation). The GenerationLoadingScreen
  // covers kickoff-to-ready feedback.
  //
  // The open prop is ALSO gated on `!genLoading` so the modal yields the instant the
  // full-screen build loader takes over: when generation kicks off, handleGenStart
  // flips genLoading true and the "Building your prototype" GenerationLoadingScreen
  // opens — without this gate the modal would stay open for the build's ~1-2s tail,
  // stacking its locate/loading UI under the full-screen loader. Closing via
  // open=false here is SAFE: the generate POST already fired (the prototype id was
  // captured via onKickoff/onGenStart), so closing does NOT abort the in-flight
  // generation — handleGenDone + the resolve/poll path + the loading screen own the
  // rest. The onClose gate (buildGatedOnClose reads genLoadingRef.current) also
  // suppresses the "navigate to brief" close while genLoading is true, so this
  // unmount fires no spurious navigation. During LOCATE, genLoading is still false
  // (it only flips at generation kickoff), so the modal stays open showing the
  // locate / picker / error UI exactly as before.
  return (
    <AppLayout>
      <div className="design-agent-surface da-prototype-page" data-testid="prototype-route">
        {/* Scoped error boundary (belt-and-suspenders to genError): any UNGUARDED
            render throw inside the locate/generate surface degrades to a clean
            in-surface fallback + Retry, instead of escaping to the framework's
            whole-page "Application error" (the repo has no app-wide error.tsx).
            Its Retry re-mounts the guarded subtree; onReset also clears genError +
            re-arms the panel so a recovered throw lands on a fresh generate panel,
            not a stale one. Scoped to THIS surface only — the app shell survives. */}
        <GenerateSurfaceErrorBoundary onReset={handleGenErrorRetry}>
          <GenerateModal
            open={generateRequested && !genLoading}
            onClose={buildGatedOnClose(
              () => genLoadingRef.current,
              () => goTo("brief"),
            )}
            prdId={prdId}
            figmaFileKey={figmaFileKey}
            platformHint={platformHint}
            onGenStart={handleGenStart}
            onKickoff={(id) => setGenProtoId(id)}
            onGenDone={handleGenDone}
            savedPreference={savedPreference}
          />
          <GenerationLoadingScreen
            open={genLoading}
            figmaFileKey={genFigmaKey}
            githubRepo={genGithubRepo}
            prototypeId={genProtoId}
            onCancel={handleGenCancel}
            onNotifyWhenReady={genProtoId != null ? handleNotifyWhenReady : undefined}
          />
        </GenerateSurfaceErrorBoundary>
      </div>
    </AppLayout>
  )
}
