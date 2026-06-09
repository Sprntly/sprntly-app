"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { useAuth } from "../../../lib/auth"
import { InterviewLayout } from "../../onboarding/InterviewLayout"
import { KpiTreePreview } from "../../onboarding/KpiTreePreview"
import { useOnboarding } from "../../../context/OnboardingContext"
import { completeOnboarding } from "../../../lib/onboarding/store"
import { briefToContentPatch } from "../../../lib/brief-adapter"
import type { Brief } from "../../../lib/api"
import {
  briefPreviewInsight,
  ensureDatasetForWorkspace,
  fetchBriefWhenReady,
  pollBriefStatus,
  seedWorkspaceContextFiles,
  startBriefGeneration,
} from "../../../lib/workspace-brief"
import { useContent } from "../../../context/ContentContext"

type GenPhase =
  | { kind: "idle" }
  | { kind: "preparing" }
  | { kind: "generating"; message: string }
  | { kind: "ready"; brief: Brief }
  | { kind: "failed"; error: string }

export function Onboarding7() {
  const auth = useAuth()
  const { workspace, loading } = useOnboarding()
  const { setContent } = useContent()
  const router = useRouter()
  const [finishing, setFinishing] = useState(false)
  const [phase, setPhase] = useState<GenPhase>({ kind: "idle" })
  const startedRef = useRef(false)

  const runGeneration = useCallback(async () => {
    if (!workspace) return
    setPhase({ kind: "preparing" })
    try {
      await ensureDatasetForWorkspace(workspace)
      setPhase({ kind: "generating", message: "Saving your workspace context…" })
      await seedWorkspaceContextFiles(workspace)

      const existing = await fetchBriefWhenReady(workspace.slug)
      if (existing) {
        setContent(briefToContentPatch(existing))
        setPhase({ kind: "ready", brief: existing })
        return
      }

      setPhase({ kind: "generating", message: "Generating your first Brief…" })
      await startBriefGeneration(workspace.slug)

      const finalStatus = await pollBriefStatus(workspace.slug, {
        onTick: (s) => {
          if (s.status === "generating") {
            setPhase({ kind: "generating", message: "Sprntly is analyzing your context…" })
          }
        },
      })

      if (finalStatus.status === "failed") {
        setPhase({
          kind: "failed",
          error: finalStatus.error || "Brief generation failed. You can add data sources and try again from Home.",
        })
        return
      }

      const brief = await fetchBriefWhenReady(workspace.slug)
      if (brief) {
        setContent(briefToContentPatch(brief))
        setPhase({ kind: "ready", brief })
      } else {
        setPhase({
          kind: "failed",
          error: "Brief is still processing. Enter Sprntly and check the Weekly Brief in a few minutes.",
        })
      }
    } catch (e) {
      setPhase({
        kind: "failed",
        error: e instanceof Error ? e.message : "Could not start brief generation.",
      })
    }
  }, [workspace, setContent])

  useEffect(() => {
    if (!workspace || startedRef.current) return
    startedRef.current = true
    void runGeneration()
  }, [workspace, runGeneration])

  async function finish() {
    if (!workspace || auth.kind !== "authed") return
    setFinishing(true)
    try {
      await completeOnboarding(workspace.id, auth.user.id)
      if (typeof window !== "undefined") {
        window.localStorage.setItem("sprntly_active_company", workspace.slug)
      }
      router.replace("/")
    } finally {
      setFinishing(false)
    }
  }

  // Redirect when there's no workspace to anchor the step. Done in an effect
  // (not during render) so navigation never fires as a render side-effect —
  // that path surfaces in production as a client-side exception / error
  // boundary. Render returns the loading shell until the redirect lands.
  useEffect(() => {
    if (!loading && !workspace) router.replace("/onboarding/1")
  }, [loading, workspace, router])

  if (loading || !workspace) return <div className="ob-shell">Loading…</div>

  const preview =
    phase.kind === "ready" ? briefPreviewInsight(phase.brief) : null

  return (
    <InterviewLayout
      step={7}
      eyebrow="First Brief preview"
      title={
        phase.kind === "ready"
          ? "Your first Brief is ready"
          : phase.kind === "failed"
            ? "Almost there"
            : "Preparing your first Brief"
      }
      agentMessage={
        phase.kind === "ready"
          ? "Here's the top finding from your first Brief — ranked against your KPI tree. You can drill into the full Brief from Home."
          : phase.kind === "generating" || phase.kind === "preparing"
            ? "We're using your onboarding context to generate a first Brief. This usually takes one to two minutes."
            : phase.kind === "failed"
              ? "You can still enter Sprntly. Add analytics or upload sources under Sources to enrich the next Brief."
              : "Starting brief generation…"
      }
      rightPane={<KpiTreePreview tree={workspace.kpi_tree} />}
      onBack={() => router.push("/onboarding/6")}
      onContinue={finish}
      continueLabel={phase.kind === "ready" ? "Enter Sprntly →" : "Enter Sprntly anyway →"}
      loading={finishing}
      continueDisabled={phase.kind === "preparing" || phase.kind === "generating"}
    >
      {phase.kind === "preparing" || phase.kind === "generating" ? (
        <div className="ob-brief-status">
          <div className="ob-brief-spinner" aria-hidden />
          <p>{phase.kind === "generating" ? phase.message : "Preparing your workspace…"}</p>
        </div>
      ) : null}

      {phase.kind === "failed" && (
        <div className="ob-form-error" role="alert">
          {phase.error}
          <div style={{ marginTop: 12, display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => void runGeneration()}>
              Retry generation
            </button>
            <Link href="/sources" className="btn btn-ghost btn-sm">
              Add sources →
            </Link>
          </div>
        </div>
      )}

      {preview && (
        <div className="ob-brief-preview ob-brief-preview-live">
          <div className="ob-brief-label">{preview.tag}</div>
          <h3 className="ob-brief-title">{preview.headline}</h3>
          {preview.subtitle && <p className="ob-brief-body">{preview.subtitle}</p>}
          {phase.kind === "ready" && (
            <p className="ob-brief-meta">
              Week: {phase.brief.week_label || "This week"} · {phase.brief.insights.length} findings
            </p>
          )}
        </div>
      )}

      {!preview && phase.kind !== "generating" && phase.kind !== "preparing" && (
        <div className="ob-brief-preview">
          <div className="ob-brief-label">Workspace summary</div>
          <ul className="ob-preview-list">
            <li>Company: {workspace.display_name}</li>
            {workspace.product?.name && <li>Product: {workspace.product.name}</li>}
            <li>North star: {workspace.kpi_tree.north_star || "—"}</li>
          </ul>
        </div>
      )}

      <style jsx>{`
        .ob-brief-status {
          display: flex;
          align-items: center;
          gap: 12px;
          padding: 16px;
          background: var(--surface-2);
          border-radius: 10px;
          font-size: 14px;
          color: var(--ink-2);
        }
        .ob-brief-spinner {
          width: 22px;
          height: 22px;
          border: 2px solid var(--line);
          border-top-color: var(--accent);
          border-radius: 50%;
          animation: ob-spin 0.8s linear infinite;
          flex-shrink: 0;
        }
        @keyframes ob-spin {
          to { transform: rotate(360deg); }
        }
        .ob-brief-preview-live {
          border: 1px solid var(--accent);
          background: var(--accent-soft, rgba(15, 111, 78, 0.06));
        }
        .ob-brief-meta {
          font-size: 12px;
          color: var(--muted);
          margin-top: 12px;
        }
      `}</style>
    </InterviewLayout>
  )
}
