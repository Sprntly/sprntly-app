"use client"

/*
 * "Generate prototype" modal (the product's v3 generate step). Opened from the
 * "Approve & next step" modal's "Generate Prototype" option, which hands
 * visibility to the shared navigation modal union (`activeModal === "generate"`).
 * Connector rows are driven by REAL connector status (`connectorsApi.list()`);
 * the GitHub repo selector is wired to the real repo-listing endpoint; the Figma
 * file selector is an honest disabled placeholder until a Figma file-listing
 * endpoint exists. The Generate button reuses the same real generation flow as
 * the launcher drawer (`designAgentApi.generate` → the shared generate flow) —
 * no faked calls. Connector + repo fetches are wrapped in the shared auth-retry
 * helper so a transient token-refresh 401 holds the last-known rows (the modal
 * does not reflow and the Generate button does not move). Source selections are
 * not yet threaded into generation; the repo param and the Figma listing
 * endpoint are future enhancements.
 */

import { useEffect, useState } from "react"
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
  // Figma: no file-listing endpoint exists in api.ts/backend (only
  //   getFigmaFile(key) fetches a SPECIFIC file by key — the Figma REST API has
  //   no "list my files" without a team/project id, and none is wired). So the
  //   Figma selector is an honest placeholder (disabled, empty-state) — no fake
  //   files. The chosen key would feed `figmaFileKey` → figma_file_key once a
  //   real listing exists; until then it falls back to the figmaFileKey prop.
  // GitHub: real endpoint — connectorsApi.listGithubRepos() → GET
  //   /v1/connectors/github/repos. We fetch + populate the repo <select>.
  const [figmaFileSel, setFigmaFileSel] = useState("")
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

  if (!open) return null

  // Figma + GitHub row state (connected vs not + account label) from the shared
  // row helper applied to each provider's live connection.
  const figmaRow = getGenerateConnectorRowState(connFor("figma"))
  const githubRow = getGenerateConnectorRowState(connFor("github"))
  const figmaActive = figmaRow.connected

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
        // the figmaFileKey prop fallback. GitHub repo selection (repoSel) has no
        // generation param to thread into today — buildGenerateParams /
        // GenerateFlowDeps["params"] expose only figma_file_key / website_url /
        // manual_design (see DesignAgentDrawer).
        figmaFileKey: figmaFileSel || figmaFileKey,
        websiteUrl: "",
        manualColor: "",
        manualFont: "",
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
                  {/* Figma file selector. Honest placeholder — no Figma
                      file-listing endpoint exists (api.ts only exposes
                      getFigmaFile(key) for a specific key). Disabled empty-state;
                      no fake files. A real selection would feed figmaFileKey →
                      figma_file_key once a listing endpoint is wired. */}
                  <select
                    className="input src-select-inline"
                    value={figmaFileSel}
                    onChange={(e) => setFigmaFileSel(e.target.value)}
                    disabled
                    aria-label="Select a design"
                  >
                    <option value="">Pick design — not wired yet</option>
                  </select>
                </>
              ) : (
                <>
                  <span className="src-not-connected">⚠ Not connected</span>
                  <button
                    type="button"
                    className="src-connect-btn"
                    onClick={() =>
                      redirectToConnect(connectorsApi.figmaAuthorizeUrl)
                    }
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
                      result when the token can't list. No generation param for the
                      repo yet, so selection is captured but not threaded into
                      buildGenerateParams. */}
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
                    onClick={() =>
                      redirectToConnect(connectorsApi.githubAuthorizeUrl)
                    }
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
