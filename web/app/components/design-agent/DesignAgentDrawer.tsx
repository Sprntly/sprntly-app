"use client"

/**
 * F2 — "Generate Prototype" popup. A NEW SIBLING of ClaudeDrawer (which is on
 * the no-modify hot-file blocklist due to its stub-toast antipattern); this
 * file never imports or mutates ClaudeDrawer. It reuses the repo's drawer CSS
 * (`drawer`, `drawer-overlay`, `btn`, `btn-accent`, `textarea`, `field-label`)
 * and native form controls — the repo ships no shadcn/ui `components/ui/*`.
 *
 * Prop-driven (open / onOpenChange / prdId / figmaFileKey) per the ticket so a
 * future PRD-surface ticket can mount it explicitly. Testability split: the
 * container wires the canonical `useNavigation().showToast` (which needs a
 * provider), while DesignAgentDrawerView holds the pure, SSR-renderable markup
 * + local state, and runGenerateFlow holds the submit orchestration as a pure
 * async function. The repo's vitest env is `node` with no @testing-library, so
 * behaviour is covered by SSR render + direct calls to these exported units.
 */

import { useState } from "react"
import { useNavigation } from "../../context/NavigationContext"
import { designAgentApi } from "../../lib/api"
import {
  runDesignAgentGeneration,
  type DesignAgentGenResult,
} from "../../lib/runDesignAgentGeneration"
import {
  markCompleted,
  markPending,
  markSeenThisLoad,
  pendingCompleted,
  recordReplayShow,
  wasSeenThisLoad,
} from "./notificationStore"
import { IconClose, IconSparkle } from "../shared/app-icons"

/** P1-12 ready-completion toast copy. Reused for the live toast, the persisted
 *  entry's sub, and the post-reload re-show so all three are byte-identical. */
const READY_TOAST_TITLE = "Prototype ready"
const READY_TOAST_SUB = "Your prototype finished generating."

export type TargetPlatform = "desktop" | "mobile" | "both"

export type DesignAgentDrawerProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  prdId: number
  figmaFileKey?: string | null
  /** P2-12: fired with the terminal generation outcome (ok or failure) so the
   *  host launcher can mount the post-generation result view. Optional — the
   *  existing toast flow is unchanged when absent. */
  onGenerated?: (result: DesignAgentGenResult) => void
}

/** Initial target-platform selection (AC2). */
export const DEFAULT_PLATFORM: TargetPlatform = "both"

const PLATFORM_OPTIONS: { value: TargetPlatform; label: string }[] = [
  { value: "desktop", label: "Desktop" },
  { value: "mobile", label: "Mobile" },
  { value: "both", label: "Both" },
]

/** AC3 — source-detection copy. Pure so it is unit-testable directly. */
export function sourceDetectedLabel(figmaFileKey?: string | null): string {
  return figmaFileKey
    ? "Figma design files detected"
    : "No Figma source connected"
}

type GenerateFlowDeps = {
  params: {
    prd_id: number
    target_platform: TargetPlatform
    instructions: string
    figma_file_key?: string | null
    /** Optional Figma node-id (frame-level targeting) parsed from a pasted URL. */
    figma_node_id?: string | null
    /** P5-02: Scenario B fallback source (shown only when no Figma). */
    website_url?: string | null
    /** P5-02: manual color/font floor (shown only when no Figma). */
    manual_design?: { primary_color: string; font_family: string } | null
    /** Connected-repo full_name ("org/repo") the prototype should match.
     *  Prompt context only — no file fetch, no clone, no agent tool. */
    github_repo?: string | null
  }
  generate: typeof designAgentApi.generate
  runGeneration: typeof runDesignAgentGeneration
  onOpenChange: (open: boolean) => void
  showToast: (title: string, sub: string) => void
  setSubmitting: (value: boolean) => void
  /** F3 opt-in: only toast on ready-completion when the user asked to be notified. */
  notifyOnReady: boolean
  /** UX-EXPLORE (throwaway — REVERT): controls the kickoff "Design Agent
   *  generating" toast. Defaults to true (the legacy DesignAgentDrawer flow is
   *  unchanged). The GenerateModal → full-screen-loading-screen path passes
   *  false: the GenerationLoadingScreen overlay now provides generation feedback,
   *  so the kickoff toast is redundant there. Failure toasts are unaffected. */
  notifyOnKickoff?: boolean
  /** P2-12: receives the terminal poll outcome so the host can render the
   *  post-generation result view. Optional — absent in the pre-P2-12 flow. */
  onGenerated?: (result: DesignAgentGenResult) => void
  /** Fires immediately after the generate POST returns with the new prototype_id.
   *  Lets the loading screen subscribe to the SSE stream as soon as the agent starts. */
  onKickoff?: (prototypeId: number) => void
}

