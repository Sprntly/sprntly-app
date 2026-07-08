"use client"

// Canonical, only-sanctioned way to add a generate/view-prototype trigger
// anywhere in the app — any future surface needing this affordance must use
// this hook (directly, or via the sibling <GeneratePrototypeCTA> component),
// never hand-roll a new existence-check/loading/nav implementation.

/**
 * Shared "generate or view a prototype" state machine, extracted from the
 * near-identical copies that used to live independently in `PrdPanelContent`'s
 * `ViewPrototypeButton`, `ApproveModal`, `BriefChat`'s finding-card
 * `cardPreview`, and (partially) `PrototypeRoute`. This hook owns:
 *
 *   - the read-only existence check (`designAgentApi.getByPrd`) that decides
 *     whether the CTA reads "View Prototype" or "Generate Prototype",
 *   - the `<GenerateModal>` open/close state (internal by default, or fully
 *     controlled by a host that already owns an external open signal),
 *   - the full-screen `<GenerationLoadingScreen>` overlay lifecycle
 *     (min-visible-duration + safety-ceiling + kickoff-failure-guard timers,
 *     copied byte-for-byte from `ApproveModal`'s existing implementation —
 *     the richest of the 4 prior copies),
 *   - the "Notify me when ready" default side effects, and
 *   - the terminal post-success outcome (navigate to the in-tab canvas, or
 *     hand the prototype to a host-supplied `onSuccess`).
 *
 * It deliberately does NOT import or reuse anything from `DesignAgentDrawer`
 * (`runGenerateFlow`, `buildGenerateParams`, or the drawer components
 * themselves) — every live entry point already delegates its actual
 * submit/poll orchestration to `<GenerateModal>`, which this hook wraps
 * without rewriting. See `DesignAgentDrawer.tsx`'s file-header fate comment
 * for the full grounding on why those drawer exports stay untouched.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { useNavigation } from "../../context/NavigationContext"
import { useWorkspace } from "../../context/WorkspaceContext"
import { updateWorkspace } from "../../lib/onboarding/store"
import type { DesignSourcePreference } from "../../lib/onboarding/types"
import { designAgentApi, type PrototypeRecord } from "../../lib/api"
import { prototypePath } from "../../lib/routes"
import type { DesignAgentGenResult } from "../../lib/runDesignAgentGeneration"

// Min-visible duration. If generation dedup-returns an existing prototype
// almost instantly, the overlay would otherwise flash; keep it visible at
// least this long so it actually registers, then dismiss promptly once BOTH
// conditions hold (generation resolved AND this minimum elapsed). Copied from
// `ApproveModal`'s existing constant — do not invent a new value.
const MIN_VISIBLE_MS = 2500
// Hard ceiling so the overlay can never hang if neither callback fires (e.g. a
// swallowed kickoff failure). `runGenerateFlow`'s own poll caps at 6 min; this
// is a slightly-longer belt-and-braces backstop. Copied from `ApproveModal`.
const SAFETY_MAX_MS = 6.5 * 60 * 1000
// Kickoff-failure guard delay. `runGenerateFlow` swallows a kickoff error
// (toasts "Generate failed", leaves the modal OPEN, never fires the done
// callback) — so on success it ALWAYS closes the modal. If the modal is still
// open a beat after start, the kickoff failed: dismiss the overlay so it
// doesn't hang to the safety ceiling. Copied from `ApproveModal`.
const KICKOFF_FAILURE_GUARD_MS = 1500

export type GeneratePrototypeCtaState =
  | "loading" // existence check in flight (skipExistenceCheck=false only)
  | "generating" // cross-surface da:generating signal active (opt-in only)
  | "view" // existing ready prototype resolved
  | "generate" // no prototype yet / existence check skipped

/** Pure derivation of the CTA's visible label — exported separately so it is
 *  unit-testable without mounting the hook. */
export function generatePrototypeCtaLabel(cta: GeneratePrototypeCtaState): string {
  switch (cta) {
    case "loading":
      return "Loading…"
    case "generating":
      return "Generating Prototype"
    case "view":
      return "View Prototype"
    case "generate":
      return "Generate Prototype"
  }
}

