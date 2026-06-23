"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { useAuth } from "../../../lib/auth"
import { OnboardingChrome } from "../../onboarding/OnboardingChrome"
import { useOnboarding } from "../../../context/OnboardingContext"
import { completeOnboarding } from "../../../lib/onboarding/store"
import { briefToContentPatch } from "../../../lib/brief-adapter"
import type { Brief } from "../../../lib/api"
import {
  ensureDatasetForWorkspace,
  fetchBriefWhenReady,
  pollBriefStatus,
  seedWorkspaceContextFiles,
  startBriefGeneration,
} from "../../../lib/workspace-brief"
import { useContent } from "../../../context/ContentContext"
import { Check, FileText } from "../../auth/icons"

/**
 * Onboarding "first-brief" step — restyled to the v4 placeholder/loading
 * design (page 16) on the shared OnboardingChrome. This page never previews
 * brief content: it narrates generation with a three-stage checklist
 * (`.gen-stages`) and, when the brief lands, AUTO-FORWARDS into the app —
 * completeOnboarding → localStorage active company → router.replace("/brief")
 * — so the brief itself is only ever seen on the Brief page.
 *
 * Phase → stage mapping (monotonic; stages only ever advance):
 *   1 "Workspace context saved"        active while preparing + seeding
 *   2 "Analyzing your sources"         active once seeding completes
 *   3 "Composing your first Monday Brief"
 *                                      active once the poller first reports
 *                                      status "generating"
 *
 * The failed state never blocks entry: Retry re-runs the pipeline, and
 * "Enter Sprntly anyway" finishes onboarding to home ("/").
 */

type GenPhase =
  | { kind: "idle" }
  | { kind: "preparing" }
  | { kind: "generating"; message: string }
  | { kind: "ready"; brief: Brief }
  | { kind: "failed"; error: string }

const STAGES = [
  {
    label: "Workspace context saved",
    sub: null,
    pendingIcon: null,
  },
  {
    label: "Analyzing your sources",
    sub: "Connected tools · website analysis · success metrics",
    pendingIcon: null,
  },
  {
    label: "Composing your first Monday Brief",
    sub: "Lands on your Brief page when ready",
    pendingIcon: FileText,
  },
] as const

