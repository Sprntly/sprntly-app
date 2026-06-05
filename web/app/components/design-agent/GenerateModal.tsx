"use client"

/*
 * UX-EXPLORE (throwaway — REVERT): New "Generate prototype" modal reproducing
 * the v3 mockup. Opened from ApproveModal's "Generate Prototype" option (which
 * now reroutes here instead of the legacy ClaudeDrawer). Connector rows are
 * driven by REAL connectorsApi.list() status; the Generate button reuses the
 * SAME real generation flow as DesignAgentDrawer (designAgentApi.generate →
 * runDesignAgentGeneration via the exported runGenerateFlow). No faked calls.
 *
 * Reuses the app's modal shell (.modal-overlay/.modal/.modal-head/.modal-title/
 * .modal-sub/.modal-body/.modal-foot/.modal-close) and the existing scoped
 * .src-* / .field / .input / .textarea styles. The v3-only additions
 * (.radio-group/.radio-pill/.modal-foot-r/.gen-breadcrumb) are appended to
 * design-agent.css under the .design-agent-surface scope, so the modal body is
 * wrapped in `.design-agent-surface`.
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
  // UX-EXPLORE (throwaway — REVERT): full-screen loading-screen hooks. onGenStart
  // fires the instant the kickoff is requested (so the parent can show the
  // overlay); onGenDone fires on the TERMINAL generation outcome (ready/failed/
  // timeout) so the parent can dismiss it.
  onGenStart,
  onGenDone,
}: {
  open: boolean
  onClose: () => void
  prdId: number | null
  figmaFileKey: string | null
  onGenStart?: () => void
  // UX-EXPLORE (throwaway — REVERT): onGenDone now receives the TERMINAL
  // generation RESULT (DesignAgentGenResult) so the parent can reveal the
  // full-screen post-generation canvas on success. May be undefined if the flow
  // rejects before producing a result.
  onGenDone?: (result?: DesignAgentGenResult) => void
}) {
  const { showToast } = useNavigation()
  // UX-EXPLORE (throwaway — REVERT): content/prdTitle was only used by the
  // removed breadcrumb; kept the hook import-free by dropping the destructure.

  const [platform, setPlatform] = useState<TargetPlatform>(DEFAULT_PLATFORM)
  const [instructions, setInstructions] = useState("")
  const [submitting, setSubmitting] = useState(false)

  // Real connector status — figma + github rows derive connected vs not from
  // connectorsApi.list() (same source AppShell uses for connectedConnectorIds).
  const [connections, setConnections] = useState<ConnectionSummary[] | null>(null)

  // UX-EXPLORE (throwaway — REVERT): per-provider source selectors.
  // Figma: NO listing endpoint exists in api.ts/backend (only getFigmaFile(key)
  //   fetches a SPECIFIC file by key — the Figma REST API has no "list my files"
  //   without a team/project id, and none is wired). So the Figma selector is an
  //   HONEST placeholder (disabled, empty-state) — we do NOT hardcode fake files.
  //   The chosen key would feed `figmaFileKey` → figma_file_key once a real
  //   listing exists; until then it falls back to the figmaFileKey prop.
  // GitHub: REAL endpoint — connectorsApi.listGithubRepos() → GET
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

  // UX-EXPLORE (throwaway — REVERT): fetch the connected user's GitHub repos for
  // the repo selector — REAL endpoint. Runs only when GitHub is active.
  const githubActive =
    connections?.find((c) => c.provider === "github")?.status === "active"
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

  const connFor = (provider: string): ConnectionSummary | undefined =>
    connections?.find((c) => c.provider === provider)
  const figmaConn = connFor("figma")
  const githubConn = connFor("github")
  const figmaActive = figmaConn?.status === "active"

  const handleGenerate = () => {
    if (submitting || prdId == null) return
    // UX-EXPLORE (throwaway — REVERT): show the full-screen loading overlay the
    // moment generation kicks off. The modal then closes (runGenerateFlow's
    // onOpenChange(false)) but the overlay lives in ApproveModal so it survives.
    onGenStart?.()
    void runGenerateFlow({
      params: buildGenerateParams({
        prdId,
        platform,
        instructions,
        // UX-EXPLORE (throwaway — REVERT): a real Figma-file selection (once a
        // listing endpoint exists) overrides the figmaFileKey prop fallback.
        // GitHub repo selection (repoSel) has NO generation param to thread into
        // today — buildGenerateParams / GenerateFlowDeps["params"] expose only
        // figma_file_key / website_url / manual_design (see DesignAgentDrawer).
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
      // UX-EXPLORE (throwaway — REVERT): the full-screen GenerationLoadingScreen
      // (shown via ApproveModal's genLoading, driven by onGenStart/onGenDone)
      // now provides all generation feedback for this path, so the success
      // toasts are redundant and REMOVED: notifyOnReady=false suppresses the
      // "Prototype ready" success toast, notifyOnKickoff=false suppresses the
      // "Design Agent generating" kickoff toast. Failure surfacing is unchanged
      // — runGenerateFlow still toasts "Generation failed" / "Generate failed".
      notifyOnReady: false,
      notifyOnKickoff: false,
      // UX-EXPLORE (throwaway — REVERT): runGenerateFlow fires onGenerated on the
      // TERMINAL poll outcome (ready OR failed/timeout) — that's our dismissal
      // signal for the loading overlay. SEPARATE from the toasts above: removing
      // the toasts does NOT touch this callback. The flow's own 6-min timeout
      // bounds it, so the overlay can never hang forever. If the kickoff itself
      // throws, onGenerated never fires; the catch in runGenerateFlow toasts
      // "Generate failed" but won't dismiss — covered below by a kickoff-failure
      // fallback.
      // UX-EXPLORE (throwaway — REVERT): thread the terminal RESULT through to
      // onGenDone so ApproveModal can reveal the full-screen canvas on success
      // ({ok:true, prototype}) and skip it on failure ({ok:false, message}).
      onGenerated: (result) => onGenDone?.(result),
    }).catch(() => {
      // UX-EXPLORE (throwaway — REVERT): defensive — if the whole flow rejects
      // (shouldn't, runGenerateFlow swallows kickoff errors), still dismiss.
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
        {/* UX-EXPLORE (throwaway — REVERT): compact header — breadcrumb +
            sub-paragraph removed; badge + title + close only. */}
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
          {/* UX-EXPLORE (throwaway — REVERT): platform label + pills on ONE row
              (gen-inline-field), label left / pills inline right. */}
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

          {/* UX-EXPLORE (throwaway — REVERT): Design source — each provider
              collapsed to a SINGLE compact row (bullet + name + tag + status +
              inline control), tight vertical rhythm. Connected/not + selectors
              all driven by REAL connector status. */}
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
                    {figmaConn?.account_label
                      ? ` · ${figmaConn.account_label}`
                      : ""}
                  </span>
                  {/* UX-EXPLORE (throwaway — REVERT): Figma file selector.
                      HONEST PLACEHOLDER — no Figma file-listing endpoint exists
                      (api.ts only exposes getFigmaFile(key) for a SPECIFIC key).
                      Disabled empty-state; no fake files. A real selection would
                      feed figmaFileKey → figma_file_key.
                      // TODO(ticket): wire real Figma file listing endpoint */}
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
                    {githubConn?.account_label
                      ? ` · ${githubConn.account_label}`
                      : ""}
                  </span>
                  {/* UX-EXPLORE (throwaway — REVERT): GitHub repo selector.
                      WIRED TO A REAL ENDPOINT — connectorsApi.listGithubRepos()
                      → GET /v1/connectors/github/repos. Empty/placeholder is the
                      honest result when the token can't list. No generation param
                      for the repo yet, so selection is captured but not threaded
                      into buildGenerateParams.
                      // TODO(ticket): wire real GitHub repo listing endpoint */}
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

            {/* UX-EXPLORE (throwaway — REVERT): fallback collapsed to a single
                line of muted helper text (was a full-width note block). */}
            <p className="src-fallback-line">
              No design source? We&apos;ll infer a style from the brand website.
            </p>
          </div>

          {/* UX-EXPLORE (throwaway — REVERT): Instructions moved below source;
              textarea shrunk to 2 rows. */}
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
            Generation is asynchronous — get notified when it&apos;s ready (F3).
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