export type UseGeneratePrototypeOptions = {
  figmaFileKey?: string | null
  /** Default false. When true, the hook performs NO getByPrd existence check —
   *  `existing` stays permanently null, `cta` is permanently "generate", and
   *  handleCtaClick always opens the GenerateModal (never navigates). Set this
   *  when the host has ALREADY established (via its own richer lookup) that no
   *  prototype exists — e.g. PrototypeRoute's empty-state branch, reached only
   *  after its own getActiveByPrd/getLatestByPrd sequence returned "none". This
   *  is what keeps the single-getByPrd-call goal honest: PrototypeRoute's
   *  resume/retry logic is intentionally NOT replaced by this hook (see
   *  Scope boundary) — it already resolved existence more richly than a bare
   *  getByPrd can (ready vs generating vs failed), so the hook must not re-do
   *  that work with a narrower (ready-only) check. */
  skipExistenceCheck?: boolean
  /** Default false. When true, the hook listens for the (unscoped, no-prdId)
   *  `da:generating` / `da:generating-done` window CustomEvents to drive
   *  `cta === "generating"`, matching ApproveModal's existing cross-instance
   *  signal. CRITICAL: these events carry NO prdId/prototypeId-to-PRD mapping —
   *  they are safe ONLY in a single-current-PRD host (at most one mounted
   *  instance tracking "the active PRD" at a time, e.g. ApproveModal keyed off
   *  ContentContext). NEVER pass true in a host that renders multiple
   *  simultaneous instances for different PRDs (e.g. BriefChat's per-finding-card
   *  loop) — every card would incorrectly flip to "Generating Prototype" the
   *  moment ANY one card's generation fires notify. Defaults false so a caller
   *  must opt in deliberately. */
  listenForCrossSurfaceGenerating?: boolean
  /** Fires on the terminal SUCCESS outcome instead of the hook's default
   *  `router.push(prototypePath(prdId))`. Use to reveal in-tab instead of
   *  navigating (PrototypeRoute). */
  onSuccess?: (prototype: PrototypeRecord) => void
  /** Controlled-open escape hatch. Supply BOTH `open` and `onOpenChange` when
   *  the host already owns an external open/close signal that must stay the
   *  source of truth — e.g. ApproveModal's NavigationContext `activeModal ===
   *  "generate"` union, which nothing else in the app reads today but which
   *  this hook must not silently duplicate with a second, competing boolean.
   *  When both are supplied, `generateModalProps.open` mirrors `open` exactly
   *  every render, and `openGenerateModal()` / the GenerateModal's internal
   *  close both call `onOpenChange` instead of an internal setState. Omit both
   *  (the default) for the hook's own internal open/close state. Supplying
   *  only one of the pair is a programmer error (not defended against beyond a
   *  dev-time console.warn — this is an internal frontend hook, not a public
   *  API surface). */
  open?: boolean
  onOpenChange?: (open: boolean) => void
  /** Fires when the user clicks "Notify me when ready" INSTEAD of the hook's
   *  default (dispatch `da:generating`, close the overlay, keep the mounted
   *  GenerateModal's onGenDone alive to fire a toast on completion — matches
   *  ApproveModal's current behavior for a host that stays mounted). Hosts that
   *  navigate away on notify (PrototypeRoute) MUST supply their own override
   *  that ALSO dispatches `da:notify-generation` with `{prototypeId, prdId}`
   *  before navigating — the shell's `useGenerationNotify` only resumes polling
   *  for ids it receives via that event; omitting it strands the poll on
   *  unmount. */
  onNotifyWhenReady?: () => void
}

/** Spread onto <GenerateModal>. */
export type GenerateModalWiredProps = {
  open: boolean
  onClose: () => void
  prdId: number | null
  figmaFileKey: string | null
  onGenStart: (ctx?: { figmaFileKey?: string | null; githubRepo?: string | null }) => void
  onKickoff: (prototypeId: number) => void
  onGenDone: (result?: DesignAgentGenResult) => void
  savedPreference: DesignSourcePreference | null
  onSavePreference: (pref: DesignSourcePreference) => Promise<void>
}

/** Spread onto <GenerationLoadingScreen>. */
export type GenerationLoadingScreenWiredProps = {
  open: boolean
  figmaFileKey: string | null
  githubRepo: string | null
  prototypeId: number | null
  onCancel: () => void
  onNotifyWhenReady: () => void
}

