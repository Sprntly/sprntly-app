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

import { useEffect, useState } from "react"
import { useNavigation } from "../../context/NavigationContext"
import {
  connectorsApi,
  designAgentApi,
  withAuthRetry,
  ApiError,
  type ConnectionSummary,
  type FigmaFile,
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

/**
 * Build the Figma file `<select>` options from the fetched list. Pure so the
 * mapping is unit-testable without driving the effect:
 *  - `null`  → still loading ("Loading designs…").
 *  - `[]`    → the honest empty state ("Couldn't load designs") — this is what a
 *              non-401 fetch failure AND a successful-but-unprovisioned listing
 *              both collapse to; NO fake files are ever rendered.
 *  - files   → a "Pick design…" prompt + one `<option value={key}>{name}` per
 *              file, so a selection feeds `figmaFileSel` (→ figma_file_key).
 */
export function figmaFileOptions(figmaFiles: FigmaFile[] | null) {
  if (figmaFiles === null) {
    return <option value="">Loading designs…</option>
  }
  if (figmaFiles.length === 0) {
    return <option value="">Couldn&apos;t load designs</option>
  }
  return (
    <>
      <option value="">Pick design…</option>
      {figmaFiles.map((f) => (
        <option key={f.key} value={f.key}>
          {f.name}
        </option>
      ))}
    </>
  )
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
  // is requested (so the parent can show the overlay); onGenDone fires on the
  // terminal generation outcome (ready/failed/timeout) so the parent can dismiss
  // it.
  onGenStart,
  onGenDone,
}: {
  open: boolean
  onClose: () => void
  prdId: number | null
  figmaFileKey: string | null
  onGenStart?: () => void
  // onGenDone receives the terminal generation RESULT (DesignAgentGenResult) so
  // the parent can reveal the full-screen post-generation canvas on success. May
  // be undefined if the flow rejects before producing a result.
  onGenDone?: (result?: DesignAgentGenResult) => void
}) {
  const { showToast } = useNavigation()

  const [platform, setPlatform] = useState<TargetPlatform>(DEFAULT_PLATFORM)
  const [instructions, setInstructions] = useState("")
  const [submitting, setSubmitting] = useState(false)

  // Real connector status — figma + github rows derive connected vs not from
  // connectorsApi.list() (same source AppShell uses for connectedConnectorIds).
  const [connections, setConnections] = useState<ConnectionSummary[] | null>(null)
  const connFor = (provider: string): ConnectionSummary | undefined =>
    connections?.find((c) => c.provider === provider)

  // Per-provider source selectors.
  // Figma: real endpoint — designAgentApi.listFigmaFiles() → GET
  //   /v1/design-agent/figma-files. We fetch + populate the file <select>. The
  //   chosen key feeds `figmaFileSel` → figma_file_key via the existing
  //   `figmaFileSel || figmaFileKey` fallback. `figmaFiles === null` means "not
  //   loaded yet"; an empty list is the honest "Couldn't load designs" state
  //   (the listing scope/team-id is a connectors-lane dependency) — no fake
  //   files are ever rendered.
  // GitHub: real endpoint — connectorsApi.listGithubRepos() → GET
  //   /v1/connectors/github/repos. We fetch + populate the repo <select>.
  const [figmaFileSel, setFigmaFileSel] = useState("")
  const [figmaFiles, setFigmaFiles] = useState<FigmaFile[] | null>(null)
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

  // Fetch the connected user's GitHub repos for the repo selector — real
  // endpoint. Runs only when GitHub is active. The active check comes from the
  // shared row helper so the effect gate and the rendered row read one mapping.
  const githubActive = getGenerateConnectorRowState(connFor("github")).connected
  useEffect(() => {
    if (!open || !githubActive) return
    let cancelled = false
    setReposError(false)
    void withAuthRetry(() => connectorsApi.listGithubRepos())
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

  // Fetch the connected company's Figma files for the design selector — real
  // endpoint. Runs only when Figma is active. Mirrors the GitHub repo fetch:
  // withAuthRetry holds the last-known rows through a transient token-refresh
  // 401; only a genuine non-auth failure clears to an empty list, which the
  // <select> renders as the honest "Couldn't load designs" state (no fake files).
  const figmaActive = getGenerateConnectorRowState(connFor("figma")).connected
  useEffect(() => {
    if (!open || !figmaActive) return
    let cancelled = false
    void withAuthRetry(() => designAgentApi.listFigmaFiles())
      .then((r) => {
        if (!cancelled) setFigmaFiles(r.files)
      })
      .catch((err) => {
        if (!cancelled && !(err instanceof ApiError && err.status === 401)) {
          setFigmaFiles([])
        }
      })
    return () => {
      cancelled = true
    }
  }, [open, figmaActive])

  if (!open) return null

  // Figma + GitHub row state (connected vs not + account label) from the shared
  // row helper applied to each provider's live connection.
  const figmaRow = getGenerateConnectorRowState(connFor("figma"))
  const githubRow = getGenerateConnectorRowState(connFor("github"))

  const handleGenerate = () => {
    if (submitting || prdId == null) return
    // Show the full-screen loading overlay the moment generation kicks off. The
    // modal then closes (runGenerateFlow's onOpenChange(false)) but the overlay
    // lives in ApproveModal so it survives.
    onGenStart?.()
    void runGenerateFlow({
      params: buildGenerateParams({
        prdId,
        platform,
        instructions,
        // A real Figma-file selection (once a listing endpoint exists) overrides
        // the figmaFileKey prop fallback. The selected GitHub repo now threads
        // into generation as prompt context (github_repo) — only when GitHub is
        // the active connected source; otherwise blank -> null. It tells the
        // agent which existing codebase to match; no file fetch / clone / tool.
        figmaFileKey: figmaFileSel || figmaFileKey,
        websiteUrl: "",
        manualColor: "",
        manualFont: "",
        githubRepo: githubActive ? repoSel : "",
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
        {/* Compact header — badge + title + close only. */}
        <div className="modal-head">
          <div className="modal-badge">Step 1 of 4 · Generate</div>
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

          {/* Design source — each provider collapses to a single compact row
              (bullet + name + tag + status + inline control), tight vertical
              rhythm. Connected/not + selectors all driven by real connector
              status. */}
          <div className="field">
            <label className="field-label">Design source</label>

            {/* Figma — primary source. */}
            <div className="src-row-compact">
              <span className="src-bullet" aria-hidden="true" />
              <span className="src-name">Figma</span>
              <span className="src-block-tag">Primary</span>
              {figmaActive ? (
                <>
                  <span className="src-connected">
                    Connected
                    {figmaRow.accountLabel ? ` · ${figmaRow.accountLabel}` : ""}
                  </span>
                  {/* Figma file selector. Wired to a real endpoint —
                      designAgentApi.listFigmaFiles() → GET
                      /v1/design-agent/figma-files. Enabled once files load; the
                      chosen key (figmaFileSel) feeds figma_file_key via the
                      existing figmaFileSel || figmaFileKey fallback. An empty
                      list is the honest "Couldn't load designs" state — no fake
                      files. */}
                  <select
                    className="input src-select-inline"
                    value={figmaFileSel}
                    onChange={(e) => setFigmaFileSel(e.target.value)}
                    disabled={!figmaFiles || figmaFiles.length === 0}
                    aria-label="Select a design"
                  >
                    {figmaFileOptions(figmaFiles)}
                  </select>
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

            {/* Codebase / GitHub. */}
            <div className="src-row-compact">
              <span className="src-bullet" aria-hidden="true" />
              <span className="src-name">Codebase</span>
              <span className="src-block-tag">Baseline branch</span>
              {githubActive ? (
                <>
                  <span className="src-connected">
                    Connected
                    {githubRow.accountLabel ? ` · ${githubRow.accountLabel}` : ""}
                  </span>
                  {/* GitHub repo selector. Wired to a real endpoint —
                      connectorsApi.listGithubRepos() → GET
                      /v1/connectors/github/repos. Empty/placeholder is the honest
                      result when the token can't list. The chosen repo full_name
                      (repoSel) now threads into generation as prompt context via
                      buildGenerateParams (github_repo) — identifier only, no fetch. */}
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
                          : "Pick repo — not wired yet"}
                      </option>
                    ) : (
                      <>
                        <option value="">Pick repo…</option>
                        {repos.map((r) => (
                          <option key={r.full_name} value={r.full_name}>
                            {r.full_name}
                          </option>
                        ))}
                      </>
                    )}
                  </select>
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

            {/* Fallback as a single line of muted helper text. */}
            <p className="src-fallback-line">
              No design source? We&apos;ll infer a style from the brand website.
            </p>
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
