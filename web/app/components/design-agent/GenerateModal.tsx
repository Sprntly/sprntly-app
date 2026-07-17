"use client"

/*
 * "Generate prototype" modal (the product's v3 generate step). Opened from the
 * "Approve & next step" modal's "Generate Prototype" option, which hands
 * visibility to the shared navigation modal union (`activeModal === "generate"`).
 * Connector rows are driven by REAL connector status (`connectorsApi.list()`);
 * the GitHub repo selector is wired to the real repo-listing endpoint, and the
 * Figma file selector is wired to the real file-listing endpoint
 * (`designAgentApi.listFigmaFiles`), degrading to an honest "Couldn't load
 * designs" empty state on failure (never fake files). The Generate button reuses
 * the same real generation flow as the launcher drawer (`designAgentApi.generate`
 * → the shared generate flow) — no faked calls. Connector + repo + Figma-file
 * fetches are wrapped in the shared auth-retry helper so a transient
 * token-refresh 401 holds the last-known rows (the modal does not reflow and the
 * Generate button does not move). The selected Figma file (`figmaFileSel`) flows
 * into generation via the existing `figmaFileSel || figmaFileKey` fallback; the
 * selected GitHub repo threads in as prompt context (`github_repo`).
 */

import { useEffect, useRef, useState } from "react"
import { useNavigation } from "../../context/NavigationContext"
import {
  connectorsApi,
  designAgentApi,
  withAuthRetry,
  ApiError,
  type ConnectionSummary,
  type GitHubRepo,
  type LocateResponse,
  type LocateCandidate,
  type LocateJobHandle,
  type LocateJobStatus,
} from "../../lib/api"
import {
  runDesignAgentGeneration,
  type DesignAgentGenResult,
} from "../../lib/runDesignAgentGeneration"
import {
  runGenerateFlow,
  buildGenerateParams,
  DEFAULT_PLATFORM,
  type TargetPlatform,
} from "./DesignAgentDrawer"
import { SourceConnectHint } from "./SourceConnectHint"
import { getGenerateConnectorRowState } from "../../lib/generateConnectorRowState"
import { IconClose } from "../shared/app-icons"
import {
  LocateConfirmView,
  type LocateConfirmCandidate,
} from "./ClarifyingQuestionSurface"
import type { DesignSourcePreference } from "../../lib/onboarding/types"
import { SourceTypePills } from "./SourceTypePills"
import { GenerateLoadingState } from "./GenerateLoadingState"
import { GenerationCancelButton } from "./GenerationCancelButton"
import locateErrorStyles from "./GenerateModalLocateError.module.css"
import type { LocatePhaseState } from "./GenerationLoadingScreen"

const PLATFORM_OPTIONS: { value: TargetPlatform; label: string }[] = [
  { value: "desktop", label: "Desktop" },
  { value: "mobile", label: "Mobile" },
  { value: "both", label: "Both" },
]

/** The modal's generate-time design-source union: the three durable sources
 *  plus the per-run "screenshot" source. Screenshot is deliberately NOT part
 *  of `DesignSourcePreference` — a file is per-run context, not a durable
 *  source preference — so the widening lives here, not in the shared types. */
type ModalDesignSource = "figma" | "github" | "website" | "screenshot"

/** The four source pills, in the canonical order (the shared component's three
 *  defaults, then the per-run Screenshot option). Screenshot needs no
 *  connector — always selectable. */
const MODAL_SOURCE_OPTIONS: { value: ModalDesignSource; label: string }[] = [
  { value: "github", label: "From our codebase" },
  { value: "figma", label: "Figma" },
  { value: "website", label: "Website" },
  { value: "screenshot", label: "Screenshot" },
]

// SourceTypePills is a SHARED component (settings pane + this modal) whose
// props are typed to the three durable sources. Its runtime is value-agnostic
// (it maps `options` and echoes the clicked value back), so the modal widens
// it at the call-site to carry the per-run screenshot option instead of
// widening the shared component's own contract (out of this change's scope —
// the settings pane must never offer a non-persistable source).
const ModalSourcePills = SourceTypePills as unknown as (props: {
  value: ModalDesignSource
  onChange: (v: ModalDesignSource) => void
  options?: { value: ModalDesignSource; label: string }[]
}) => ReturnType<typeof SourceTypePills>

/**
 * Single-modal phase machine for the generate-entry flow.
 *
 *   config            → the source/platform/instructions form (the resting state)
 *   locating          → loading UI is visible while the screen-resolve job runs
 *   picker            → an ambiguous match needs the user to pick a screen
 *   unmapped-resolve  → no match; pick a screen or switch back to config
 *   error             → the resolve job failed or timed out; an explicit error
 *                       message + Retry button on the loading surface. This is a
 *                       FIRST-CLASS phase, not a fall-through to config: a failed
 *                       locate must NOT silently collapse back to the PRD form
 *                       (the prod hang→collapse bug). Retry re-runs from the POST.
 *   generating        → a real run exists; hand off to the loading screen + drawer
 *
 * The modal stays MOUNTED across every phase and only hands off (onGenStart /
 * onKickoff / onGenDone) once a real prototype run has been kicked off. The key
 * fix this encodes: the loading SCREEN is decoupled from the resolve CALL —
 * `locating` mounts immediately on generate-click, and the resolve job (POST →
 * poll) fires behind it, so the user never stares at a frozen form.
 */
type FlowPhase =
  | "config"
  | "locating"
  | "picker"
  | "unmapped-resolve"
  | "error"
  | "generating"

// Poll loop tuning for the async locate contract. The POST returns a
// job id; we GET the job on an interval until it is done/error, capped by an
// overall timeout so a stuck job surfaces an explicit error instead of hanging
// forever (and never collapses back to the PRD form).
//   - INTERVAL: cadence between successive poll GETs (~1s).
//   - TIMEOUT:  overall ceiling on the whole resolve (~90s). On expiry → error.
//   - MAX_RETRIES: transient-failure budget. A poll GET that 5xxs or network-
//     errors (or a 5xx on the POST) is retried up to this many times with
//     linear backoff before the flow gives up. A 404/400 is TERMINAL — it is
//     not retried; it goes straight to the error phase.
const LOCATE_POLL_INTERVAL_MS = 1000
const LOCATE_POLL_TIMEOUT_MS = 90_000
const LOCATE_POLL_MAX_RETRIES = 4

/** A transient failure is a 5xx or a non-ApiError (network/abort-adjacent). A
 *  404 or 400 from the poll/POST is terminal — distinguishing the two is what
 *  lets the loop retry a flaky backend while failing fast on an unknown job. */
function isTransientLocateError(err: unknown): boolean {
  if (err instanceof ApiError) return err.status >= 500
  // Anything that is not a structured ApiError (network error, JSON parse,
  // fetch reject) is treated as transient and retried.
  return true
}

/**
 * A candidate is "real" (pickable) when it names an actual host surface: it has
 * a usable resolution key — a non-empty route OR a non-empty id — AND at least
 * one component, AND is NOT a decline rationale. The id-OR-route check is
 * load-bearing: a non-route host (the app shell, an in-page section) legitimately
 * carries an EMPTY route and is keyed only by its id (chosen_screen_id is the
 * resolution key `runGenerateForRoute` forwards). A route-only test would wrongly
 * filter that real host out. The backend can also return a degenerate placeholder
 * (empty route AND empty id / zero components / "no screen can be identified"
 * rationale) inside a ranked_confirm; surfacing THAT as a "Suggested / Use this
 * screen" card is a wrong-screen trap. This predicate keeps the placeholders out
 * of the picker while preserving a real empty-route host.
 */
export function isRealCandidate(c: LocateCandidate): boolean {
  const route = (c.route ?? "").trim()
  const id = (c.id ?? "").trim()
  const componentCount = c.component_count ?? 0
  const rationale = c.rationale ?? ""
  return (
    (route.length > 0 || id.length > 0) &&
    componentCount > 0 &&
    !/no screen can be identified/i.test(rationale)
  )
}

/** Local image-frame glyph — the mockup's attach/screenshot icon.
 *  Kept local rather than added to shared app-icons (single-use, one component). */
function IconImage({ size = 16, className }: { size?: number; className?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={className}
    >
      <rect x="3" y="3" width="18" height="18" rx="2" />
      <circle cx="9" cy="9" r="1.6" />
      <path d="m21 15-4.5-4.5L7 20" />
    </svg>
  )
}

/** Image-as-steer honesty states, mirroring the backend's
 *  `image_status`. Only "applied" may surface cues / an "applied" affirmation. */
type LocateImageStatus = "absent" | "applied" | "ignored_oversize" | "ignored_decode"

/** Client-side bounds for the steer screenshot. Accept PNG/JPEG/WebP
 *  only and reject > ~5 MB BEFORE upload; downscale to ≤1568px longest edge
 *  (Anthropic's vision sweet spot) so request size + vision token cost stay sane.
 *  The server enforces its own cap and falls open to text-only regardless. */
const STEER_IMAGE_ACCEPTED_TYPES = ["image/png", "image/jpeg", "image/webp"] as const
const STEER_IMAGE_MAX_BYTES = 5 * 1024 * 1024
const STEER_IMAGE_MAX_EDGE = 1568

/**
 * Downscale + re-encode a user-selected screenshot to a base64 data URL bounded
 * at STEER_IMAGE_MAX_EDGE on its longest edge, using the native canvas API (no
 * dependency). Defensive for SSR / no-canvas: if there's no document or no 2D
 * context, or the image is already within bounds, the original data URL is
 * returned unchanged. Pure — never touches storage; the caller holds the result
 * in memory only. Injectable in tests via the `_testDownscale` prop (jsdom has
 * no real canvas / image decode).
 */