export type UseGeneratePrototypeResult = {
  existing: PrototypeRecord | null
  isLoadingExisting: boolean
  cta: GeneratePrototypeCtaState
  ctaLabel: string
  /** Returns a Promise so a host that needs a busy indicator around the async
   *  "view" re-verify (ApproveModal's `viewBusy`, dimming the option while it
   *  re-checks the prototype still exists before navigating) can `await` it;
   *  callers that don't need this (PrdPanelContent/BriefChat, via the plain
   *  `onClick`) may ignore the returned Promise exactly as any async React
   *  event handler is normally used. The "generate" branch resolves
   *  synchronously (no network call), so awaiting it around a busy-flag is a
   *  harmless same-tick no-op for hosts that don't need it. */
  handleCtaClick: () => Promise<void>
  /** Unconditionally opens the GenerateModal, independent of `cta`/`existing`.
   *  `handleCtaClick`'s "generate" branch is a thin wrapper over this. Exists
   *  for hosts with their OWN click-routing logic driven by a DIFFERENT
   *  existence source of truth than this hook's — e.g. BriefChat, whose
   *  per-card View/Generate decision comes from `useBriefPrototypeMap`'s batch
   *  fetch, not this hook's `existing`. Those hosts call `useGeneratePrototype`
   *  with `skipExistenceCheck: true` (so the hook does no redundant fetch of
   *  its own) and call `openGenerateModal()` directly from their own click
   *  handler once THEY have decided generation should start. Safe to call from
   *  a handler that also just changed the `prdId` passed into this hook in the
   *  same tick (a plain state setter — both queue for the next render; the
   *  GenerateModal mounted after that render reads the fresh `prdId`). */
  openGenerateModal: () => void
  deleteExisting: () => Promise<void>
  refetchExisting: () => void
  generateModalProps: GenerateModalWiredProps
  loadingScreenProps: GenerationLoadingScreenWiredProps
}

