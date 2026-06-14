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

const PLATFORM_OPTIONS: { value: TargetPlatform; label: string }[] = [
  { value: "desktop", label: "Desktop" },
  { value: "mobile", label: "Mobile" },
  { value: "both", label: "Both" },
]

/**
 * Single-modal phase machine for the generate-entry flow.
 *
 *   config            → the source/platform/instructions form (the resting state)
 *   locating          → loading UI is visible while the screen-resolve call runs
 *   picker            → an ambiguous match needs the user to pick a screen
 *   unmapped-resolve  → no match; pick a screen or switch back to config
 *   generating        → a real run exists; hand off to the loading screen + drawer
 *
 * The modal stays MOUNTED across every phase and only hands off (onGenStart /
 * onKickoff / onGenDone) once a real prototype run has been kicked off. The key
 * fix this encodes: the loading SCREEN is decoupled from the resolve CALL —
 * `locating` mounts immediately on generate-click, and the resolve call fires
 * behind it, so the user never stares at a frozen form.
 */
type FlowPhase =
  | "config"
  | "locating"
  | "picker"
  | "unmapped-resolve"
  | "generating"

/** Maps LocateCandidate[] to the shape LocateConfirmView expects. */
export function mapLocateCandidates(ranked: LocateCandidate[]): LocateConfirmCandidate[] {
  return ranked.map((c, i) => ({
    id: c.id,
    route: c.route,
    entry_component: c.entry_component,
    component_count: c.component_count,
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
  // Persisted design source preference. When set and the named source is
  // healthy (connected + key/repo valid), the modal fires generation immediately
  // without user interaction and closes itself. Pass null to always show.
  savedPreference,
  onSavePreference,
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
  savedPreference?: DesignSourcePreference | null
  onSavePreference?: (pref: DesignSourcePreference) => Promise<void>
  _testConnections?: ConnectionSummary[] | null
  _testRepos?: GitHubRepo[] | null
  _testInitSource?: "figma" | "github" | "website"
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
}) {
  const { showToast } = useNavigation()

  const [platform, setPlatform] = useState<TargetPlatform>(DEFAULT_PLATFORM)
  const [designSource, setDesignSource] = useState<"figma" | "github" | "website">(
    _testInitSource ?? "website",
  )
  const [instructions, setInstructions] = useState("")
  const [submitting, setSubmitting] = useState(false)

  // Single-modal phase machine (see FlowPhase). The modal stays mounted across
  // every phase; `config` is the resting state. Codebase generate drives it
  // through locating → (picker | unmapped-resolve) → generating.
  const [flowPhase, setFlowPhase] = useState<FlowPhase>(_testFlowPhase ?? "config")
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

    if (prdId == null) return

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

  // Suppress the modal only for the figma/website auto-skip paths, which still
  // close the modal and generate directly (no resolve call). For those, showing
  // the form for a frame before the effect closes it would flash.
  //
  // The github saved-preference path is the redesign's whole point: it NO LONGER
  // suppresses. Instead it stays open and drives the locating phase immediately
  // (the loading UI is the point), so there is nothing to hide. Once we are in
  // any loading phase (past config) the modal must always render its phase UI.
  //
  // Health checks mirror the auto-skip effect.
  if (savedPreference && flowPhase === "config") {
    const src = savedPreference.design_source

    // github never suppresses now — it renders the loading phase in-modal.
    if (src !== "github") {
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
  function enterLoadingFlow(opts: { repo: string }) {
    if (prdId == null) return
    // Re-entry guard: one resolve call per flow. Covers the auto-skip effect
    // re-running on connections/repos churn and an accidental double-click.
    if (locateInFlightRef.current) return
    if (flowPhase !== "config") return

    const localPrdId = prdId
    const token = ++flowTokenRef.current
    locateInFlightRef.current = true

    // Move to locating FIRST so the loading UI mounts before the (slow) resolve
    // call runs. Reset any stale carry-over from a previous flow.
    setLocateError(null)
    setLocateResult(null)
    setMatchedRoute(null)
    setProceedNote(null)
    setFlowPhase("locating")

    // Fire the resolve call behind the loading UI — NOT awaited before render.
    void (async () => {
      try {
        const result = await designAgentApi.locate({
          prd_id: localPrdId,
          github_repo: opts.repo,
        })
        // Ignore a stale result from a superseded flow.
        if (token !== flowTokenRef.current) return
        locateInFlightRef.current = false
        setLocateResult(result)

        if (result.unmapped) {
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
        // FIRST ranked_confirm → straight to the picker. Never re-sample.
        setFlowPhase("picker")
      } catch {
        if (token !== flowTokenRef.current) return
        locateInFlightRef.current = false
        setLocateError(
          "Couldn't analyse the codebase — pick a screen or switch source",
        )
        setFlowPhase("config")
      }
    })()
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
    void onSavePreference?.({
      design_source: designSource,
      figma_file_key: designSource === "figma" ? (figmaUrlKey || figmaFileKey || null) : null,
      github_repo: designSource === "github" ? (repoSel || null) : null,
      website_url: null,
    })
    runGenerateForRoute(null)
  }

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
            <SourceTypePills value={designSource} onChange={setDesignSource} />

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

          {/* A resolve error from a prior attempt drops back to config with the
              message shown above the form so the user can retry / switch source. */}
          {locateError && (
            <p className="locate-error" data-testid="locate-error" role="alert">
              {locateError}
            </p>
          )}
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

          {/* picker phase — an ambiguous match; the user picks the screen. Reuses
              the existing inline confirm view. */}
          {flowPhase === "picker" && locateResult && (
            <LocateConfirmView
              candidates={mapLocateCandidates(locateResult.ranked)}
              onChoose={(route, id) => {
                // Pick → generating. forCodebase=true so the github wiring is on.
                runGenerateForRoute(route, undefined, id, repoSel, true)
              }}
              onSearchOther={() => {
                // Switch source = a phase change BACK to config (not a remount).
                // Clearing the in-flight guard lets a fresh flow start later.
                locateInFlightRef.current = false
                flowTokenRef.current++
                setLocateResult(null)
                setFlowPhase("config")
              }}
            />
          )}

          {/* unmapped-resolve phase — no match. Pick a screen (from the ranked
              fallbacks, if any) or switch source back to config. */}
          {flowPhase === "unmapped-resolve" && (
            <div data-testid="unmapped-resolve">
              <p className="locate-hint" data-testid="locate-unmapped">
                We couldn&apos;t match your codebase to a screen — pick a screen
                or switch source.
              </p>
              {locateResult && locateResult.ranked.length > 0 && (
                <LocateConfirmView
                  question="Pick the closest screen:"
                  candidates={mapLocateCandidates(locateResult.ranked)}
                  onChoose={(route, id) => {
                    // unmapped has no snapshot to pin against → omit the SHA.
                    runGenerateForRoute(route, null, id, repoSel, true)
                  }}
                />
              )}
              <button
                type="button"
                className="btn"
                data-testid="unmapped-switch-source"
                onClick={() => {
                  locateInFlightRef.current = false
                  flowTokenRef.current++
                  setLocateResult(null)
                  setFlowPhase("config")
                }}
              >
                Switch source
              </button>
            </div>
          )}
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
                (codebaseMode && !repoSel)
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