/**
 * P5-02 — pure request-param builder. Maps the drawer's form state to the
 * generate request body, including the Scenario B floor:
 *   - `website_url`: the typed URL, or null when blank.
 *   - `manual_design`: the color + font when BOTH are set, else null (a color
 *     with no font name, or vice-versa, is not enough to style output).
 * Extracted (and exported) so the mapping is unit-testable without a DOM — the
 * repo's vitest env is `node` with no @testing-library, so we cannot click the
 * Generate button; we assert the produced params instead.
 */
export function buildGenerateParams({
  prdId,
  platform,
  instructions,
  figmaFileKey,
  figmaNodeId,
  websiteUrl,
  manualColor,
  manualFont,
  githubRepo,
}: {
  prdId: number
  platform: TargetPlatform
  instructions: string
  figmaFileKey?: string | null
  /** Optional node-id extracted from a pasted Figma URL; targets generation at
   *  a specific frame rather than the file's default top-5. Null when the URL
   *  has no node-id or when figmaFileKey came from the PRD context (not a paste). */
  figmaNodeId?: string | null
  websiteUrl: string
  manualColor: string
  manualFont: string
  /** Connected-repo full_name ("org/repo") to match; blank/whitespace -> null. */
  githubRepo?: string
}): GenerateFlowDeps["params"] {
  return {
    prd_id: prdId,
    target_platform: platform,
    instructions,
    figma_file_key: figmaFileKey ?? null,
    figma_node_id: figmaNodeId ?? null,
    website_url: websiteUrl || null,
    manual_design:
      manualColor && manualFont
        ? { primary_color: manualColor, font_family: manualFont }
        : null,
    github_repo: githubRepo?.trim() || null,
  }
}

/**
 * Connect-affordance redirect. The source IA's "Connect Figma" /
 * "Connect a repo" buttons navigate to the Settings → Connectors page so
 * the user can wire up their integration there. Simple and synchronous — no
 * inline OAuth initiation, no server round-trip from the drawer. The
 * Settings page owns the full OAuth handshake.
 */
export function redirectToConnect(provider: "figma" | "github"): void {
  location.href = `/settings?section=connectors`
}

/**
 * AC1 + AC5 — Generate submit orchestration. On a successful kickoff: close the
 * drawer, toast "Design Agent generating", then fire-and-forget the poll. The
 * ready-completion toast (F3) is gated on `notifyOnReady` — when the user did
 * not opt in, generation still runs but no "ready" notification fires. Failures
 * always surface. On a failed kickoff: toast "Generate failed" and leave the
 * drawer open. Extracted as a pure async fn (dependency-injected) so it can be
 * unit-tested without a DOM.
 */