async function downscaleImageToDataUrl(file: File): Promise<string> {
  const readAsDataUrl = (f: File) =>
    new Promise<string>((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => resolve(reader.result as string)
      reader.onerror = () => reject(new Error("read-failed"))
      reader.readAsDataURL(f)
    })

  const dataUrl = await readAsDataUrl(file)
  if (typeof document === "undefined" || typeof Image === "undefined") {
    return dataUrl
  }

  const img = await new Promise<HTMLImageElement>((resolve, reject) => {
    const el = new Image()
    el.onload = () => resolve(el)
    el.onerror = () => reject(new Error("decode-failed"))
    el.src = dataUrl
  })

  const longest = Math.max(img.width, img.height)
  if (!longest || longest <= STEER_IMAGE_MAX_EDGE) return dataUrl

  const scale = STEER_IMAGE_MAX_EDGE / longest
  const canvas = document.createElement("canvas")
  canvas.width = Math.round(img.width * scale)
  canvas.height = Math.round(img.height * scale)
  const ctx = canvas.getContext("2d")
  if (!ctx) return dataUrl
  ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
  // PNG/WebP keep their type (transparency); anything else re-encodes to JPEG.
  const outType =
    file.type === "image/png" || file.type === "image/webp" ? file.type : "image/jpeg"
  return canvas.toDataURL(outType, 0.9)
}

/** Decode a base64 data URL into a Blob — the DOWNSCALED bytes the screenshot
 *  upload sends (never the raw file: consistent with the steer flow, caps
 *  vision-token cost, and stays far under the server's 8 MB guard). Manual
 *  atob decode rather than fetch("data:…") so the conversion is dependency-free
 *  and deterministic under jsdom. */
function dataUrlToBlob(dataUrl: string): Blob {
  const comma = dataUrl.indexOf(",")
  const meta = dataUrl.slice(0, Math.max(comma, 0))
  const mime = /^data:([^;,]+)/.exec(meta)?.[1] ?? "application/octet-stream"
  const bin = atob(dataUrl.slice(comma + 1))
  const bytes = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i)
  return new Blob([bytes], { type: mime })
}

/** Maps LocateCandidate[] to the shape LocateConfirmView expects. */
export function mapLocateCandidates(ranked: LocateCandidate[]): LocateConfirmCandidate[] {
  return ranked.map((c, i) => ({
    id: c.id ?? "",
    route: c.route ?? "",
    entry_component: c.entry_component ?? "",
    component_count: c.component_count ?? 0,
    rationale: c.rationale ?? "",
    is_top: i === 0,
  }))
}

/**
 * Visibility is driven by the shared navigation modal union: the parent threads
 * `open={activeModal === "generate"}` and `onClose={closeModal}` in via these
 * props. `open` toggles render; `prdId`/`figmaFileKey` come from the current PRD
 * content.
 */
