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
import { IconClose, IconSparkle } from "../shared/app-icons"

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
    /** P5-02: Scenario B fallback source (shown only when no Figma). */
    website_url?: string | null
    /** P5-02: manual color/font floor (shown only when no Figma). */
    manual_design?: { primary_color: string; font_family: string } | null
  }
  generate: typeof designAgentApi.generate
  runGeneration: typeof runDesignAgentGeneration
  onOpenChange: (open: boolean) => void
  showToast: (title: string, sub: string) => void
  setSubmitting: (value: boolean) => void
  /** F3 opt-in: only toast on ready-completion when the user asked to be notified. */
  notifyOnReady: boolean
  /** P2-12: receives the terminal poll outcome so the host can render the
   *  post-generation result view. Optional — absent in the pre-P2-12 flow. */
  onGenerated?: (result: DesignAgentGenResult) => void
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
  websiteUrl,
  manualColor,
  manualFont,
}: {
  prdId: number
  platform: TargetPlatform
  instructions: string
  figmaFileKey?: string | null
  websiteUrl: string
  manualColor: string
  manualFont: string
}): GenerateFlowDeps["params"] {
  return {
    prd_id: prdId,
    target_platform: platform,
    instructions,
    figma_file_key: figmaFileKey ?? null,
    website_url: websiteUrl || null,
    manual_design:
      manualColor && manualFont
        ? { primary_color: manualColor, font_family: manualFont }
        : null,
  }
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
  onGenerated,
}: GenerateFlowDeps): Promise<void> {
  setSubmitting(true)
  try {
    const kickoff = await generate(params)
    onOpenChange(false)
    showToast(
      "Design Agent generating",
      "We'll let you know when the prototype is ready.",
    )
    void runGeneration({ prototypeId: kickoff.prototype_id }).then((result) => {
      if (result.ok) {
        if (notifyOnReady) {
          showToast(
            "Prototype ready",
            "Open the PRD's Design section to view it.",
          )
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

          {/* P5-02 Scenario B floor — only when no Figma source is connected.
              A brand URL (matched automatically) plus a manual color + font that
              guarantee styled output even with no extractor (the absolute floor). */}
          {!figmaFileKey && (
            <div style={{ marginTop: 16 }}>
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
                We&apos;ll match the site&apos;s colors and fonts. No site? Set a
                brand color and font below.
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
          )}

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

          <div
            style={{
              marginTop: 14,
              padding: "10px 12px",
              background: "var(--surface-2)",
              borderRadius: 8,
              fontSize: 12.5,
              color: "var(--muted)",
            }}
          >
            Source detected: {sourceDetectedLabel(figmaFileKey)}
          </div>
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