export async function runGenerateFlow({
  params,
  generate,
  runGeneration,
  onOpenChange,
  showToast,
  setSubmitting,
  notifyOnReady,
  notifyOnKickoff = true,
  onGenerated,
  onKickoff,
}: GenerateFlowDeps): Promise<void> {
  setSubmitting(true)
  try {
    const kickoff = await generate(params)
    // P5-09: persist a `pending` entry so a reload mid-generation that then
    // completes still captures the ready notification.
    markPending(kickoff.prototype_id)
    onKickoff?.(kickoff.prototype_id)
    onOpenChange(false)
    // UX-EXPLORE (throwaway — REVERT): the kickoff "Design Agent generating"
    // toast is gated on `notifyOnKickoff` (default true → legacy drawer
    // unchanged). The GenerateModal path passes false because the full-screen
    // GenerationLoadingScreen now provides the kickoff feedback.
    if (notifyOnKickoff) {
      showToast(
        "Design Agent generating",
        "We'll let you know when the prototype is ready.",
      )
    }
    void runGeneration({ prototypeId: kickoff.prototype_id }).then((result) => {
      if (result.ok) {
        // P5-09: record completion BEFORE the live toast so a subsequent
        // same-session reload can re-show it. The persistence delta is
        // independent of the F3 opt-in — the entry is always recorded; only the
        // *live* toast stays gated on `notifyOnReady` (unchanged from P1-12).
        markCompleted(kickoff.prototype_id, READY_TOAST_SUB)
        if (notifyOnReady) {
          showToast(READY_TOAST_TITLE, READY_TOAST_SUB)
        }
      } else {
        showToast("Generation failed", result.message)
      }
      // P2-12: hand the terminal outcome to the host launcher so it can mount
      // the post-generation result view (success) — failures stay toast-only.
      onGenerated?.(result)
    })
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error"
    showToast("Generate failed", message)
  } finally {
    setSubmitting(false)
  }
}

/** AC4 — footer reflects the submitting state. Exported so both states can be
 *  asserted via SSR render (the live submitting flag is internal useState). */
export function DrawerFooter({
  submitting,
  onCancel,
  onGenerate,
}: {
  submitting: boolean
  onCancel: () => void
  onGenerate: () => void
}) {
  return (
    <div className="drawer-foot">
      <span style={{ fontSize: 11.5, color: "var(--muted)" }}>
        Generates a React + Vite prototype from this PRD
      </span>
      <div style={{ display: "flex", gap: 8 }}>
        <button
          type="button"
          className="btn"
          onClick={onCancel}
          disabled={submitting}
        >
          Cancel
        </button>
        <button
          type="button"
          className="btn btn-accent"
          onClick={onGenerate}
          disabled={submitting}
        >
          {submitting ? "Generating…" : "Generate"}
        </button>
      </div>
    </div>
  )
}

/**
 * P5-09 + P6-05 (Decision-D(b)) — re-show any completed-but-unacknowledged
 * ready notification that was persisted before a same-session reload. Hoisted
 * to the authed AppShell (`DesignAgentNotificationReplay`) so it fires on EVERY
 * authed page, not only the PRD Design section where the drawer mounts.
 *
 * Decision-D(b) (RESOLVED 2026-06-04, Babajide): the toast persists until the
 * user acknowledges — it is NOT auto-acked on first show (the bug P6-05 fixes).
 * So this:
 *   - skips ids already shown THIS page-load (`wasSeenThisLoad`) so it fires
 *     once per load even as AppShell re-mounts the replay across navigations;
 *   - shows the toast and records it (`recordReplayShow`) so the replay can ack
 *     its OWN last-shown id when that toast later clears (ack-on-toast-clear,
 *     wired in `DesignAgentNotificationReplay`);
 *   - does NOT call `acknowledge` — the sessionStorage entry survives, so a
 *     subsequent hard reload re-shows it again until the user clears the toast.
 *
 * Pure (sessionStorage + in-memory guards via notificationStore + injected
 * `showToast`) so it stays unit-testable without a DOM (the repo's vitest env
 * is `node`, where effects do not fire under SSR render).
 */
export function replayCompletedNotifications(
  showToast: (title: string, sub: string) => void,
): void {
  for (const n of pendingCompleted()) {
    if (wasSeenThisLoad(n.prototypeId)) continue
    showToast(READY_TOAST_TITLE, n.sub)
    markSeenThisLoad(n.prototypeId)
    recordReplayShow(n.prototypeId, READY_TOAST_TITLE, n.sub)
  }
}

type ViewProps = DesignAgentDrawerProps & {
  showToast: (title: string, sub: string) => void
}