export function useGeneratePrototype(
  prdId: number | null,
  options?: UseGeneratePrototypeOptions,
): UseGeneratePrototypeResult {
  const figmaFileKey = options?.figmaFileKey ?? null
  const skipExistenceCheck = options?.skipExistenceCheck ?? false
  const listenForCrossSurfaceGenerating = options?.listenForCrossSurfaceGenerating ?? false
  const { onSuccess, open: controlledOpen, onOpenChange, onNotifyWhenReady } = options ?? {}

  const router = useRouter()
  const { showToast } = useNavigation()
  const { workspace, refresh } = useWorkspace()
  const savedPreference = workspace?.design_source ?? null

  // ── Controlled-open escape hatch ──────────────────────────────────────────
  const isControlled = controlledOpen !== undefined && onOpenChange !== undefined
  const [internalOpen, setInternalOpen] = useState(false)
  const genModalOpen = isControlled ? (controlledOpen as boolean) : internalOpen

  // Dev-time guard: supplying only one of open/onOpenChange is a programmer
  // error (the pair must travel together — see the option's doc comment).
  // Not defended against beyond this warning; this is an internal hook, not a
  // public API surface.
  const controlledPairIncomplete =
    (controlledOpen !== undefined) !== (onOpenChange !== undefined)
  useEffect(() => {
    if (controlledPairIncomplete && process.env.NODE_ENV !== "production") {
      // eslint-disable-next-line no-console
      console.warn(
        "useGeneratePrototype: supply BOTH `open` and `onOpenChange` for controlled mode, or neither — supplying only one is a programmer error.",
      )
    }
  }, [controlledPairIncomplete])

  const openGenerateModal = useCallback(() => {
    if (isControlled) {
      onOpenChange!(true)
    } else {
      setInternalOpen(true)
    }
  }, [isControlled, onOpenChange])

  const closeGenerateModal = useCallback(() => {
    if (isControlled) {
      onOpenChange!(false)
    } else {
      setInternalOpen(false)
    }
  }, [isControlled, onOpenChange])

  // ── Existence check ────────────────────────────────────────────────────────
  const [existing, setExisting] = useState<PrototypeRecord | null>(null)
  const [isLoadingExisting, setIsLoadingExisting] = useState(!skipExistenceCheck)
  const [isGenerating, setIsGenerating] = useState(false)
  const [refetchNonce, setRefetchNonce] = useState(0)

  const refetchExisting = useCallback(() => setRefetchNonce((n) => n + 1), [])

  useEffect(() => {
    if (skipExistenceCheck || prdId == null) {
      setExisting(null)
      setIsLoadingExisting(false)
      return
    }
    let cancelled = false
    setIsLoadingExisting(true)
    designAgentApi
      .getByPrd(prdId)
      .then((proto) => {
        if (cancelled) return
        setExisting(proto && proto.status === "ready" && proto.bundle_url ? proto : null)
        // Belt-and-braces seed for the cross-surface "generating" signal,
        // reusing this SAME fetch (no extra network call): a fresh mount/prdId
        // change that lands mid-generation should show "Generating Prototype"
        // even if the window listener below missed the past da:generating
        // dispatch (mirrors ApproveModal's modal-open-time re-check).
        if (listenForCrossSurfaceGenerating && proto && proto.status === "generating") {
          setIsGenerating(true)
        }
        setIsLoadingExisting(false)
      })
      .catch(() => {
        if (cancelled) return
        setExisting(null)
        setIsLoadingExisting(false)
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prdId, skipExistenceCheck, refetchNonce])

  // Window CustomEvent listeners for the opt-in cross-surface generating
  // signal. Mounted only when listenForCrossSurfaceGenerating is true — see
  // that option's doc comment for why this must stay opt-in.
  useEffect(() => {
    if (!listenForCrossSurfaceGenerating) return
    const onGeneratingEvt = () => setIsGenerating(true)
    const onDoneEvt = () => setIsGenerating(false)
    window.addEventListener("da:generating", onGeneratingEvt)
    window.addEventListener("da:generating-done", onDoneEvt)
    return () => {
      window.removeEventListener("da:generating", onGeneratingEvt)
      window.removeEventListener("da:generating-done", onDoneEvt)
    }
  }, [listenForCrossSurfaceGenerating])

  const cta: GeneratePrototypeCtaState = useMemo(() => {
    if (isLoadingExisting) return "loading"
    if (listenForCrossSurfaceGenerating && isGenerating) return "generating"
    if (existing) return "view"
    return "generate"
  }, [isLoadingExisting, listenForCrossSurfaceGenerating, isGenerating, existing])
  const ctaLabel = generatePrototypeCtaLabel(cta)

  const deleteExisting = useCallback(async () => {
    if (!existing) return
    await designAgentApi.delete(existing.id)
    setExisting(null)
  }, [existing])

  // ── Loading-overlay lifecycle (mirrors ApproveModal's existing shape) ──────
  const [genLoading, setGenLoading] = useState(false)
  const [genFigmaKey, setGenFigmaKey] = useState<string | null>(null)
  const [genGithubRepo, setGenGithubRepo] = useState<string | null>(null)
  const [genProtoId, setGenProtoId] = useState<number | null>(null)

  const shownAtRef = useRef(0)
  const resolvedRef = useRef(false)
  const safetyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const minTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  // The prototype to hand off (navigate / onSuccess) once the overlay actually
  // dismisses. Null on failure/cancel/notify → no hand-off.
  const pendingResultRef = useRef<PrototypeRecord | null>(null)
  // True once the user has chosen "Notify me when ready" for the in-flight
  // run — the terminal onGenDone then skips navigation/onSuccess entirely
  // (the notify path already handed off).
  const notifyModeRef = useRef(false)
  // Live mirror of the generate modal's open state for the kickoff-failure
  // guard's deferred timeout (avoids a stale closure).
  const generateActiveRef = useRef(false)
  generateActiveRef.current = genModalOpen

  const clearOverlayTimers = useCallback(() => {
    if (safetyTimerRef.current) clearTimeout(safetyTimerRef.current)
    if (minTimerRef.current) clearTimeout(minTimerRef.current)
    safetyTimerRef.current = null
    minTimerRef.current = null
  }, [])

  const hideLoading = useCallback(() => {
    clearOverlayTimers()
    setGenLoading(false)
    if (pendingResultRef.current) {
      const revealed = pendingResultRef.current
      pendingResultRef.current = null
      if (onSuccess) {
        onSuccess(revealed)
      } else {
        router.push(prototypePath(prdId))
      }
    }
  }, [clearOverlayTimers, onSuccess, router, prdId])

  const handleGenStart = useCallback(
    (ctx?: { figmaFileKey?: string | null; githubRepo?: string | null }) => {
      setGenFigmaKey(ctx?.figmaFileKey ?? null)
      setGenGithubRepo(ctx?.githubRepo ?? null)
      setGenProtoId(null)
      shownAtRef.current = Date.now()
      resolvedRef.current = false
      notifyModeRef.current = false
      pendingResultRef.current = null
      setGenLoading(true)
      if (safetyTimerRef.current) clearTimeout(safetyTimerRef.current)
      safetyTimerRef.current = setTimeout(hideLoading, SAFETY_MAX_MS)
      setTimeout(() => {
        if (!resolvedRef.current && generateActiveRef.current) hideLoading()
      }, KICKOFF_FAILURE_GUARD_MS)
    },
    [hideLoading],
  )

  const handleKickoff = useCallback((prototypeId: number) => {
    setGenProtoId(prototypeId)
  }, [])

  const handleGenDone = useCallback(
    (result?: DesignAgentGenResult) => {
      if (resolvedRef.current) return
      resolvedRef.current = true
      if (notifyModeRef.current) {
        // Notify mode already dismissed the overlay and handed off (default or
        // host override) — this terminal callback only clears timers.
        clearOverlayTimers()
        return
      }
      pendingResultRef.current = result?.ok && result.prototype ? result.prototype : null
      const remaining = MIN_VISIBLE_MS - (Date.now() - shownAtRef.current)
      if (remaining <= 0) {
        hideLoading()
      } else {
        if (minTimerRef.current) clearTimeout(minTimerRef.current)
        minTimerRef.current = setTimeout(hideLoading, remaining)
      }
    },
    [hideLoading, clearOverlayTimers],
  )

  // Default "Notify me when ready" side effects — reproduces
  // ApproveModal.handleNotifyWhenReady exactly (byte-for-byte copy of that
  // function's 3 side effects): toast, conditional da:generating dispatch,
  // close the overlay. A host-supplied override replaces this entirely (AC12).
  const handleNotifyWhenReady = useCallback(() => {
    notifyModeRef.current = true
    if (onNotifyWhenReady) {
      onNotifyWhenReady()
      return
    }
    showToast("Prototype is processing", "We'll let you know when it's ready.")
    if (genProtoId != null) {
      window.dispatchEvent(
        new CustomEvent("da:generating", { detail: { prototypeId: genProtoId } }),
      )
    }
    setGenLoading(false)
  }, [onNotifyWhenReady, showToast, genProtoId])

  // No sanctioned "stop the running generation" contract exists at this
  // wrapper layer — the true-abort endpoint (`designAgentApi.cancel`) is only
  // wired by PrototypeRoute's own, out-of-scope state machine (see this
  // ticket's Scope boundary). Cancelling here safely dismisses the overlay
  // without navigating or toasting, so the user is never trapped; the
  // in-flight generation itself is left to resolve in the background (the
  // mounted GenerateModal's onGenDone still fires, but notifyModeRef being set
  // means it becomes a no-op — matching the "stays mounted, no shell handoff"
  // posture the notify path already covers, minus the toast).
  const handleCancel = useCallback(() => {
    clearOverlayTimers()
    notifyModeRef.current = true
    setGenLoading(false)
  }, [clearOverlayTimers])

  const handleSavePreference = useCallback(
    async (pref: DesignSourcePreference) => {
      if (!workspace) return
      await updateWorkspace(workspace.id, { design_source: pref })
      await refresh()
    },
    [workspace, refresh],
  )

  const handleCtaClick = useCallback(async () => {
    if (cta === "loading" || cta === "generating") return
    if (cta === "view") {
      if (prdId == null) return
      try {
        const fresh = await designAgentApi.getByPrd(prdId)
        if (fresh && fresh.status === "ready" && fresh.bundle_url) {
          router.push(prototypePath(prdId))
        } else {
          setExisting(null)
          showToast(
            "Prototype unavailable",
            "The prototype was removed. Generate a new one.",
          )
        }
      } catch {
        setExisting(null)
        showToast(
          "Prototype unavailable",
          "The prototype was removed. Generate a new one.",
        )
      }
      return
    }
    openGenerateModal()
  }, [cta, prdId, router, showToast, openGenerateModal])

  const generateModalProps: GenerateModalWiredProps = {
    open: genModalOpen,
    onClose: closeGenerateModal,
    prdId,
    figmaFileKey,
    onGenStart: handleGenStart,
    onKickoff: handleKickoff,
    onGenDone: handleGenDone,
    savedPreference,
    onSavePreference: handleSavePreference,
  }

  const loadingScreenProps: GenerationLoadingScreenWiredProps = {
    open: genLoading,
    figmaFileKey: genFigmaKey,
    githubRepo: genGithubRepo,
    prototypeId: genProtoId,
    onCancel: handleCancel,
    onNotifyWhenReady: handleNotifyWhenReady,
  }

  return {
    existing,
    isLoadingExisting,
    cta,
    ctaLabel,
    handleCtaClick,
    openGenerateModal,
    deleteExisting,
    refetchExisting,
    generateModalProps,
    loadingScreenProps,
  }
}
