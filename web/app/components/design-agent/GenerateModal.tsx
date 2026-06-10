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
} from "../../lib/api"
import {
  runDesignAgentGeneration,
  type DesignAgentGenResult,
} from "../../lib/runDesignAgentGeneration"
import {
  runGenerateFlow,
  buildGenerateParams,
  DEFAULT_PLATFORM,
  redirectToConnect,
  type TargetPlatform,
} from "./DesignAgentDrawer"
import { getGenerateConnectorRowState } from "../../lib/generateConnectorRowState"
import { IconClose } from "../shared/app-icons"

const PLATFORM_OPTIONS: { value: TargetPlatform; label: string }[] = [
  { value: "desktop", label: "Desktop" },
  { value: "mobile", label: "Mobile" },
  { value: "both", label: "Both" },
]

const SOURCE_OPTIONS: { value: "figma" | "github" | "website"; label: string }[] = [
  { value: "figma", label: "Figma" },
  { value: "github", label: "Codebase" },
  { value: "website", label: "Website" },
]

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
}: {
  open: boolean
  onClose: () => void
  prdId: number | null
  figmaFileKey: string | null
  onGenStart?: (ctx?: { figmaFileKey?: string | null; githubRepo?: string | null }) => void
  /** Fires immediately after generate POST returns with the new prototype_id. */
  onKickoff?: (prototypeId: number) => void
  // onGenDone receives the terminal generation RESULT (DesignAgentGenResult) so
  // the parent can reveal the full-screen post-generation canvas on success. May
  // be undefined if the flow rejects before producing a result.
  onGenDone?: (result?: DesignAgentGenResult) => void
}) {
  const { showToast } = useNavigation()

  const [platform, setPlatform] = useState<TargetPlatform>(DEFAULT_PLATFORM)
  const [designSource, setDesignSource] = useState<"figma" | "github" | "website">("website")
  const [instructions, setInstructions] = useState("")
  const [submitting, setSubmitting] = useState(false)

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
  const [connections, setConnections] = useState<ConnectionSummary[] | null>(null)
  const connFor = (provider: string): ConnectionSummary | undefined =>
    connections?.find((c) => c.provider === provider)

  // Per-provider source selectors.
  // GitHub: real endpoint — connectorsApi.listGithubRepos() → GET
  //   /v1/connectors/github/repos. We fetch + populate the repo <select>.
  const [repos, setRepos] = useState<GitHubRepo[] | null>(null)
  const [reposError, setReposError] = useState(false)
  const [repoSel, setRepoSel] = useState("")

  useEffect(() => {
    if (!open) return
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

  if (!open) return null

  // Figma + GitHub row state (connected vs not + account label) from the shared
  // row helper applied to each provider's live connection.
  const figmaRow = getGenerateConnectorRowState(connFor("figma"))
  const githubRow = getGenerateConnectorRowState(connFor("github"))

  const handleGenerate = () => {
    if (submitting || prdId == null) return
    // Show the full-screen loading overlay the moment generation kicks off. Pass
    // the selected source context so the loading screen can show source-aware steps.
    // The modal then closes (runGenerateFlow's onOpenChange(false)) but the overlay
    // lives in ApproveModal so it survives.
    onGenStart?.({
      figmaFileKey: designSource === "figma" ? (figmaUrlKey || figmaFileKey) : null,
      githubRepo: designSource === "github" && githubActive ? repoSel : null,
    })
    void runGenerateFlow({
      params: buildGenerateParams({
        prdId,
        platform,
        instructions,
        // Only send the chosen source's specific input; the other is cleared to
        // null so the backend receives a clean single-source request.
        figmaFileKey: designSource === "figma" ? (figmaUrlKey || figmaFileKey) : null,
        // figmaNodeId only applies when Figma is the chosen source AND a URL was pasted.
        figmaNodeId: designSource === "figma" && figmaUrlKey ? figmaNodeId : null,
        websiteUrl: "",
        manualColor: "",
        manualFont: "",
        githubRepo: designSource === "github" && githubActive ? repoSel : "",
        designSource,
      }),
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

          {/* Design source — single-select picker using the same radio-pill
              vocabulary as the Platform selector above. Connector-status rows
              always visible so the user can connect a not-yet-connected provider
              before picking it; the source-specific input beneath each row is
              gated on the matching selection. */}
          <div className="field">
            <label className="field-label">Design source</label>
            <div className="radio-group">
              {SOURCE_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  className={
                    "radio-pill" + (designSource === opt.value ? " selected" : "")
                  }
                  data-val={opt.value}
                  aria-pressed={designSource === opt.value}
                  onClick={() => setDesignSource(opt.value)}
                >
                  {opt.label}
                </button>
              ))}
            </div>

            {/* Figma connector status — always shown so user can connect before
                selecting. The URL paste input is only revealed when Figma is
                the active selection AND Figma is connected. */}
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
                <>
                  <span className="src-not-connected">⚠ Not connected</span>
                  <button
                    type="button"
                    className="src-connect-btn"
                    onClick={() => void redirectToConnect("figma")}
                  >
                    Connect Figma →
                  </button>
                </>
              )}
            </div>

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

            {/* Codebase / GitHub connector status — always shown so user can
                connect before selecting. The repo selector is only revealed
                when GitHub is the chosen source and GitHub is connected. */}
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
                <>
                  <span className="src-not-connected muted">Not connected</span>
                  <button
                    type="button"
                    className="src-connect-btn ghost"
                    onClick={() => void redirectToConnect("github")}
                  >
                    Connect a repo →
                  </button>
                </>
              )}
            </div>

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
                      ? "Couldn’t load repos"
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
        </div>

        <div className="modal-foot">
          <span style={{ fontSize: 11.5, color: "var(--muted)" }}>
            Generation is asynchronous — get notified when it&apos;s ready.
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
              onClick={handleGenerate}
              disabled={submitting || prdId == null}
            >
              {submitting ? "Generating…" : "Generate →"}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