/** Pure presentational + local-state view (no context hooks → SSR-testable). */
export function DesignAgentDrawerView({
  open,
  onOpenChange,
  prdId,
  figmaFileKey,
  showToast,
  onGenerated,
}: ViewProps) {
  const [platform, setPlatform] = useState<TargetPlatform>(DEFAULT_PLATFORM)
  const [instructions, setInstructions] = useState("")
  const [notifyOnReady, setNotifyOnReady] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  // P5-02 Scenario B floor — only used (and only rendered) when no Figma source.
  const [websiteUrl, setWebsiteUrl] = useState("")
  const [manualColor, setManualColor] = useState("#3b82f6")
  const [manualFont, setManualFont] = useState("")

  // P6-05: the completed-notification replay was hoisted OUT of this drawer up
  // to the authed AppShell (`DesignAgentNotificationReplay`) so a hard reload
  // landing on Home / No-draft (where the drawer never mounts) still re-shows an
  // unacknowledged completion toast. Removing the drawer's own mount effect also
  // avoids a double-show when a reload lands ON the Design section (both the
  // shell and the drawer would otherwise replay). `markPending` / `markCompleted`
  // (kickoff + completion persistence) stay in `runGenerateFlow` unchanged.

  if (!open) return null

  const handleGenerate = () => {
    if (submitting) return
    void runGenerateFlow({
      // P5-02: website_url / manual_design are only meaningful when no Figma;
      // they resolve to harmless nulls when their inputs are blank.
      params: buildGenerateParams({
        prdId,
        platform,
        instructions,
        figmaFileKey,
        websiteUrl,
        manualColor,
        manualFont,
      }),
      generate: designAgentApi.generate,
      runGeneration: runDesignAgentGeneration,
      onOpenChange,
      showToast,
      setSubmitting,
      notifyOnReady,
      onGenerated,
    })
  }

  return (
    <>
      <div
        className="drawer-overlay open"
        onClick={() => onOpenChange(false)}
      />
      <aside className="drawer open" role="dialog" aria-label="Generate Prototype">
        <div className="drawer-head">
          <h3 className="drawer-title">
            <span className="drawer-icon">
              <IconSparkle size={15} />
            </span>
            Generate Prototype
          </h3>
          <button
            type="button"
            className="drawer-close"
            onClick={() => onOpenChange(false)}
            aria-label="Close"
          >
            <IconClose size={18} />
          </button>
        </div>
        <div className="drawer-body">
          <p className="drawer-sub">
            Turn this PRD into an interactive React prototype. Pick a target
            platform and add any extra direction for the Design Agent.
          </p>

          <fieldset style={{ border: 0, padding: 0, margin: "0 0 16px" }}>
            <legend className="field-label">Target platform</legend>
            {PLATFORM_OPTIONS.map((opt) => (
              <label
                key={opt.value}
                htmlFor={`dap-platform-${opt.value}`}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  marginTop: 6,
                }}
              >
                <input
                  type="radio"
                  id={`dap-platform-${opt.value}`}
                  name="dap-platform"
                  value={opt.value}
                  checked={platform === opt.value}
                  onChange={() => setPlatform(opt.value)}
                />
                {opt.label}
              </label>
            ))}
          </fieldset>

          <div>
            <label className="field-label" htmlFor="dap-instructions">
              Additional instructions (optional)
            </label>
            <textarea
              id="dap-instructions"
              className="textarea"
              value={instructions}
              onChange={(e) => setInstructions(e.target.value)}
              placeholder={'e.g. "Use a dark theme, emphasise the primary CTA"'}
              rows={4}
            />
          </div>

          {/* P6-15 (UX-5) — source-first IA. Replaces the old read-only "Source
              detected" info row with three EXPLICIT options: Connect Figma
              (primary), Connect a repo (codebase), and the website-style
              inference as the EXPLICIT fallback (the retained P5-02 floor, now
              labelled). The connect buttons redirect to the SAME connectors
              entry points ConnectorsScreen uses (connectorsApi.*AuthorizeUrl) —
              no OAuth handshake in the drawer (Quokka's connectors lane owns the
              flow). Rendered inside DesignAgentLauncher's `.design-agent-surface`
              wrapper, so the appended `.design-agent-surface .src-*` rules apply. */}
          <div style={{ marginTop: 16 }}>
            <span className="field-label">Source for this prototype</span>

            {/* Figma — primary. Connected state read from the existing
                `figmaFileKey` prop (no new connectors-status fetch). */}
            <div className="src-block">
              <div className="src-block-head">
                <span>Figma</span>
                <span className="src-block-tag">primary</span>
              </div>
              <div className="src-row">
                {figmaFileKey ? (
                  <span className="src-connected">
                    {sourceDetectedLabel(figmaFileKey)}
                  </span>
                ) : (
                  <>
                    <span className="src-not-connected">
                      {sourceDetectedLabel(figmaFileKey)}
                    </span>
                    <button
                      type="button"
                      className="src-connect-btn"
                      onClick={() => void redirectToConnect("figma")}
                    >
                      Connect Figma
                    </button>
                  </>
                )}
              </div>
            </div>

            {/* Repo / codebase — Sprntly passes no repo-connected prop today, so
                render the connect affordance unconditionally (seam: a future
                ticket may pass `repoConnected`; UX-5 does not invent one). */}
            <div className="src-block">
              <div className="src-block-head">
                <span>Connect a repo</span>
                <span className="src-block-tag">codebase</span>
              </div>
              <div className="src-row">
                <span className="src-not-connected muted">
                  Match an existing codebase&apos;s style
                </span>
                <button
                  type="button"
                  className="src-connect-btn ghost"
                  onClick={() => void redirectToConnect("github")}
                >
                  Connect a repo
                </button>
              </div>
            </div>

            {/* P5-02 Scenario B floor — the EXPLICIT website-style fallback, now
                labelled with a `src-fallback-note`. Shown only when no Figma
                source is connected: a brand URL (matched automatically) plus a
                manual color + font that guarantee styled output even with no
                extractor (the absolute floor — RETAINED verbatim, never deleted). */}
            {!figmaFileKey && (
              <>
                <div className="src-fallback-note">
                  No design source? We&apos;ll infer a style from a website, or
                  set a brand color and font below.
                </div>
                <div style={{ marginTop: 12 }}>
                  <label className="field-label" htmlFor="dap-website-url">
                    Brand website URL (optional)
                  </label>
                  <input
                    type="url"
                    id="dap-website-url"
                    className="input"
                    value={websiteUrl}
                    onChange={(e) => setWebsiteUrl(e.target.value)}
                    placeholder="https://yourbrand.com"
                  />
                  <p
                    style={{
                      fontSize: 11.5,
                      color: "var(--muted)",
                      margin: "6px 0 0",
                    }}
                  >
                    We&apos;ll match the site&apos;s colors and fonts. No site?
                    Set a brand color and font below.
                  </p>
                  <div
                    style={{
                      display: "flex",
                      gap: 12,
                      marginTop: 12,
                      alignItems: "flex-end",
                    }}
                  >
                    <div>
                      <label className="field-label" htmlFor="dap-manual-color">
                        Brand color
                      </label>
                      <input
                        type="color"
                        id="dap-manual-color"
                        value={manualColor}
                        onChange={(e) => setManualColor(e.target.value)}
                        style={{
                          display: "block",
                          width: 48,
                          height: 34,
                          padding: 2,
                          border: "1px solid var(--border)",
                          borderRadius: 6,
                          background: "var(--surface)",
                        }}
                      />
                    </div>
                    <div style={{ flex: 1 }}>
                      <label className="field-label" htmlFor="dap-manual-font">
                        Brand font
                      </label>
                      <input
                        type="text"
                        id="dap-manual-font"
                        className="input"
                        value={manualFont}
                        onChange={(e) => setManualFont(e.target.value)}
                        placeholder="e.g. Inter"
                      />
                    </div>
                  </div>
                </div>
              </>
            )}
          </div>

          <label
            htmlFor="dap-notify"
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              marginTop: 14,
              fontSize: 13,
              color: "var(--ink-2)",
            }}
          >
            <input
              type="checkbox"
              id="dap-notify"
              checked={notifyOnReady}
              onChange={(e) => setNotifyOnReady(e.target.checked)}
            />
            Notify me when ready
          </label>
        </div>
        <DrawerFooter
          submitting={submitting}
          onCancel={() => onOpenChange(false)}
          onGenerate={handleGenerate}
        />
      </aside>
    </>
  )
}

/**
 * Public component. Wires the canonical toast from NavigationContext and
 * delegates rendering to the pure view.
 */
export function DesignAgentDrawer(props: DesignAgentDrawerProps) {
  const { showToast } = useNavigation()
  return <DesignAgentDrawerView {...props} showToast={showToast} />
}