export function FirstBrief() {
  const auth = useAuth()
  const { workspace, loading } = useOnboarding()
  const { setContent } = useContent()
  const router = useRouter()
  const [finishing, setFinishing] = useState(false)
  const [finishError, setFinishError] = useState<string | null>(null)
  const [phase, setPhase] = useState<GenPhase>({ kind: "idle" })
  // 1-based index of the currently ACTIVE generation stage; only ever bumped
  // upward so the checklist never regresses while phases churn.
  const [stage, setStage] = useState(1)
  const startedRef = useRef(false)
  const forwardedRef = useRef(false)

  const bumpStage = useCallback((n: number) => {
    setStage((s) => Math.max(s, n))
  }, [])

  const runGeneration = useCallback(async () => {
    if (!workspace) return
    setStage(1)
    setPhase({ kind: "preparing" })
    try {
      await ensureDatasetForWorkspace(workspace)
      setPhase({ kind: "generating", message: "Saving your workspace context…" })
      await seedWorkspaceContextFiles(workspace)
      // Context is persisted — stage 1 done, "Analyzing your sources" active.
      bumpStage(2)

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
            // Backend is composing — stage 2 done, stage 3 active.
            bumpStage(3)
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
  }, [workspace, setContent, bumpStage])

  useEffect(() => {
    if (!workspace || startedRef.current) return
    startedRef.current = true
    void runGeneration()
  }, [workspace, runGeneration])

  /** Finish onboarding and enter the app at `dest` ("/brief" or "/"). */
  const finish = useCallback(
    async (dest: string) => {
      if (!workspace || auth.kind !== "authed") return
      setFinishError(null)
      setFinishing(true)
      try {
        await completeOnboarding(workspace.id, auth.user.id)
        if (typeof window !== "undefined") {
          window.localStorage.setItem("sprntly_active_company", workspace.slug)
        }
        router.replace(dest)
      } catch (e) {
        // Manual-fallback path: surface the error and re-enable the footer
        // button so the user is never stuck on this page.
        setFinishError(
          e instanceof Error ? e.message : "Couldn't finish onboarding. Try again below.",
        )
        setFinishing(false)
      }
    },
    [workspace, auth, router],
  )

  // AUTO-FORWARD: the moment the brief is ready, finish onboarding and land
  // on /brief. Guarded by a ref so it fires exactly once, and driven from an
  // effect — NEVER as a render side-effect (that pattern surfaced in
  // production as a client-side exception / error boundary). If finish()
  // throws, finishError renders with the footer button as manual fallback.
  useEffect(() => {
    if (phase.kind !== "ready" || forwardedRef.current) return
    if (!workspace || auth.kind !== "authed") return
    forwardedRef.current = true
    void finish("/brief")
  }, [phase, workspace, auth.kind, finish])

  // Redirect when there's no workspace to anchor the step. Done in an effect
  // (not during render) so navigation never fires as a render side-effect —
  // that path surfaces in production as a client-side exception / error
  // boundary. Render returns the loading shell until the redirect lands.
  useEffect(() => {
    if (!loading && !workspace) router.replace("/onboarding/business-info")
  }, [loading, workspace, router])

  if (loading || !workspace) return <div className="onb-shell">Loading…</div>

  const generating =
    phase.kind === "idle" || phase.kind === "preparing" || phase.kind === "generating"

  const title =
    phase.kind === "ready" ? (
      <>
        Your first Brief is <em>ready.</em>
      </>
    ) : phase.kind === "failed" ? (
      <>
        Almost <em>there.</em>
      </>
    ) : (
      <>
        Setting up your <em>workspace.</em>
      </>
    )

  const subtitle =
    phase.kind === "ready"
      ? "Your workspace is live and your coworkers have finished their first pass."
      : phase.kind === "failed"
        ? "Your workspace is set up. The first Brief needs a bit more data — you can still enter Sprntly now and it will land on your Brief page once sources come in."
        : "Your coworkers are reading everything you shared and composing your first Monday Brief. It'll be waiting on your Brief page — this usually takes one to two minutes."

  const footerMeta =
    phase.kind === "ready"
      ? "Workspace ready · 4 of 4 steps complete"
      : phase.kind === "failed"
        ? "You can enrich the next Brief from Sources once inside"
        : "Generating… your Brief opens as soon as it's ready"

  const findings = phase.kind === "ready" ? phase.brief.insights.length : 0

  // The weekly brief is sent Monday 09:00 in the company's local timezone
  // (backend: brief_schedule.should_run_weekly_brief / resolve_timezone). We
  // don't capture a timezone in onboarding, so surface the user's browser
  // timezone when the runtime exposes one and fall back to "your local time".
  const localTimezone =
    typeof Intl !== "undefined"
      ? Intl.DateTimeFormat().resolvedOptions().timeZone || null
      : null
  const briefCadenceCopy = localTimezone
    ? `From now on, Sprntly sends you a fresh Brief of what's happening and the new insights it found every Monday at 9:00 AM (your timezone: ${localTimezone}).`
    : "From now on, Sprntly sends you a fresh Brief of what's happening and the new insights it found every Monday at 9:00 AM your local time."

  return (
    <OnboardingChrome
      step={4}
      title={title}
      subtitle={subtitle}
      footerMeta={footerMeta}
      onBack={() => router.push("/onboarding/connectors")}
      onContinue={() => void finish(phase.kind === "failed" ? "/" : "/brief")}
      continueLabel={phase.kind === "failed" ? "Enter Sprntly anyway" : "Open your Brief"}
      continueDisabled={generating}
      loading={finishing}
    >
      {finishError && (
        <div className="onb-form-error" role="alert">
          {finishError}
        </div>
      )}

      {generating && (
        <div className="gen-stages">
          {STAGES.map((s, i) => {
            const n = i + 1
            const state = n < stage ? "done" : n === stage ? "active" : "pending"
            const PendingIcon = s.pendingIcon
            return (
              <div key={s.label} className={`gen-stage ${state}`}>
                <span className="st-ic" aria-hidden>
                  {state === "done" && <Check style={{ width: 13, height: 13 }} />}
                  {state === "pending" && PendingIcon && (
                    <PendingIcon style={{ width: 12, height: 12 }} />
                  )}
                </span>
                <div>
                  {s.label}
                  {s.sub && <span className="st-sub">{s.sub}</span>}
                </div>
              </div>
            )
          })}
        </div>
      )}

      {phase.kind === "ready" && (
        <div className="gen-ready">
          <div className="ic" aria-hidden>
            <Check style={{ width: 17, height: 17 }} />
          </div>
          <div>
            <div className="t">Your Monday Brief is waiting</div>
            <div className="s">
              {findings > 0 ? `${findings} findings, ranked against your KPI tree — open` : "Open"}{" "}
              it to see what your coworkers found, and ask follow-ups in the thread.
            </div>
            <div className="s brief-cadence">{briefCadenceCopy}</div>
          </div>
        </div>
      )}

      {phase.kind === "failed" && (
        <div className="gen-fail" role="alert">
          {phase.error}
          <div className="acts">
            <button type="button" className="btn btn-ghost" onClick={() => void runGeneration()}>
              Retry generation
            </button>
            <Link href="/sources" className="btn btn-ghost">
              Add sources →
            </Link>
          </div>
        </div>
      )}

      <div className="ws-strip">
        <div className="kv">
          Company<b>{workspace.display_name}</b>
        </div>
        {workspace.product?.name && (
          <div className="kv">
            Product<b>{workspace.product.name}</b>
          </div>
        )}
        <div className="kv">
          North star<b className="ns">{workspace.kpi_tree.north_star || "—"}</b>
        </div>
      </div>
    </OnboardingChrome>
  )
}
