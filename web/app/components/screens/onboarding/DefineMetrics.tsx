"use client"

import { useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "../../../lib/auth"
import { useOnboarding } from "../../../context/OnboardingContext"
import { useContent } from "../../../context/ContentContext"
import {
  completeOnboarding,
  saveMetricDefinitions,
} from "../../../lib/onboarding/store"
import type { MetricDefinition } from "../../../lib/onboarding/types"
import { briefToContentPatch } from "../../../lib/brief-adapter"
import {
  ensureDatasetForWorkspace,
  fetchBriefWhenReady,
  seedWorkspaceContextFiles,
  startBriefGeneration,
} from "../../../lib/workspace-brief"
import { onboardingApi } from "../../../lib/api"
import { ArrowLeft, ArrowRight } from "../../auth/icons"

/**
 * Post-wizard define-metrics sub-flow (v6 screenshot spec 2026-07-17) — the
 * TRANSIENT, UNNUMBERED closer at `/onboarding/define-metrics` (no progress
 * dots, like the your-name gate).
 *
 * One screen per metric picked in step 3: confirm the AI-drafted
 * plain-English definition and the analytics event mapping (both editable;
 * drafts come from POST /v1/onboarding/metric-definitions, detected from the
 * connected analytics where possible). A closing review table shows metric /
 * mapping / best-effort current value ("—" when none), and "Looks right ·
 * generate knowledge graph" persists companies.metric_definitions, COMPLETES
 * onboarding, kicks the first brief, and enters the app — the same closer
 * the retired workspace step ran.
 */
export function DefineMetrics() {
  const auth = useAuth()
  const { workspace, loading } = useOnboarding()
  const { setContent } = useContent()
  const router = useRouter()

  // null = drafts still loading. Index === defs.length → the review screen.
  const [defs, setDefs] = useState<MetricDefinition[] | null>(null)
  const [index, setIndex] = useState(0)
  const requested = useRef(false)

  const [finishing, setFinishing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Redirect when there's no workspace to anchor the flow.
  useEffect(() => {
    if (!loading && !workspace) router.replace("/onboarding/company")
  }, [loading, workspace, router])

  // Resolve definitions: saved on the company → AI drafts → name-only rows.
  useEffect(() => {
    if (!workspace || requested.current) return
    requested.current = true
    const names = workspace.kpi_tree.metrics
      .map((m) => m.name.trim())
      .filter(Boolean)
    const saved = workspace.metric_definitions
    if (saved.length) {
      // Keep saved rows, appending any newly picked metrics missing from them.
      const have = new Set(saved.map((d) => d.metric.toLowerCase()))
      const missing = names.filter((n) => !have.has(n.toLowerCase()))
      setDefs([
        ...saved,
        ...missing.map((n) => ({ metric: n, definition: "", mapping: "", baseline: null })),
      ])
      return
    }
    if (!names.length) {
      setDefs([])
      return
    }
    onboardingApi
      .draftMetricDefinitions(names)
      .then((r) => {
        const byName = new Map(
          r.definitions.map((d) => [d.metric.toLowerCase(), d] as const),
        )
        setDefs(
          names.map((n) => {
            const d = byName.get(n.toLowerCase())
            return {
              metric: n,
              definition: d?.definition ?? "",
              mapping: d?.mapping ?? "",
              baseline: d?.baseline ?? null,
            }
          }),
        )
      })
      .catch(() =>
        // Drafting failed — the PM writes definitions by hand.
        setDefs(names.map((n) => ({ metric: n, definition: "", mapping: "", baseline: null }))),
      )
  }, [workspace])

  function patch(i: number, p: Partial<MetricDefinition>) {
    setDefs((prev) =>
      prev ? prev.map((d, j) => (j === i ? { ...d, ...p } : d)) : prev,
    )
  }

  async function finish() {
    if (!workspace || auth.kind !== "authed" || !defs) return
    setError(null)
    setFinishing(true)
    try {
      // 1) Persist the confirmed definitions (best-effort content, hard save).
      if (defs.length) await saveMetricDefinitions(workspace.id, defs)

      // 2) Kick the first brief (fire-and-forget). It lands on the Brief page.
      void (async () => {
        try {
          await ensureDatasetForWorkspace(workspace)
          await seedWorkspaceContextFiles(workspace)
          const existing = await fetchBriefWhenReady(workspace.slug)
          if (existing) setContent(briefToContentPatch(existing))
          else await startBriefGeneration(workspace.slug)
        } catch {
          /* generation runs server-side; the Brief page reflects status */
        }
      })()

      // 3) Complete onboarding and enter the app.
      await completeOnboarding(workspace.id, auth.user.id)
      if (typeof window !== "undefined") {
        window.localStorage.setItem("sprntly_active_company", workspace.slug)
      }
      router.replace("/brief")
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Couldn't finish setting up your workspace.",
      )
      setFinishing(false)
    }
  }

  if (loading || !workspace) return <div className="onb-shell">Loading…</div>

  const shell = (children: React.ReactNode) => (
    <div className="onb-shell">
      <div className="onb-head">
        <span className="onb-brand">
          sprntly<span className="dot">.</span>
        </span>
        <span className="save">
          <span className="pulse" />
          Saved
        </span>
      </div>
      <div className="onb-card">{children}</div>
    </div>
  )

  if (defs === null) {
    return shell(
      <p className="onb-field-hint" role="status">
        Drafting how Sprntly should measure each metric — detected from your
        connected analytics where possible…
      </p>,
    )
  }

  // Review screen (also the empty-metrics case — nothing to define).
  if (index >= defs.length) {
    return shell(
      <>
        <div className="onb-section-h">Review</div>
        <div className="onb-h">
          Does this <em>look right?</em>
        </div>
        <div className="onb-sub">
          Here&apos;s how Sprntly will track the metrics you chose, with the
          current number from your data. Confirm to build your knowledge graph.
        </div>

        {error && <div className="onb-form-error">{error}</div>}

        {defs.length ? (
          <table className="onb-review-table" style={{ width: "100%", marginTop: 12 }}>
            <tbody>
              {defs.map((d) => (
                <tr key={d.metric}>
                  <td style={{ fontWeight: 600, padding: "8px 6px" }}>{d.metric}</td>
                  <td style={{ padding: "8px 6px" }}>
                    <code>{d.mapping || "—"}</code>
                  </td>
                  <td style={{ textAlign: "right", padding: "8px 6px", fontWeight: 600 }}>
                    {d.baseline ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="onb-field-hint">
            You haven&apos;t picked metrics yet — you can define them any time in
            Settings → Metrics.
          </p>
        )}

        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            marginTop: 20,
          }}
        >
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() =>
              defs.length ? setIndex(defs.length - 1) : router.push("/onboarding/review")
            }
            disabled={finishing}
          >
            <ArrowLeft style={{ width: 13, height: 13 }} aria-hidden /> Back
          </button>
          <button
            type="button"
            className="btn btn-brand"
            onClick={() => void finish()}
            disabled={finishing}
          >
            {finishing ? "Building…" : "⚡ Looks right · generate knowledge graph"}
          </button>
        </div>
      </>,
    )
  }

  const d = defs[index]
  const isLast = index === defs.length - 1

  return shell(
    <>
      <div className="onb-section-h">
        Metric {index + 1} of {defs.length}
      </div>
      <div className="onb-h">
        Define <em>{d.metric}.</em>
      </div>
      <div className="onb-sub">
        Confirm how Sprntly should measure this — edit the plain-English
        definition or the event mapping, then confirm.
      </div>

      {error && <div className="onb-form-error">{error}</div>}

      <div className="field full" data-field="definition">
        <div className="field-l">Plain-English definition</div>
        <textarea
          className="inp"
          rows={3}
          value={d.definition}
          onChange={(e) => patch(index, { definition: e.target.value })}
          maxLength={500}
          placeholder={`What counts as "${d.metric}" for your product`}
          aria-label={`${d.metric} definition`}
        />
      </div>

      <div className="field full" style={{ marginTop: 12 }} data-field="mapping">
        <div className="field-l">Maps to your analytics</div>
        <input
          className="inp"
          style={{ fontFamily: "var(--font-mono, monospace)" }}
          value={d.mapping}
          onChange={(e) => patch(index, { mapping: e.target.value })}
          maxLength={300}
          placeholder="event: session_start where feature_engaged = true"
          aria-label={`${d.metric} analytics mapping`}
        />
        <p className="onb-field-hint">
          🔗 Detected from your connected analytics — edit if it&apos;s off.
        </p>
      </div>

      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginTop: 20,
        }}
      >
        <button
          type="button"
          className="btn btn-ghost"
          onClick={() =>
            index === 0 ? router.push("/onboarding/review") : setIndex(index - 1)
          }
        >
          <ArrowLeft style={{ width: 13, height: 13 }} aria-hidden /> Back
        </button>
        <button
          type="button"
          className="btn btn-brand"
          onClick={() => setIndex(index + 1)}
        >
          {isLast ? "Confirm · review" : "Confirm · next metric"}{" "}
          <ArrowRight style={{ width: 13, height: 13 }} aria-hidden />
        </button>
      </div>
    </>,
  )
}