export function GenerateModal({
  open,
  onClose,
  prdId,
  figmaFileKey,
  // Full-screen loading-screen hooks. onGenStart fires the instant the kickoff
  // is requested (so the parent can show the overlay); onKickoff fires once the
  // generate POST returns with the prototype_id (so the loading screen can subscribe
  // to the SSE stream); onGenDone fires on the terminal generation outcome
  // (ready/failed/timeout) so the parent can dismiss it.
  onGenStart,
  onKickoff,
  onGenDone,
  onCancel,
  // Persisted design source preference. When set and the named source is
  // healthy (connected + key/repo valid), the modal fires generation immediately
  // without user interaction and closes itself. Pass null to always show.
  savedPreference,
  onSavePreference,
  // Pre-build locate phase bridge. Emits the current locate phase so the parent
  // can thread it into the full-screen GenerationLoadingScreen. Accepted here
  // for forward-compat with the locate-in-loading-screen rollout; this version
  // of the modal drives locate in-modal (no-op on the callback).
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  onLocatePhase: _onLocatePhase,
  // Injected for testing — bypass the async useEffect cycle so node-env vitest
  // can render the modal in a known connector/repo/source state without a DOM.
  // Omit in production; defaults preserve real behaviour.
  _testConnections,
  _testRepos,
  _testInitSource,
  _testInitRepoSel,
  _testFlowPhase,
  _testLocateResult,
  _testLocateError,
  _testMatchedRoute,
  _testProceedNote,
  _testPollIntervalMs,
  _testPollTimeoutMs,
  _testPollMaxRetries,
  _testDownscale,
}: {
  open: boolean
  onClose: () => void
  prdId: number | null
  figmaFileKey: string | null
  onGenStart?: (ctx?: {
    figmaFileKey?: string | null
    githubRepo?: string | null
    // The chosen screen route from the locate gate (additive optional field).
    // Does NOT change buildGenerateParams or the /generate body — C3 consumes
    // the chosen screen server-side via the map. C2 only passes the UX + repo.
    chosenScreenRoute?: string | null
  }) => void
  /** Fires immediately after generate POST returns with the new prototype_id. */
  onKickoff?: (prototypeId: number) => void
  // onGenDone receives the terminal generation RESULT (DesignAgentGenResult) so
  // the parent can reveal the full-screen post-generation canvas on success. May
  // be undefined if the flow rejects before producing a result.
  onGenDone?: (result?: DesignAgentGenResult) => void
  /** Optional. When provided, renders a labeled "Cancel" control during the
   *  in-modal "locating" phase (the codebase-source screen-resolve call, up
   *  to ~90s per LOCATE_POLL_TIMEOUT_MS) — the one phase where this modal's
   *  own body is the sole visible surface. NOT rendered during "generating":
   *  that phase is already covered by the full-page GenerationLoadingScreen
   *  overlay (z-index 1000 vs this modal's 200), which supplies its own
   *  working Cancel + "Notify me when ready" once mounted. */
  onCancel?: () => void
  savedPreference?: DesignSourcePreference | null
  onSavePreference?: (pref: DesignSourcePreference) => Promise<void>
  /** Emits the current pre-build locate phase so the parent can thread it into
   *  the full-screen GenerationLoadingScreen. This version drives locate in-modal;
   *  the callback is accepted for forward-compat and is a no-op here. */
  onLocatePhase?: (phase: LocatePhaseState | null) => void
  _testConnections?: ConnectionSummary[] | null
  _testRepos?: GitHubRepo[] | null
  _testInitSource?: ModalDesignSource
  _testInitRepoSel?: string
  // Phase-state injection for node-env vitest (bypasses async effects so a
  // given phase can be rendered directly without driving the resolve call).
  _testFlowPhase?: FlowPhase
  _testLocateResult?: LocateResponse | null
  _testLocateError?: string | null
  // The resolved screen shown on the transient "matched" line in locating.
  _testMatchedRoute?: string | null
  // The optional explanatory note shown beneath the matched line.
  _testProceedNote?: string | null
  // Poll tuning overrides (tests only). Production uses the LOCATE_POLL_*
  // constants. Tests shrink the interval/timeout (and disable retry backoff
  // delay implicitly via the interval) so the loop runs without fake timers.
  _testPollIntervalMs?: number
  _testPollTimeoutMs?: number
  _testPollMaxRetries?: number
  // Image downscale override (tests only). jsdom has no real canvas/image
  // decode, so tests stub this to return a deterministic data URL. Production
  // uses the native-canvas downscaleImageToDataUrl.
  _testDownscale?: (file: File) => Promise<string>
}) {
  const { showToast } = useNavigation()

  const [platform, setPlatform] = useState<TargetPlatform>(DEFAULT_PLATFORM)
  const [designSource, setDesignSource] = useState<ModalDesignSource>(
    _testInitSource ?? "website",
  )
  const [instructions, setInstructions] = useState("")
  const [submitting, setSubmitting] = useState(false)

  // Single-modal phase machine (see FlowPhase). The modal stays mounted across
  // every phase; `config` is the resting state. Codebase generate drives it
  // through locating → (picker | unmapped-resolve) → generating.
  const [flowPhase, setFlowPhase] = useState<FlowPhase>(_testFlowPhase ?? "config")
  // The "search again" steer the PM typed on the no-match panel. Drives the
  // hint sent on a re-run locate; cleared when a flow leaves the panel.
  const [searchHint, setSearchHint] = useState("")
  // True only after a STEERED re-search (a hint was carried) still lands on the
  // recovery body with no real candidate — so we can tell the PM the steer
  // missed rather than silently re-rendering the same panel. Cleared on a hit,
  // on a generate, and whenever a new direction is typed.
  const [steerMissed, setSteerMissed] = useState(false)
  // Image-as-steer. A SECOND, optional steer on the SAME recovery
  // modal: the PM attaches a screenshot of the screen they want and locate reads
  // its on-screen text/route cues to re-rank. Co-located with searchHint because
  // it feeds the very same `enterLoadingFlow` re-search — not a parallel control.
  //   - steerImage: the client-downscaled base64 data URL (in-memory only; never
  //     persisted, never logged). null = nothing attached.
  //   - steerImageName: the filename, for the chip.
  //   - steerImageError: client-side reject message (non-image MIME / >5MB /
  //     unreadable) shown inline before any upload happens.
  //   - imageStatus / steerCues mirror the backend's honesty contract: cues only
  //     ride an `applied` status; a fall-open (ignored_*) must NEVER render cues
  //     or an "applied to the screenshot" claim. Initialised from an
  //     injected test result; otherwise set by handleLocateResult after a search
  //     and reset whenever the attachment changes (so a stale notice can't linger).
  const [steerImage, setSteerImage] = useState<string | null>(null)
  const [steerImageName, setSteerImageName] = useState<string | null>(null)
  const [steerImageError, setSteerImageError] = useState<string | null>(null)
  const [imageStatus, setImageStatus] = useState<LocateImageStatus>(
    _testLocateResult?.image_status ?? "absent",
  )
  const [steerCues, setSteerCues] = useState<string[]>(
    _testLocateResult?.image_status === "applied"
      ? _testLocateResult.read_cues ?? []
      : [],
  )
  const [locateResult, setLocateResult] = useState<LocateResponse | null>(_testLocateResult ?? null)
  const [locateError, setLocateError] = useState<string | null>(_testLocateError ?? null)
  // The screen the resolve call matched, shown on the transient "matched" line
  // as the flow transitions locating → generating.
  const [matchedRoute, setMatchedRoute] = useState<string | null>(_testMatchedRoute ?? null)
  // Optional explanatory note (a lower-confidence proceed note), shown as
  // subtext beneath the matched line.
  const [proceedNote, setProceedNote] = useState<string | null>(_testProceedNote ?? null)

  // Re-entry guard. Each resolve call is an independent model sample, so
  // re-firing it can flip a genuinely sub-threshold (ambiguous) match into an
  // auto-proceed by pure sampling variance — silently defeating the wrong-screen
  // guard. These refs enforce EXACTLY ONE resolve call per loading-flow entry:
  //   - locateInFlightRef is true from the moment a flow enters loading until it
  //     resolves (or errors), so a second enterLoadingFlow() is a no-op.
  //   - flowTokenRef is bumped on each entry; the resolve continuation checks its
  //     captured token against the current one and ignores stale results (a
  //     superseded flow can never write phase/result state for a newer one).
  const locateInFlightRef = useRef(false)
  const flowTokenRef = useRef(0)
  // Hidden file input for the image-as-steer attach control.
  const steerImageInputRef = useRef<HTMLInputElement | null>(null)

  // Screenshot-as-context (the fourth design source). Per-run only — nothing
  // here is ever written to DesignSourcePreference.
  //   - screenshotKey: staged storage key from POST /uploads/screenshot; null
  //     until an upload has SUCCEEDED. This is the Generate gate in screenshot
  //     mode (and a fresh pick nulls it immediately, so the gate holds while a
  //     replacement uploads).
  //   - screenshotPreview: the downscaled data URL shown as the thumbnail.
  //   - screenshotName: the picked filename, for the inline label.
  //   - screenshotUploading: in-flight flag (disables the picker).
  //   - screenshotError: client-side reject or server 4xx message (verbatim).
  const [screenshotKey, setScreenshotKey] = useState<string | null>(null)
  const [screenshotPreview, setScreenshotPreview] = useState<string | null>(null)
  const [screenshotName, setScreenshotName] = useState<string | null>(null)
  const [screenshotUploading, setScreenshotUploading] = useState(false)
  const [screenshotError, setScreenshotError] = useState<string | null>(null)
  // Hidden file input for the screenshot-source picker.
  const screenshotInputRef = useRef<HTMLInputElement | null>(null)

  // Closing the modal discards any staged screenshot: the key is per-run
  // context, not a preference — the next open starts clean. (The now-orphaned
  // server-side upload is the parked-cleanup class; nothing to do here.)
  useEffect(() => {
    if (!open) {
      setScreenshotKey(null)
      setScreenshotPreview(null)
      setScreenshotName(null)
      setScreenshotUploading(false)
      setScreenshotError(null)
    }
  }, [open])

  // One-shot gate for the saved-preference AUTO-SKIP effect. The effect
  // re-runs on every dep churn (connections / repos / savedPreference identity
  // changes from context re-renders), so without a latch a locate FAILURE — which
  // clears locateInFlightRef and leaves flowPhase==="error" — would let the effect
  // re-enter enterLoadingFlow() (its guard allows re-entry from "error" for the
  // Retry button) and re-POST in a storm: the surface thrashes config-auto-skip →
  // locating → error → locating … which on the live /prototype tab reads as a
  // blank/unstable screen and hammers the failing endpoint. This latch makes the
  // AUTO-SKIP fire the loading flow AT MOST ONCE per open; only the explicit Retry
  // button may re-run locate after a failure. Reset when the modal re-opens.
  const autoSkipFiredRef = useRef(false)

  // Holds the function that re-runs the most recent locate from scratch (the
  // POST), so the explicit error phase's Retry button can re-fire it. Set when
  // a flow enters; invoked by the Retry affordance.
  const locateRetryRef = useRef<(() => void) | null>(null)
  // Flipped on unmount / modal-close so an in-flight poll loop aborts: the loop
  // checks it between polls and bails without setState. Prevents leaked
  // intervals and setState-after-unmount.
  const pollAbortedRef = useRef(false)

  // Abort any in-flight poll loop on unmount so it cannot setState after the
  // modal is gone or leak a pending timer.
  useEffect(() => {
    return () => {
      pollAbortedRef.current = true
      flowTokenRef.current++
    }
  }, [])

  // When the modal closes, abort the in-flight poll too (the component may stay
  // mounted but hidden). Re-arm when it re-opens.
  useEffect(() => {
    if (!open) {
      pollAbortedRef.current = true
      flowTokenRef.current++
      locateInFlightRef.current = false
      // Re-arm the auto-skip one-shot so a fresh open evaluates the saved
      // preference again (a closed-then-reopened modal is a new flow).
      autoSkipFiredRef.current = false
    } else {
      pollAbortedRef.current = false
    }
  }, [open])

  // Figma URL paste state — URL input, extracted key, extracted node-id,
  // resolved label, and validating flag. When figmaUrlKey is set it is
  // preferred over figmaFileSel (the dropdown) as the figma_file_key for
  // generation. figmaNodeId carries the node-id from the URL query string so
  // generation can target that specific frame.
  const [figmaUrlInput, setFigmaUrlInput] = useState("")
  const [figmaUrlKey, setFigmaUrlKey] = useState<string | null>(null)
  const [figmaNodeId, setFigmaNodeId] = useState<string | null>(null)
  const [figmaUrlLabel, setFigmaUrlLabel] = useState<string | null>(null)
  const [figmaUrlValidating, setFigmaUrlValidating] = useState(false)
  const figmaDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  /**
   * Extract the Figma file key and optional node-id from a pasted design/file
   * URL. node-id is encoded with hyphens in the URL but the Figma API expects
   * colons; this converts automatically.
   */
  function extractFigmaKey(url: string): { key: string; nodeId: string | null } | null {
    const m = url.match(/(?:file|design)\/([A-Za-z0-9]+)/)
    if (!m) return null
    const key = m[1]
    // Extract node-id from query string; convert hyphen → colon (Figma URL
    // encodes as "-", API expects ":").
    const nodeMatch = url.match(/[?&]node-id=([A-Za-z0-9%:-]+)/)
    const nodeId = nodeMatch ? decodeURIComponent(nodeMatch[1]).replace(/-/g, ":") : null
    return { key, nodeId }
  }

  /** Walk a Figma document tree to find a node name by id. Returns null when
   *  not found (caller falls back to showing the file name alone). */
  function findNodeName(doc: unknown, targetId: string): string | null {
    if (!doc || typeof doc !== "object") return null
    const node = doc as Record<string, unknown>
    if (node["id"] === targetId && typeof node["name"] === "string") return node["name"]
    const children = node["children"]
    if (Array.isArray(children)) {
      for (const child of children) {
        const found = findNodeName(child, targetId)
        if (found) return found
      }
    }
    return null
  }

  /**
   * Fired on every change to the Figma URL input. Parses the key and node-id
   * immediately; if a key is found, hits GET /v1/connectors/figma/files/{key}
   * to resolve the file name (and frame name when a node-id is present) as a
   * confirmation label. Falls back to showing the raw key on error so the user
   * at least sees _something_ was parsed.
   */
  function handleFigmaUrlChange(raw: string) {
    setFigmaUrlInput(raw)
    const parsed = extractFigmaKey(raw)
    if (!parsed) {
      setFigmaUrlKey(null)
      setFigmaNodeId(null)
      setFigmaUrlLabel(null)
      return
    }
    const { key, nodeId } = parsed
    setFigmaUrlKey(key)
    setFigmaNodeId(nodeId)
    setFigmaUrlLabel(null)
    if (figmaDebounceRef.current) clearTimeout(figmaDebounceRef.current)
    figmaDebounceRef.current = setTimeout(async () => {
      setFigmaUrlValidating(true)
      try {
        const file = await connectorsApi.getFigmaFile(key)
        const fileName = file && typeof file === "object" && "name" in file
          ? String((file as { name: string }).name)
          : null
        // When a node-id is present, try to surface the frame name alongside
        // the file name ("✓ MyFile · MyFrame"). Falls back to the file name
        // alone when the node is not found in the returned document tree.
        let label = fileName ?? key
        if (nodeId && file && typeof file === "object" && "document" in file) {
          const frameName = findNodeName((file as { document: unknown }).document, nodeId)
          if (frameName) label = `${label} · ${frameName}`
        }
        setFigmaUrlLabel(label)
      } catch {
        setFigmaUrlLabel(key)
      }
      setFigmaUrlValidating(false)
    }, 500)
  }

  // Real connector status — figma + github rows derive connected vs not from
  // connectorsApi.list() (same source AppShell uses for connectedConnectorIds).
  const [connections, setConnections] = useState<ConnectionSummary[] | null>(
    _testConnections !== undefined ? _testConnections : null,
  )
  const connFor = (provider: string): ConnectionSummary | undefined =>
    connections?.find((c) => c.provider === provider)

  // Per-provider source selectors.
  // GitHub: real endpoint — connectorsApi.listGithubRepos() → GET
  //   /v1/connectors/github/repos. We fetch + populate the repo <select>.
  const [repos, setRepos] = useState<GitHubRepo[] | null>(
    _testRepos !== undefined ? _testRepos : null,
  )
  const [reposError, setReposError] = useState(false)
  const [repoSel, setRepoSel] = useState(_testInitRepoSel ?? "")

  useEffect(() => {
    if (!open) return
    // Skip the real fetch when test connections are injected directly.
    if (_testConnections !== undefined) return
    let cancelled = false
    void withAuthRetry(() => connectorsApi.list())
      .then((r) => {
        if (!cancelled) setConnections(r.connections)
      })
      .catch((err) => {
        // A 401 here is a token-refresh race, not a real disconnect —
        // withAuthRetry already retried once. If it still 401s, hold the
        // last-known rows so the modal doesn't reflow and the Generate button
        // doesn't move. Only a genuine non-auth failure clears to "Not
        // connected".
        if (!cancelled && !(err instanceof ApiError && err.status === 401)) {
          setConnections([])
        }
      })
    return () => {
      cancelled = true
    }
  }, [open])

  // Fetch repos the Sprntly App can access for this company — uses the
  // App installation token via /v1/connectors/github/accessible-repos so
  // we list exactly what was granted at App-install time. The old
  // /github/repos endpoint went via the OAuth user token + read:user
  // scope which couldn't enumerate private repos and returned empty for
  // users with no public repos under their login (e.g. service accounts
  // like @sprntlyai). Runs only when GitHub is active.
  const githubActive = getGenerateConnectorRowState(connFor("github")).connected
  useEffect(() => {
    if (!open || !githubActive) return
    // Skip the real fetch when test repos are injected directly.
    if (_testRepos !== undefined) return
    let cancelled = false
    setReposError(false)
    void withAuthRetry(() => connectorsApi.listAccessibleGithubRepos())
      .then((r) => {
        if (!cancelled) setRepos(r.repositories)
      })
      .catch((err) => {
        // Same token-refresh race: a transient 401 holds the last-known repo
        // list (no "Couldn't load repos"). Only a genuine non-auth failure
        // surfaces the error state.
        if (!cancelled && !(err instanceof ApiError && err.status === 401)) {
          setRepos([])
          setReposError(true)
        }
      })
    return () => {
      cancelled = true
    }
  }, [open, githubActive])

  const figmaActive = getGenerateConnectorRowState(connFor("figma")).connected

  // Auto-generate effect: fires when open=true and connector data is loaded.
  // When the saved preference's source is healthy, fires generation immediately
  // and closes the modal without user interaction.
  useEffect(() => {
    if (!open) return
    if (!savedPreference) return
    // One-shot: once this open's auto-skip has fired a flow, dep churn must not
    // re-trigger it. Without this, a locate FAILURE (which leaves flowPhase
    // ==="error" and clears locateInFlightRef) lets a re-run re-enter the loading
    // flow and re-POST in a storm — the blank/unstable-surface bug. Only
    // the explicit Retry button re-runs locate after a failure.
    if (autoSkipFiredRef.current) return
    if (connections === null) return
    const src = savedPreference.design_source
    if (src === "github" && repos === null) return

    const figmaHealthy = src === "figma" && figmaActive && !!savedPreference.figma_file_key
    const githubHealthy =
      src === "github" &&
      githubActive &&
      !!savedPreference.github_repo &&
      !!repos?.find((r) => r.full_name === savedPreference.github_repo)
    const websiteHealthy = src === "website"

    if (!figmaHealthy && !githubHealthy && !websiteHealthy) {
      return
    }

    setDesignSource(src)
    if (src === "figma" && savedPreference.figma_file_key) {
      setFigmaUrlKey(savedPreference.figma_file_key)
    }
    if (src === "github" && savedPreference.github_repo) {
      setRepoSel(savedPreference.github_repo)
    }

    // Latch the one-shot now that we are committed to acting on the saved
    // preference. Set BEFORE the fire so any subsequent dep-churn re-run is a
    // no-op (the storm guard), and so a locate failure can never be auto-retried
    // by the effect — only the explicit Retry button re-enters.
    autoSkipFiredRef.current = true

    if (prdId == null) {
      // Defense-in-depth: the render guard suppresses the config form when the
      // saved preference is healthy, so a missing prdId here would otherwise
      // leave the modal in a permanent null-rendering config-auto-skip state — a
      // blank screen with no error or Retry. Surface the explicit error phase
      // instead so the never-blank guarantee holds even on this edge.
      setLocateError("Couldn't start generation — no PRD is selected")
      setFlowPhase("error")
      return
    }

    // GitHub auto-skip MUST go through the SAME guarded loading flow the manual
    // Generate click uses — NOT its own bare locate-then-generate that closes
    // the modal first. Funnelling through enterLoadingFlow() means:
    //   - the loading UI (locating phase) mounts immediately instead of the
    //     modal closing to nothing while the resolve call runs (the dead-air
    //     bug), and
    //   - the re-entry guard applies here too: this effect re-runs whenever its
    //     deps (connections / repos) settle, but enterLoadingFlow() fires the
    //     resolve call at most once per flow, so dep churn cannot re-sample a
    //     sub-threshold match into an auto-proceed.
    // The repo is passed explicitly because setRepoSel() above has not yet
    // re-rendered this closure.
    if (src === "github") {
      const repo = savedPreference.github_repo!
      enterLoadingFlow({ repo })
      return
    }

    // Figma + Website auto-skip: unchanged — no locate, generate directly.
    onClose()
    setTimeout(() => {
      onGenStart?.({
        figmaFileKey: src === "figma" ? savedPreference.figma_file_key ?? null : null,
        githubRepo: null,
      })
      const baseParams = buildGenerateParams({
        prdId,
        platform,
        instructions,
        figmaFileKey: src === "figma" ? savedPreference.figma_file_key ?? null : null,
        figmaNodeId: null,
        websiteUrl: "",
        manualColor: "",
        manualFont: "",
        githubRepo: "",
        designSource: src,
      })
      void runGenerateFlow({
        params: baseParams,
        generate: designAgentApi.generate,
        runGeneration: runDesignAgentGeneration,
        onOpenChange: () => {},
        showToast,
        setSubmitting,
        notifyOnReady: false,
        notifyOnKickoff: false,
        onKickoff,
        onGenerated: (result) => onGenDone?.(result),
      }).catch(() => { onGenDone?.() })
    }, 0)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, connections, repos, savedPreference])

  // When connector data loads and there's no saved preference, default the
  // source selection to the first healthy source: github → figma → website.
  useEffect(() => {
    if (connections === null) return
    if (savedPreference) return
    if (_testInitSource !== undefined) return
    // Re-derive health from the loaded connection state
    const fActive = getGenerateConnectorRowState(connections.find((c) => c.provider === "figma")).connected
    const gActive = getGenerateConnectorRowState(connections.find((c) => c.provider === "github")).connected
    const healthy: "figma" | "github" | "website" = gActive ? "github" : fActive ? "figma" : "website"
    setDesignSource(healthy)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connections, savedPreference])

  // 'From our codebase' = github source with the locate gate enabled.
  // design_source stays 'github' on the wire — no backend enum change in this
  // ticket; locate is keyed off the repo, not a new enum value. The locate gate
  // sits in front of handleGenerate for this mode only; all other source paths
  // are untouched.
  const codebaseMode = designSource === "github" && githubActive

  if (!open) return null

  // Suppress the config form whenever a saved preference is healthy (the
  // auto-skip effect is about to fire) or while connector data hasn't loaded
  // yet (so the form never flashes for a frame before the effect acts).
  //
  // Once we are past "config" (in any loading phase) the modal must always
  // render its phase UI — suppress only applies in the config phase.
  //
  // Health checks mirror the auto-skip effect exactly.
  //
  // CRITICAL: this suppression is ONLY for the INITIAL pre-auto-skip
  // flash window. Once the auto-skip has fired (autoSkipFiredRef.current), any
  // return to flowPhase==="config" is an EXPLICIT user navigation — the "Switch
  // source" / Retry-fallback buttons set flowPhase back to "config" after a
  // locate failure (or an ambiguous / unmapped result). With a healthy saved
  // github/figma/website preference still set, the guard below would return null
  // and BLANK the in-tab /prototype surface (the tester's repro:
  // failure → error/Retry → Switch source → blank). So we only suppress while
  // the one-shot has NOT yet fired; afterwards the config/source-picker form
  // renders so the user can pick a different source. The effect's own
  // autoSkipFiredRef latch (set ~line 500) guarantees this does not re-arm an
  // auto-skip storm — only the explicit Retry button re-runs locate.
  if (savedPreference && flowPhase === "config" && !autoSkipFiredRef.current) {
    const src = savedPreference.design_source

    if (src === "github") {
      // Github auto-skip routes through enterLoadingFlow (no modal close —
      // the loading UI IS the point). Suppress the config form while:
      //   1. connector / repo data hasn't loaded yet (can't decide), OR
      //   2. the saved preference is healthy (effect will fire loading flow).
      // An unhealthy pref (repo missing from list) falls through so the user
      // sees the form as a recovery path.
      //
      // CRITICAL: `repos === null` only counts as "still loading" when github
      // is ACTUALLY connected (githubActive) — the repos fetch effect is gated
      // on githubActive, so when github is NOT connected the repos fetch NEVER
      // runs and `repos` stays null forever. Without the `githubActive &&`
      // guard here, a saved github preference whose connector is disconnected
      // (e.g. the saved pref outlived the connection) would make this branch
      // `return null` PERMANENTLY: the empty state is gone, the auto-skip effect
      // also waits forever on `repos !== null` (and never fires, since an
      // unhealthy pref must not auto-skip), so NOTHING ever replaces it — a
      // fully blank /prototype surface with no locate call and no error. Gating
      // the repos-pending check on githubActive lets the form render as the
      // recovery path (the "Connect a codebase / switch source" affordance)
      // exactly as the unhealthy-repo case already does.
      const dataStillLoading =
        connections === null || (githubActive && repos === null)
      const githubHealthy =
        githubActive &&
        !!savedPreference.github_repo &&
        !!repos?.find((r) => r.full_name === savedPreference.github_repo)
      if (dataStillLoading || githubHealthy) return null
    } else {
      const dataStillLoading = connections === null

      const figmaHealthy = src === "figma" && figmaActive && !!savedPreference.figma_file_key
      const websiteHealthy = src === "website"
      const prefHealthy = figmaHealthy || websiteHealthy

      // While connector data is still loading we cannot decide, so suppress to
      // avoid a form flash; once loaded, suppress only if the saved source is
      // healthy (the effect will close + generate). An unhealthy saved pref
      // falls through and the form renders as a fallback.
      const pendingAutoSkip = dataStillLoading || prefHealthy
      if (pendingAutoSkip) return null
    }
  }

  // Figma + GitHub row state (connected vs not + account label) from the shared
  // row helper applied to each provider's live connection.
  const figmaRow = getGenerateConnectorRowState(connFor("figma"))
  const githubRow = getGenerateConnectorRowState(connFor("github"))

  // Kick off the generate flow with an optional chosen screen route.
  // In codebase mode the chosen route + the locate snapshot SHA are also
  // threaded into the /generate body so the backend can resolve them into a
  // LocatedScreen and feed the recreate pre-seed branch of generate_prototype.
  // The SHA is only sent when non-empty (unmapped → omit; the backend has no
  // snapshot to pin against). Non-codebase paths never carry either key.
  //
  // forCodebase forces the codebase wiring on even when designSource/githubActive
  // have not yet re-rendered this closure (the auto-skip path sets them via
  // setState in the same tick). The manual path omits it and the live
  // designSource/githubActive decide.
  function runGenerateForRoute(
    chosenRoute: string | null,
    overrideSha?: string | null,
    chosenId?: string | null,
    // Repo override for the saved-preference auto-skip path, where setRepoSel()
    // has not yet re-rendered the closure when generation fires. The manual
    // path omits it and falls back to the settled repoSel — identical behaviour.
    repoOverride?: string,
    forCodebase?: boolean,
  ) {
    if (prdId == null) return
    const codebaseGenerate = forCodebase || (designSource === "github" && githubActive)
    const effectiveRepo = repoOverride ?? repoSel
    // When the codebase path forces generation on (auto-skip / picker pick), the
    // live designSource may not have re-rendered from setDesignSource("github")
    // yet — pin the wire source to github so the body never goes out as the
    // stale default. The manual path leaves designSource as the live selection.
    const effectiveSource = forCodebase ? "github" : designSource
    // A real run is being kicked off — move to the generating phase so the modal
    // shows the handoff state, not the form or the picker.
    setFlowPhase("generating")
    // Auto-proceed path passes the SHA explicitly because it fires before the
    // setLocateResult re-render lands; the picker path reads from locateResult
    // state which is already populated by the time onChoose fires.
    const retainedSha =
      (codebaseGenerate
        ? (overrideSha ?? locateResult?.commit_sha)
        : null) || null
    onGenStart?.({
      figmaFileKey: effectiveSource === "figma" ? (figmaUrlKey || figmaFileKey) : null,
      githubRepo: codebaseGenerate ? effectiveRepo : null,
      chosenScreenRoute: chosenRoute,
    })
    const baseParams = buildGenerateParams({
      prdId,
      platform,
      instructions,
      // Only send the chosen source's specific input; the other is cleared to
      // null so the backend receives a clean single-source request.
      figmaFileKey: effectiveSource === "figma" ? (figmaUrlKey || figmaFileKey) : null,
      // figmaNodeId only applies when Figma is the chosen source AND a URL was pasted.
      figmaNodeId: effectiveSource === "figma" && figmaUrlKey ? figmaNodeId : null,
      websiteUrl: "",
      manualColor: "",
      manualFont: "",
      githubRepo: codebaseGenerate ? effectiveRepo : "",
      designSource: effectiveSource,
      // The staged upload key rides ONLY the screenshot source; every other
      // source omits the field entirely (buildGenerateParams drops a null —
      // wire back-compat, byte-identical to today).
      screenshotKey: effectiveSource === "screenshot" ? screenshotKey : null,
    })
    // Fire the recreate wiring when EITHER a route or a stable id was chosen —
    // a non-route host (the app shell, an in-page section) has an empty route,
    // so id is the only signal present for it. chosen_screen_route still travels
    // for back-compat + as the human label / cache pin; chosen_screen_id is the
    // resolution key the backend prefers.
    const params =
      codebaseGenerate && (chosenRoute || chosenId)
        ? {
            ...baseParams,
            chosen_screen_route: chosenRoute,
            ...(chosenId ? { chosen_screen_id: chosenId } : {}),
            ...(retainedSha ? { map_commit_sha: retainedSha } : {}),
          }
        : codebaseGenerate && retainedSha
        ? {
            // No screen chosen, but a successful locate gives us a snapshot SHA:
            // send it (with no chosen_screen_*) so the backend builds the repo
            // map (cache hit on the pinned snapshot) and the shell-grounded
            // fallback (Tier-2) seats the PRD inside the real app shell. When
            // locate was unmapped (build_map failed → no SHA), retainedSha is
            // empty and we send neither — the backend degrades to design-system
            // -only (Tier-3), since there is no shell to ground on anyway.
            ...baseParams,
            map_commit_sha: retainedSha,
          }
        : baseParams
    void runGenerateFlow({
      params,
      generate: designAgentApi.generate,
      runGeneration: runDesignAgentGeneration,
      onOpenChange: (next) => {
        if (!next) onClose()
      },
      showToast,
      setSubmitting,
      // The full-screen loading screen (shown via ApproveModal's genLoading,
      // driven by onGenStart/onGenDone) provides all generation feedback for this
      // path, so the success toasts are redundant: notifyOnReady=false suppresses
      // the "Prototype ready" success toast, notifyOnKickoff=false suppresses the
      // "Design Agent generating" kickoff toast. Failure surfacing is unchanged —
      // runGenerateFlow still toasts "Generation failed" / "Generate failed".
      notifyOnReady: false,
      notifyOnKickoff: false,
      onKickoff,
      // runGenerateFlow fires onGenerated on the terminal poll outcome (ready OR
      // failed/timeout) — that's the dismissal signal for the loading overlay.
      // Separate from the toasts above: suppressing the toasts does not touch
      // this callback. The flow's own 6-min timeout bounds it, so the overlay can
      // never hang forever. If the kickoff itself throws, onGenerated never fires;
      // the catch in runGenerateFlow toasts "Generate failed" but won't dismiss —
      // covered below by a kickoff-failure fallback. The terminal RESULT is
      // threaded through to onGenDone so ApproveModal can reveal the full-screen
      // canvas on success and skip it on failure.
      onGenerated: (result) => onGenDone?.(result),
    }).catch(() => {
      // Defensive — if the whole flow rejects (shouldn't, runGenerateFlow
      // swallows kickoff errors), still dismiss the overlay.
      onGenDone?.()
    })
  }

  // THE single shared loading-flow entry. Both the manual Generate click
  // (handleGenerate) and the saved-preference auto-skip effect funnel through
  // here so that:
  //   1. The loading SCREEN is decoupled from the resolve CALL — we move to the
  //      locating phase IMMEDIATELY (so the loading UI mounts), THEN fire the
  //      resolve call behind it. It never blocks the render. The old auto-skip
  //      path closed the modal and awaited the call (dead air); now it drives
  //      this same locating phase.
  //   2. A github prototype is NEVER generated without first resolving the
  //      chosen screen the recreate branch needs.
  //
  // RE-ENTRY GUARD (correctness, not just UX): each resolve call is an
  // independent model sample, so re-firing it can promote a genuinely
  // sub-threshold (ambiguous) match into an auto-proceed by pure variance —
  // silently defeating the wrong-screen guard. So:
  //   - locateInFlightRef ⇒ EXACTLY ONE resolve call per entry. A second call
  //     while one is pending (or after it resolved into a later phase) is a
  //     no-op. This is what stops the auto-skip effect's dep-change re-runs from
  //     re-sampling.
  //   - flowTokenRef ⇒ a stale continuation (from a superseded flow) is ignored,
  //     so it can never write phase/result state for a newer flow.
  // The FIRST ranked_confirm goes straight to the picker — never re-sample for a
  // luckier confidence.
  // Branch on a settled LocateResponse exactly as the old synchronous flow did
  // (unmapped / auto_proceed / proceed_with_note / ranked_confirm). Pulled out
  // so the POST→poll resolver and any future caller share one success path —
  // behaviour here is preserved byte-for-byte from the pre-poll version.
  // Image-as-steer: validate + downscale a selected screenshot, then
  // hold it in memory for the next "Search again". Client-side bounds (MIME +
  // size) are enforced BEFORE any work; the downscale shrinks the bytes. The
  // file never touches storage or a log — it rides only the outgoing locate body.
  async function handleSteerImageSelected(file: File) {
    setSteerImageError(null)
    if (!STEER_IMAGE_ACCEPTED_TYPES.includes(file.type as (typeof STEER_IMAGE_ACCEPTED_TYPES)[number])) {
      setSteerImageError("That's not an image — attach a PNG, JPEG, or WebP screenshot.")
      return
    }
    if (file.size > STEER_IMAGE_MAX_BYTES) {
      setSteerImageError("That screenshot is over 5 MB — attach a smaller one.")
      return
    }
    try {
      const dataUrl = await (_testDownscale ?? downscaleImageToDataUrl)(file)
      setSteerImage(dataUrl)
      setSteerImageName(file.name)
      // A freshly attached image invalidates any prior search's image feedback
      // (cues / fall-open notice) until the next "Search again" resolves.
      setImageStatus("absent")
      setSteerCues([])
      setSteerMissed(false)
    } catch {
      setSteerImageError("Couldn't read that screenshot — try another file.")
    }
  }

  // Clear the attached screenshot and any feedback derived from it. Pure local
  // reset — the in-flight/last locate result is untouched.
  function clearSteerImage() {
    setSteerImage(null)
    setSteerImageName(null)
    setSteerImageError(null)
    setImageStatus("absent")
    setSteerCues([])
  }

  // Screenshot-as-context picker. Validates client-side (same bounds as the
  // steer flow), downscales via the SAME seam (`_testDownscale` under jsdom),
  // uploads the DOWNSCALED bytes via POST /uploads/screenshot, and holds the
  // returned key for the generate body. Re-picking replaces the pending key
  // client-side; any failure returns the picker to a re-pickable, pre-upload
  // state — a server 4xx (413 oversize / 422 type) surfaces its user-readable
  // message verbatim.
  async function handleScreenshotSelected(file: File) {
    setScreenshotError(null)
    if (
      !STEER_IMAGE_ACCEPTED_TYPES.includes(
        file.type as (typeof STEER_IMAGE_ACCEPTED_TYPES)[number],
      )
    ) {
      setScreenshotError(
        "That's not an image — attach a PNG, JPEG, or WebP screenshot.",
      )
      return
    }
    if (file.size > STEER_IMAGE_MAX_BYTES) {
      setScreenshotError("That screenshot is over 5 MB — attach a smaller one.")
      return
    }
    // A fresh pick invalidates any previously staged key IMMEDIATELY, so the
    // Generate gate holds while the replacement uploads (the orphaned prior
    // upload is the server-side parked-cleanup class — nothing to do here).
    setScreenshotKey(null)
    setScreenshotUploading(true)
    try {
      const dataUrl = await (_testDownscale ?? downscaleImageToDataUrl)(file)
      setScreenshotPreview(dataUrl)
      setScreenshotName(file.name)
      const res = await designAgentApi.uploadScreenshot(dataUrlToBlob(dataUrl))
      setScreenshotKey(res.screenshot_key)
    } catch (err) {
      // Reset to the pre-upload state (re-pickable) on ANY failure.
      setScreenshotKey(null)
      setScreenshotPreview(null)
      setScreenshotName(null)
      setScreenshotError(
        err instanceof ApiError
          ? err.message
          : "Couldn't upload that screenshot — check your connection and try again.",
      )
    } finally {
      setScreenshotUploading(false)
    }
  }

  function handleLocateResult(
    result: LocateResponse,
    opts: { repo: string; hint?: string; image?: string },
  ) {
    setLocateResult(result)
    // Image-as-steer honesty: mirror the backend gate exactly.
    // Cues ride ONLY an "applied" status; a fall-open (ignored_*) or "absent"
    // surfaces no cues and no "applied to the screenshot" claim. Trust the
    // backend (read_cues is [] unless applied) AND gate the UI on the status.
    const status = (result.image_status ?? "absent") as LocateImageStatus
    setImageStatus(status)
    setSteerCues(status === "applied" ? result.read_cues ?? [] : [])

    // Route a no-match (unmapped) OR a ranked_confirm whose only candidates are
    // degenerate placeholders to the SAME recovery body. A degenerate
    // ranked_confirm must NEVER reach the picker — surfacing a placeholder as a
    // "Suggested / Use this screen" card is the wrong-screen trap.
    const realRanked = result.ranked.filter(isRealCandidate)
    if (result.unmapped) {
      // A steered re-search that still missed → tell the PM (true only when THIS
      // resolve carried a hint). The initial unmapped landing has no hint.
      setSteerMissed(!!opts.hint)
      setFlowPhase("unmapped-resolve")
      return
    }
    if (
      result.decision === "auto_proceed" ||
      result.decision === "proceed_with_note"
    ) {
      const route = result.chosen[0]?.route ?? null
      const id = result.chosen[0]?.id ?? null
      setMatchedRoute(route)
      if (result.decision === "proceed_with_note") {
        const note = result.chosen[0]?.rationale ?? null
        setProceedNote(note)
      }
      // A generate is being kicked off — the recovery body is left behind, so
      // any prior miss message must not linger.
      setSteerMissed(false)
      // Persist the source preference now that a confident match exists.
      void onSavePreference?.({
        design_source: "github",
        figma_file_key: null,
        github_repo: opts.repo || null,
        website_url: null,
      })
      // Transition locating → generating and kick off the real run. The SHA
      // is passed explicitly because the setLocateResult above has not yet
      // re-rendered this closure. forCodebase=true forces the github wiring
      // on even when the auto-skip path's setState has not re-rendered.
      runGenerateForRoute(
        route,
        result.commit_sha || null,
        id,
        opts.repo,
        true,
      )
      return
    }
    // ranked_confirm. With at least one REAL candidate → the picker (never
    // re-sample). With only degenerate placeholders → the recovery body, same
    // as a no-match, with the steered-miss feedback when this was a re-search.
    if (realRanked.length > 0) {
      setSteerMissed(false)
      setFlowPhase("picker")
    } else {
      setSteerMissed(!!opts.hint)
      setFlowPhase("unmapped-resolve")
    }
  }

  // Drive the async locate contract: POST → job id → poll until done/error,
  // capped by an overall timeout and tolerant of transient backend failures.
  //
  // Failure handling is the load-bearing change: on a terminal failure (404/400
  // job, exhausted transient budget, or timeout) the flow goes to the EXPLICIT
  // "error" phase — NOT back to "config". A failed locate no longer silently
  // collapses to the PRD form (the prod hang→collapse bug).
  async function runLocateResolve(
    opts: { repo: string; hint?: string; image?: string },
    token: number,
  ): Promise<void> {
    const intervalMs = _testPollIntervalMs ?? LOCATE_POLL_INTERVAL_MS
    const timeoutMs = _testPollTimeoutMs ?? LOCATE_POLL_TIMEOUT_MS
    const maxRetries = _testPollMaxRetries ?? LOCATE_POLL_MAX_RETRIES
    const localPrdId = prdId!
    const deadline = Date.now() + timeoutMs

    // True iff this flow was superseded (newer flow), aborted (unmount/close),
    // or the overall deadline has passed.
    const isStale = () =>
      token !== flowTokenRef.current || pollAbortedRef.current

    const sleep = (ms: number) =>
      new Promise<void>((resolve) => setTimeout(resolve, ms))

    // Move to the explicit error phase with a message + Retry affordance.
    const fail = (message: string) => {
      if (isStale()) return
      locateInFlightRef.current = false
      setLocateError(message)
      setFlowPhase("error")
    }

    try {
      // 1) POST to start the job, retrying transient (5xx/network) failures.
      //    A 404 here is terminal (feature off / PRD not owned / cross-workspace).
      let handle: LocateJobHandle | null = null
      let postAttempts = 0
      while (handle === null) {
        if (isStale()) return
        try {
          handle = await designAgentApi.locate({
            prd_id: localPrdId,
            github_repo: opts.repo,
            // Steer carried only when the PM typed a "search again" direction;
            // a plain locate sends no hint (unsteered, unchanged behaviour).
            ...(opts.hint ? { hint: opts.hint } : {}),
            // Image-as-steer: the bounded base64 data URL rides the
            // body ONLY when a screenshot is attached. With no image the key is
            // omitted entirely, so the no-image request stays byte-identical to
            // the text-only path.
            ...(opts.image ? { image: opts.image } : {}),
          })
        } catch (err) {
          if (isStale()) return
          if (!isTransientLocateError(err) || postAttempts >= maxRetries) {
            fail("Couldn't start codebase analysis — try again or switch source")
            return
          }
          postAttempts++
          await sleep(intervalMs * postAttempts)
        }
      }
      if (isStale()) return

      // A handle without a usable job id is terminal — never poll
      // `locateJob(undefined)` (the `jobs/undefined` 404 class). Fail clean.
      if (!handle.job_id) {
        fail("Couldn't start codebase analysis — try again or switch source")
        return
      }

      // 2) Poll the job until done/error/timeout. Transient poll failures are
      //    retried within the same budget; a 404/400 job is terminal.
      let transientPolls = 0
      // First poll fires immediately (no leading delay) so an already-done job
      // resolves promptly; subsequent polls wait `intervalMs`.
      let firstPoll = true
      // eslint-disable-next-line no-constant-condition
      while (true) {
        if (isStale()) return
        if (Date.now() > deadline) {
          fail("Codebase analysis timed out — try again or switch source")
          return
        }
        if (!firstPoll) await sleep(intervalMs)
        firstPoll = false
        if (isStale()) return

        let status: LocateJobStatus
        try {
          status = await designAgentApi.locateJob(handle.job_id)
        } catch (err) {
          if (isStale()) return
          if (!isTransientLocateError(err) || transientPolls >= maxRetries) {
            // 404/400 (unknown/TTL-swept/cross-workspace job) or exhausted
            // transient budget — terminal.
            fail("Couldn't analyse the codebase — try again or switch source")
            return
          }
          transientPolls++
          await sleep(intervalMs * transientPolls)
          continue
        }

        if (status.status === "running") continue
        if (status.status === "error") {
          fail(
            status.error
              ? `Codebase analysis failed — ${status.error}`
              : "Codebase analysis failed — try again or switch source",
          )
          return
        }
        // status.status === "done"
        if (isStale()) return
        if (!status.result) {
          fail("Codebase analysis returned no result — try again or switch source")
          return
        }
        locateInFlightRef.current = false
        handleLocateResult(status.result, opts)
        return
      }
    } catch {
      // Defensive catch-all — any unexpected throw still surfaces the explicit
      // error phase rather than hanging or collapsing to config.
      fail("Couldn't analyse the codebase — try again or switch source")
    }
  }

  function enterLoadingFlow(opts: { repo: string; hint?: string; image?: string }) {
    if (prdId == null) return
    // Re-entry guard: one resolve flow at a time. Covers the auto-skip effect
    // re-running on connections/repos churn and an accidental double-click.
    if (locateInFlightRef.current) return
    // Allowed entry phases: the resting config form, a retry from the error
    // phase, or a "search again" steer from EITHER recovery surface (the picker
    // and the no-match unmapped-resolve panel now share the steer). Never
    // re-enter from a live phase (locating/generating).
    if (
      flowPhase !== "config" &&
      flowPhase !== "error" &&
      flowPhase !== "unmapped-resolve" &&
      flowPhase !== "picker"
    )
      return

    const token = ++flowTokenRef.current
    locateInFlightRef.current = true
    pollAbortedRef.current = false

    // Move to locating FIRST so the loading UI mounts before the (slow) resolve
    // job runs. Reset any stale carry-over from a previous flow.
    setLocateError(null)
    setLocateResult(null)
    setMatchedRoute(null)
    setProceedNote(null)
    setFlowPhase("locating")

    // Remember how to re-run THIS flow from scratch so the error phase's Retry
    // button can re-fire the whole POST→poll sequence.
    locateRetryRef.current = () => {
      locateInFlightRef.current = false
      enterLoadingFlow(opts)
    }

    // Fire the resolve flow behind the loading UI — NOT awaited before render.
    void runLocateResolve(opts, token)
  }

  const handleGenerate = () => {
    if (submitting || prdId == null) return

    if (codebaseMode) {
      // Codebase mode: enter the shared loading flow (loading UI immediate,
      // resolve call behind it, picker only when ambiguous).
      enterLoadingFlow({ repo: repoSel })
      return
    }

    // Non-codebase path — runs as before, chosenScreenRoute is null.
    // Screenshot is per-run context, never a durable preference (locked
    // decision): the saved-preference union stays untouched, so the next open
    // restores the previous non-screenshot preference.
    if (designSource !== "screenshot") {
      void onSavePreference?.({
        design_source: designSource,
        figma_file_key: designSource === "figma" ? (figmaUrlKey || figmaFileKey || null) : null,
        github_repo: designSource === "github" ? (repoSel || null) : null,
        website_url: null,
      })
    }
    runGenerateForRoute(null)
  }

  // The ONE shared recovery body, rendered identically in BOTH the picker and
  // unmapped-resolve phases (the phase names persist; the UI is consolidated).
  // It always carries: the steer + Search again, the real candidates (pickable,
  // only when present), the Generate-from-the-PRD-anyway floor, and Switch
  // source — one consolidated action row. A degenerate placeholder never
  // reaches the pickable LocateConfirmView because we filter on isRealCandidate.
  const realRanked = locateResult
    ? locateResult.ranked.filter(isRealCandidate)
    : []
  const recoveryPanel = locateResult ? (
    <div data-testid="unmapped-resolve">
      <p className="locate-hint" data-testid="locate-unmapped">
        {realRanked.length > 0 ? (
          <>
            We found some candidate screens — pick one, or point us at a
            different screen and search again.
          </>
        ) : (
          <>
            We couldn&apos;t find a screen to anchor on in this repo. Tell us
            where to look and search again, or generate straight from the PRD —
            we&apos;ll match your app&apos;s look.
          </>
        )}
      </p>
      {/* Candidates lead on the PICKER variant. The pickable confirm view
          (Suggested + Other options) renders ABOVE the steer whenever a real
          candidate survives the isRealCandidate filter — "the picker and the
          options always appear at the top." A degenerate placeholder is filtered
          out so it never shows a "Suggested / Use this screen" card, and the
          UNMAPPED variant has no candidates at all — so there the steer leads
          instead. The SHA travels so a picked screen is Tier-1 on the right
          snapshot. */}
      {realRanked.length > 0 && (
        <LocateConfirmView
          question="Pick the closest screen:"
          candidates={mapLocateCandidates(realRanked)}
          onChoose={(route, id) =>
            runGenerateForRoute(
              route,
              locateResult.commit_sha || null,
              id,
              repoSel,
              true,
            )
          }
        />
      )}
      {/* Steer + re-search. On the UNMAPPED variant (no candidates) this is the
          sole, primary action → "Search again" is accent and leads the panel. On
          the PICKER variant the candidate list above is the primary path, so the
          steer is demoted below it and "Search again" is plain/secondary — it
          must not compete with the accent "Use this screen" cards. Blank input
          disables the button. */}
      {/* Image-as-steer. The screenshot affordance lives ONLY on the
          MAPPED variant (realRanked > 0): there ARE candidates to re-rank, so a
          screenshot can help. On the UNMAPPED variant there is no map to re-rank,
          so NONE of this renders — that path keeps the text steer
          + PRD-anyway floor only. The chip + image feedback sit above the steer
          row (mockup intent); the attach button sits inside the row. */}
      {/* Framing heading for the mapped steer grouping — names both ways to
          redirect the anchor (type a direction, or show a screenshot). MAPPED
          variant only, matching the image-control gating; the unmapped variant
          keeps its own steer copy. */}
      {realRanked.length > 0 && (
        <p
          className="locate-steer-heading"
          data-testid="locate-steer-heading"
        >
          Not the right screen? Tell us where to anchor — or show us.
        </p>
      )}
      {realRanked.length > 0 && steerImage && (
        <div className="locate-image-chip" data-testid="locate-image-chip">
          <IconImage size={16} className="locate-image-chip-icon" />
          <div className="locate-image-chip-meta">
            <span className="locate-image-name">
              {steerImageName ?? "screenshot"}
            </span>
            {/* Cues render ONLY on an "applied" status — the honest signal
                that the screenshot's text actually steered the re-rank. */}
            {imageStatus === "applied" && steerCues.length > 0 && (
              <span
                className="locate-image-cues"
                data-testid="locate-image-cues"
              >
                Read from screenshot:{" "}
                {steerCues.map((cue, i) => (
                  <code key={`${cue}-${i}`} className="locate-image-cue">
                    {cue}
                  </code>
                ))}
              </span>
            )}
          </div>
          <button
            type="button"
            className="locate-image-remove"
            data-testid="locate-image-remove"
            aria-label="Remove screenshot"
            onClick={clearSteerImage}
          >
            <IconClose size={15} />
          </button>
        </div>
      )}
      {/* No silent image drop. A fall-open (oversize / undecodable on the
          server) must NEVER claim the image was used: show an inline notice that
          we searched on text instead, and render NO cues / no "re-ranked toward
          the screenshot" copy. Gated on the mapped variant + a non-applied,
          non-absent status returned by the last search. */}
      {realRanked.length > 0 &&
        (imageStatus === "ignored_oversize" ||
          imageStatus === "ignored_decode") && (
          <p className="locate-image-notice" data-testid="locate-image-notice">
            {imageStatus === "ignored_oversize"
              ? "Screenshot too large — searched on your text instead."
              : "Couldn't read that screenshot — searched on your text instead."}
          </p>
        )}
      <div className="locate-steer" data-testid="locate-steer">
        <input
          type="text"
          className="input"
          data-testid="locate-steer-input"
          placeholder="Tell us where to anchor — e.g. 'the settings page', 'the dashboard'"
          value={searchHint}
          onChange={(e) => {
            setSearchHint(e.target.value)
            // Typing a fresh direction clears the prior miss message.
            setSteerMissed(false)
          }}
          maxLength={300}
          onKeyDown={(e) => {
            // Search fires on EITHER signal — a typed direction OR an attached
            // screenshot.
            if (e.key === "Enter" && (searchHint.trim() || steerImage)) {
              e.preventDefault()
              enterLoadingFlow({
                repo: repoSel,
                hint: searchHint.trim(),
                image: steerImage || undefined,
              })
            }
          }}
        />
        {/* Attach / replace screenshot — MAPPED variant only. Hidden file input
            driven by a visible button so the control matches the steer row's
            vocabulary. */}
        {realRanked.length > 0 && (
          <>
            <input
              ref={steerImageInputRef}
              type="file"
              accept="image/png,image/jpeg,image/webp"
              data-testid="locate-image-input"
              className="locate-image-input"
              onChange={(e) => {
                const file = e.target.files?.[0]
                if (file) void handleSteerImageSelected(file)
                // Reset so re-selecting the same file fires onChange again.
                e.target.value = ""
              }}
            />
            <button
              type="button"
              className="btn locate-image-attach"
              data-testid="locate-image-attach"
              onClick={() => steerImageInputRef.current?.click()}
            >
              <IconImage size={16} />
              {steerImage ? "Replace" : "Add screenshot"}
            </button>
          </>
        )}
        <button
          type="button"
          className={realRanked.length === 0 ? "btn btn-accent" : "btn"}
          data-testid="locate-search-again"
          disabled={!(searchHint.trim() || steerImage)}
          onClick={() =>
            enterLoadingFlow({
              repo: repoSel,
              hint: searchHint.trim(),
              image: steerImage || undefined,
            })
          }
        >
          Search again
        </button>
      </div>
      {/* Client-side reject: a non-image MIME / >5 MB / unreadable file is
          refused BEFORE any upload, with an inline message. Mapped variant only. */}
      {realRanked.length > 0 && steerImageError && (
        <p className="locate-image-error" data-testid="locate-image-error">
          {steerImageError}
        </p>
      )}
      {/* A steered re-search that still missed — say so explicitly rather than
          re-rendering the same panel silently. */}
      {steerMissed && (
        <p className="locate-hint" data-testid="locate-steer-missed">
          Still couldn&apos;t pin a screen for that — try another direction, or
          generate anyway.
        </p>
      )}
      {/* "Generate from the PRD anyway" — the PRD-only floor — renders ONLY on
          the UNMAPPED variant (no real candidates). On the picker the user has
          real screens to pick or steer toward, so the floor isn't offered there.
          A de-emphasized text link, not a button. Switch source was removed from
          this panel (close the modal to swap source); the X/close path back to
          config is unaffected. */}
      {realRanked.length === 0 && (
        <div className="locate-generate-row">
          <button
            type="button"
            className="locate-generate-link"
            data-testid="generate-anyway"
            onClick={() =>
              runGenerateForRoute(
                null,
                locateResult.commit_sha || null,
                undefined,
                repoSel,
                true,
              )
            }
          >
            Generate from the PRD anyway
          </button>
        </div>
      )}
    </div>
  ) : null

  return (
    <div
      className="modal-overlay open"
      id="modal-generate"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="modal design-agent-surface">
        {/* Compact header — title + close only. */}
        <div className="modal-head">
          <h3 className="modal-title">Generate prototype</h3>
          <button
            type="button"
            className="modal-close"
            style={{ position: "absolute", top: 18, right: 18 }}
            onClick={onClose}
            aria-label="Close"
          >
            <IconClose size={18} />
          </button>
        </div>

        <div className="modal-body">
          {/* config phase — the source/platform/instructions form. In every
              other phase the form is replaced by the phase UI below so the modal
              never shows a stale, interactive form behind a running flow. */}
          {flowPhase === "config" && (
          <>
          {/* Platform label + pills on one row (gen-inline-field): label left,
              pills inline right. */}
          <div className="field gen-inline-field">
            <label className="field-label">Platform</label>
            <div className="radio-group">
              {PLATFORM_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  className={
                    "radio-pill" + (platform === opt.value ? " selected" : "")
                  }
                  data-val={opt.value}
                  aria-pressed={platform === opt.value}
                  onClick={() => setPlatform(opt.value)}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>

          {/* Design source — single-select picker using the shared SourceTypePills
              component (same radio-pill vocabulary as the Platform selector above).
              Connector-status rows always visible so the user can connect a
              not-yet-connected provider before picking it; the source-specific
              input beneath each row is gated on the matching selection. */}
          <div className="field">
            <label className="field-label">Design source</label>
            <ModalSourcePills
              value={designSource}
              onChange={setDesignSource}
              options={MODAL_SOURCE_OPTIONS}
            />

            {/* Figma connector status — shown only when Figma is the selected
                source. Displays connected state or a connect affordance when
                not yet connected. The URL paste input is only revealed when
                Figma is the active selection AND Figma is connected. */}
            {designSource === "figma" && (
              <div className="src-row-compact">
                <span className="src-bullet" aria-hidden="true" />
                <span className="src-name">Figma</span>
                {figmaActive ? (
                  <>
                    <span className="src-connected">
                      Connected
                      {figmaRow.accountLabel ? ` · ${figmaRow.accountLabel}` : ""}
                    </span>
                  </>
                ) : (
                  <SourceConnectHint provider="figma" />
                )}
              </div>
            )}

            {/* Figma URL paste — shown only when Figma is the chosen source and
                Figma is connected. Paste any Figma design/file URL; the key is
                extracted client-side and validated against the real
                GET /v1/connectors/figma/files/{key} endpoint. */}
            {designSource === "figma" && figmaActive && (
              <div className="da-generate-figma-url">
                <label className="da-generate-label" htmlFor="figma-url-input">
                  Paste Figma file link
                </label>
                <input
                  id="figma-url-input"
                  type="url"
                  className="da-generate-input"
                  placeholder="https://www.figma.com/design/…"
                  value={figmaUrlInput}
                  onChange={(e) => void handleFigmaUrlChange(e.target.value)}
                  data-testid="figma-url-input"
                />
                {figmaUrlValidating && (
                  <span className="da-generate-hint">Checking…</span>
                )}
                {figmaUrlLabel && !figmaUrlValidating && (
                  <span className="da-generate-hint da-generate-hint--ok">
                    ✓ {figmaUrlLabel}
                  </span>
                )}
              </div>
            )}

            {/* Codebase / GitHub connector status — shown only when Codebase is
                the selected source. Displays connected state or a connect
                affordance when not yet connected. The repo selector is only
                revealed when GitHub is the chosen source and GitHub is connected. */}
            {designSource === "github" && (
              <div className="src-row-compact">
                <span className="src-bullet" aria-hidden="true" />
                <span className="src-name">Codebase</span>
                {githubActive ? (
                  <>
                    <span className="src-connected">
                      Connected
                      {githubRow.accountLabel ? ` · ${githubRow.accountLabel}` : ""}
                    </span>
                  </>
                ) : (
                  <SourceConnectHint provider="github" />
                )}
              </div>
            )}

            {/* GitHub repo selector — shown only when Codebase is the chosen
                source and GitHub is connected. */}
            {designSource === "github" && githubActive && (
              <select
                className="input src-select-inline"
                value={repoSel}
                onChange={(e) => setRepoSel(e.target.value)}
                disabled={!repos || repos.length === 0}
                aria-label="Select a repo"
              >
                {repos === null ? (
                  <option value="">Loading repos…</option>
                ) : repos.length === 0 ? (
                  <option value="">
                    {reposError
                      ? "Couldn't load repos"
                      : "No repos — install the Sprntly App on a repo"}
                  </option>
                ) : (
                  <>
                    <option value="">Pick repo…</option>
                    {[...repos].sort((a, b) => a.full_name.localeCompare(b.full_name)).map((r) => (
                      <option key={r.full_name} value={r.full_name}>
                        {r.full_name}
                      </option>
                    ))}
                  </>
                )}
              </select>
            )}

            {/* Website default — the resolved state shown when Website is the
                chosen source (the onboarding site is used automatically). */}
            {designSource === "website" && (
              <p className="src-fallback-line">
                We&apos;ll infer a style from the brand website.
              </p>
            )}

            {/* Screenshot source — per-run upload; no connector, always
                selectable. The hidden input + visible button mirror the
                image-as-steer control's vocabulary; the picked file is
                client-downscaled and uploaded on choice, and Generate stays
                disabled until the upload has succeeded. */}
            {designSource === "screenshot" && (
              <>
                <input
                  ref={screenshotInputRef}
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  data-testid="screenshot-file-input"
                  className="locate-image-input"
                  onChange={(e) => {
                    const file = e.target.files?.[0]
                    if (file) void handleScreenshotSelected(file)
                    // Reset so re-selecting the same file fires onChange again.
                    e.target.value = ""
                  }}
                />
                <div className="src-row-compact">
                  <span className="src-bullet" aria-hidden="true" />
                  <span className="src-name">Screenshot</span>
                  <button
                    type="button"
                    className="btn locate-image-attach"
                    data-testid="screenshot-pick"
                    disabled={screenshotUploading}
                    onClick={() => screenshotInputRef.current?.click()}
                  >
                    <IconImage size={16} />
                    {screenshotPreview ? "Replace image" : "Choose image"}
                  </button>
                  {screenshotUploading && (
                    <span
                      className="da-generate-hint"
                      data-testid="screenshot-uploading"
                    >
                      Uploading…
                    </span>
                  )}
                </div>
                {screenshotPreview && (
                  // The downscaled data URL doubles as the preview thumbnail —
                  // in-memory only, exactly the bytes that were uploaded.
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={screenshotPreview}
                    alt={
                      screenshotName
                        ? `Screenshot preview — ${screenshotName}`
                        : "Screenshot preview"
                    }
                    data-testid="screenshot-preview"
                    style={{
                      display: "block",
                      maxWidth: "100%",
                      maxHeight: 140,
                      marginTop: 8,
                      borderRadius: 6,
                      border: "1px solid var(--border, rgba(128,128,128,.35))",
                    }}
                  />
                )}
                {screenshotPreview && screenshotName && !screenshotUploading && (
                  <span
                    className="da-generate-hint da-generate-hint--ok"
                    data-testid="screenshot-ready-hint"
                  >
                    {screenshotKey ? `✓ ${screenshotName}` : screenshotName}
                  </span>
                )}
                {screenshotError && (
                  <p
                    className="locate-image-error"
                    data-testid="screenshot-error"
                    role="alert"
                  >
                    {screenshotError}
                  </p>
                )}
                {!screenshotPreview &&
                  !screenshotError &&
                  !screenshotUploading && (
                    <p className="src-fallback-line">
                      Upload a screenshot of a design you like — we&apos;ll
                      match its look.
                    </p>
                  )}
              </>
            )}
          </div>

          {/* Instructions below source; compact two-row textarea. */}
          <div className="field">
            <label className="field-label" htmlFor="gen-instructions">
              Instructions (optional)
            </label>
            <textarea
              id="gen-instructions"
              className="textarea textarea-compact"
              value={instructions}
              onChange={(e) => setInstructions(e.target.value)}
              placeholder={'e.g. "Lean into the dark theme, emphasise the primary CTA"'}
              rows={2}
            />
          </div>

          </>
          )}

          {/* locating / generating phases — loading UI is visible immediately on
              generate-click while the (slow) resolve call runs behind it. Once a
              screen resolves, the transient "matched" line shows as the flow
              transitions into generation. */}
          {(flowPhase === "locating" || flowPhase === "generating") && (
            <GenerateLoadingState
              matchedRoute={matchedRoute}
              note={proceedNote}
            />
          )}

          {flowPhase === "locating" && onCancel && (
            <div className="proto-gen-footer">
              <GenerationCancelButton onCancel={onCancel} />
            </div>
          )}

          {/* error phase — the resolve job failed or timed out. An EXPLICIT
              error message + Retry on the loading surface. Critically this is a
              terminal phase of its OWN, not a fall-through to config: a failed
              locate must never silently collapse back to the PRD form. Retry
              re-runs the whole POST→poll sequence; Switch source returns to the
              form deliberately (a user choice, not a silent collapse). */}
          {flowPhase === "error" && (
            <div
              className={`locate-error-state ${locateErrorStyles.state}`}
              data-testid="locate-error-state"
            >
              <p
                className="locate-error"
                data-testid="locate-error"
                role="alert"
              >
                {locateError ??
                  "Couldn't analyse the codebase — try again or switch source"}
              </p>
              <div className={`locate-error-actions ${locateErrorStyles.actions}`}>
                {/* Codebase-only escape hatch: when locate fails for a github
                    source the user can still generate straight from the PRD (no
                    screen anchor). Gated on codebaseMode so figma/website error
                    UX is unchanged. */}
                {codebaseMode && (
                  <button
                    type="button"
                    className="btn btn-accent"
                    data-testid="generate-anyway"
                    onClick={() => runGenerateForRoute(null, undefined, undefined, undefined, true)}
                  >
                    Generate from the PRD anyway
                  </button>
                )}
                <button
                  type="button"
                  className="btn btn-accent"
                  data-testid="locate-retry"
                  onClick={() => {
                    // Re-run the full locate from the POST. Falls back to a clean
                    // config return only if no retry was ever armed (shouldn't
                    // happen — the error phase is only reachable from a flow).
                    if (locateRetryRef.current) {
                      locateRetryRef.current()
                    } else {
                      locateInFlightRef.current = false
                      flowTokenRef.current++
                      setLocateError(null)
                      setFlowPhase("config")
                    }
                  }}
                >
                  Retry
                </button>
                <button
                  type="button"
                  className="btn"
                  data-testid="locate-error-switch-source"
                  onClick={() => {
                    locateInFlightRef.current = false
                    pollAbortedRef.current = true
                    flowTokenRef.current++
                    setLocateError(null)
                    setLocateResult(null)
                    setFlowPhase("config")
                  }}
                >
                  Switch source
                </button>
              </div>
            </div>
          )}

          {/* picker + unmapped-resolve — one consolidated recovery body. Both
              phase names persist (the routing in handleLocateResult decides
              which); the rendered UI is identical. The picker phase is reached
              only with at least one REAL candidate; unmapped-resolve covers the
              no-match and degenerate-only cases. The shared body always offers
              the steer + Search again, the Generate-from-the-PRD-anyway floor,
              and Switch source; the pickable confirm view appears only when real
              candidates survive the isRealCandidate filter. */}
          {(flowPhase === "picker" || flowPhase === "unmapped-resolve") &&
            recoveryPanel}
        </div>

        {/* The action footer belongs to the config phase only — once a flow is
            running there is nothing to submit; the phase UI carries any action. */}
        {flowPhase === "config" && (
        <div className="modal-foot">
          <span
            style={{ fontSize: 11.5, color: "var(--muted)" }}
            data-testid={codebaseMode && !repoSel ? "codebase-no-repo-helper" : undefined}
          >
            {codebaseMode && !repoSel
              ? "Connect Figma or a codebase to generate"
              : "Generation is asynchronous — get notified when it’s ready."}
          </span>
          <div className="modal-foot-r">
            <button
              type="button"
              className="btn"
              onClick={onClose}
              disabled={submitting}
            >
              Cancel
            </button>
            <button
              type="button"
              className="btn btn-accent"
              data-testid="generate-btn"
              onClick={handleGenerate}
              disabled={
                submitting ||
                prdId == null ||
                (codebaseMode && !repoSel) ||
                // Screenshot mode gates on a SUCCEEDED upload: the staged key
                // is nulled on pick and set only when the upload resolves, so
                // this also covers the in-flight window.
                (designSource === "screenshot" && !screenshotKey)
              }
            >
              {submitting ? "Generating…" : "Generate →"}
            </button>
          </div>
        </div>
        )}
      </div>
    </div>
  )
}
