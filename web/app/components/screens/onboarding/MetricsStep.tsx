"use client"

import { useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "../../../lib/auth"
import { useFieldValidation } from "../../onboarding/InterviewLayout"
import { OnboardingChrome } from "../../onboarding/OnboardingChrome"
import { useOnboarding } from "../../../context/OnboardingContext"
import {
  INDUSTRIES,
  BUSINESS_TYPES,
  PRIORITIZATION_FRAMEWORKS,
  stepForSlug,
} from "../../../lib/onboarding/types"
import { updateWorkspace } from "../../../lib/onboarding/store"
import { saveDraft, loadDraft, clearDraft } from "../../../lib/onboarding/useFormDraft"
import {
  buildSelectionPayload,
  kpiTreeApi,
  type SupportingMetric,
} from "../../../lib/onboarding/kpiTreeApi"
import {
  mergeCandidates,
  selectedAsMetrics,
  DEFAULT_METRICS_BY_BUSINESS_TYPE,
  FALLBACK_CANDIDATES_BY_INDUSTRY,
  type MetricCandidate,
} from "./Metrics"
import { Check, InfoCircle, Plus } from "../../auth/icons"

const DRAFT_KEY = "metrics-step"

/** v6: pick UP TO this many success metrics (at least one to continue). */
const METRIC_PICKS = 5

function canSaveMetrics(picked: SupportingMetric[]): boolean {
  const n = picked.filter((m) => m.name.trim().length > 0).length
  return n >= 1 && n <= METRIC_PICKS
}

/**
 * Onboarding step 07 — "Your metrics" (v6 screenshot spec 2026-07-17,
 * reordered 2026-07-22).
 *
 * Pick up to 5 success metrics (at least one), plus "How does your team
 * prioritize?"* — the prioritization framework moved here from the old team
 * step so metrics and how they're weighed live on one screen.
 *
 * Seeding order: saved KPI tree → website-analysis suggestions → business-type
 * / industry defaults — unchanged from the previous flow.
 */
export function MetricsStep() {
  const auth = useAuth()
  const { workspace, setWorkspace, websiteAnalysis, loading } = useOnboarding()
  const router = useRouter()

  const draft = loadDraft(DRAFT_KEY)
  const [candidates, setCandidates] = useState<MetricCandidate[]>(
    (draft?.candidates as MetricCandidate[]) ?? [],
  )
  const [selected, setSelected] = useState<string[]>((draft?.selected as string[]) ?? [])
  const [customMetric, setCustomMetric] = useState("")
  const [framework, setFramework] = useState((draft?.framework as string) ?? "")
  // Which source seeded the candidate pool. Lets a late-arriving context import
  // (metrics on companies.kpi_tree) SUPERSEDE the analysis/defaults shown on
  // mount, while latching so a new `suggestedMetrics` array reference can't loop
  // the effect. `metricsTouched` freezes the pool the moment the user picks, so
  // the import — which lands ~30-60s after upload — never overwrites their choice.
  const metricsSource = useRef<"none" | "defaults" | "imported" | "draft">("none")
  const metricsTouched = useRef(false)
  const [limitWarning, setLimitWarning] = useState<string | null>(null)
  const [limitNonce, setLimitNonce] = useState(0)

  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const resolvedIndustry =
    workspace?.industry || websiteAnalysis?.industry || INDUSTRIES[0]
  const resolvedBusinessType =
    workspace?.business_type || websiteAnalysis?.business_type || BUSINESS_TYPES[0]

  function flashLimitWarning() {
    setLimitWarning(`You can pick up to ${METRIC_PICKS} metrics — deselect one to swap.`)
    setLimitNonce((n) => n + 1)
  }
  useEffect(() => {
    if (!limitWarning) return
    const t = setTimeout(() => setLimitWarning(null), 3500)
    return () => clearTimeout(t)
  }, [limitWarning, limitNonce])

  useEffect(() => {
    const onHide = () => {
      if (document.hidden) saveDraft(DRAFT_KEY, { candidates, selected, framework })
    }
    document.addEventListener("visibilitychange", onHide)
    return () => document.removeEventListener("visibilitychange", onHide)
  }, [candidates, selected, framework])

  // Redirect when there's no workspace to anchor the step.
  useEffect(() => {
    if (!loading && !workspace) router.replace("/onboarding/company")
  }, [loading, workspace, router])

  // Seed the framework from the saved workspace. Fill-only: a value the user
  // picked (or restored from a draft) is never overwritten when a late import
  // lands — it only fills the field while it is still empty.
  useEffect(() => {
    if (!workspace) return
    const imported = workspace.prioritization_framework ?? ""
    if (imported) setFramework((cur) => cur || imported)
  }, [workspace]) // eslint-disable-line react-hooks/exhaustive-deps

  const suggestedMetrics = websiteAnalysis?.suggested_metrics ?? []

  // Seed the metric picker, preferring metrics already on the workspace
  // (companies.kpi_tree — where the context import writes) over analysis
  // suggestions and business/industry defaults. Runs on every relevant change,
  // so the import that finishes ~30-60s after upload (while the user is on the
  // connectors step) SUPERSEDES the defaults shown on mount the moment it
  // arrives — the bug before was a single latch that froze the defaults in and
  // locked the import out. Latched per-source so it can't loop on a new
  // `suggestedMetrics` reference, and frozen entirely once the user picks.
  useEffect(() => {
    if (!workspace) return
    if (metricsTouched.current) return
    // A saved draft is the user's own prior work — treat it as their pick.
    if (draft?.candidates) {
      metricsSource.current = "draft"
      return
    }
    if (metricsSource.current === "imported") return

    const named = workspace.kpi_tree.metrics.filter((m) => m.name.trim().length > 0)
    if (named.length) {
      const pool = mergeCandidates(
        named.map((m) => ({ name: m.name, description: m.description ?? "" })),
      )
      metricsSource.current = "imported"
      setCandidates(pool)
      setSelected(pool.slice(0, METRIC_PICKS).map((c) => c.name))
      return
    }

    // No workspace metrics yet — show analysis/defaults once and wait for a
    // possible import to replace them. Not re-seeded on later renders (the
    // `defaults` latch below), so a fresh `suggestedMetrics` array reference
    // can't loop this effect. Preselect the first 3 (the "3 of 5" default).
    if (metricsSource.current !== "none") return
    const fromAnalysis: MetricCandidate[] = suggestedMetrics
      .filter((m) => m.metric)
      .map((m) => ({ name: m.metric, description: m.description ?? "" }))
    const fromBizDefaults: MetricCandidate[] = (
      DEFAULT_METRICS_BY_BUSINESS_TYPE[resolvedBusinessType] ?? []
    ).map((name) => ({ name, description: "" }))
    const fromIndustryFallback: MetricCandidate[] = (
      FALLBACK_CANDIDATES_BY_INDUSTRY[resolvedIndustry] ??
      FALLBACK_CANDIDATES_BY_INDUSTRY.default
    ).map((name) => ({ name, description: "" }))

    const pool = mergeCandidates(fromAnalysis, fromBizDefaults, fromIndustryFallback)
    if (pool.length === 0) return
    metricsSource.current = "defaults"
    setCandidates(pool)
    setSelected(pool.slice(0, 3).map((c) => c.name))
  }, [workspace, suggestedMetrics, resolvedBusinessType, resolvedIndustry]) // eslint-disable-line react-hooks/exhaustive-deps

  const { errors, validate, clearError, containerRef } = useFieldValidation(() => [
    {
      key: "metrics",
      valid: canSaveMetrics(selectedAsMetrics(candidates, selected)),
      message: "Pick at least one metric to continue.",
    },
    {
      key: "framework",
      valid: framework.trim().length > 0,
      message: "Pick how your team prioritizes.",
    },
  ])

  function toggle(name: string) {
    // The user is now curating the pool — freeze it against a later import.
    metricsTouched.current = true
    const key = name.toLowerCase()
    const isSelected = selected.some((s) => s.toLowerCase() === key)
    if (!isSelected && selected.length >= METRIC_PICKS) {
      flashLimitWarning()
      return
    }
    setSelected((prev) => {
      if (prev.some((s) => s.toLowerCase() === key)) {
        return prev.filter((s) => s.toLowerCase() !== key)
      }
      if (prev.length >= METRIC_PICKS) return prev
      clearError("metrics")
      return [...prev, name]
    })
  }

  function addCustom() {
    const m = customMetric.trim()
    if (!m) return
    // The user is now curating the pool — freeze it against a later import.
    metricsTouched.current = true
    const key = m.toLowerCase()
    setCandidates((prev) =>
      prev.some((c) => c.name.toLowerCase() === key)
        ? prev
        : [...prev, { name: m, description: "" }],
    )
    const alreadySelected = selected.some((s) => s.toLowerCase() === key)
    if (!alreadySelected && selected.length >= METRIC_PICKS) {
      flashLimitWarning()
      setCustomMetric("")
      return
    }
    setSelected((prev) =>
      prev.some((s) => s.toLowerCase() === key) || prev.length >= METRIC_PICKS
        ? prev
        : [...prev, m],
    )
    clearError("metrics")
    setCustomMetric("")
  }

  async function persist(): Promise<boolean> {
    if (!workspace || auth.kind !== "authed") return false
    setError(null)
    if (!validate().ok) return false
    setSaving(true)
    try {
      const picks: SupportingMetric[] = selectedAsMetrics(candidates, selected)
      if (canSaveMetrics(picks)) {
        await kpiTreeApi.putFromSelection(buildSelectionPayload(picks))
      }
      const updated = await updateWorkspace(workspace.id, {
        prioritization_framework: framework || null,
        onboarding_step: stepForSlug("invite") ?? 8,
      })
      setWorkspace(updated)
      clearDraft(DRAFT_KEY)
      return true
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save your metrics.")
      setSaving(false)
      return false
    }
  }

  async function go() {
    // Next numbered step is invite — persist() writes its index, derived
    // from the slug list rather than hardcoded so a reorder can't strand a
    // resuming user on the wrong step.
    if (await persist()) router.push("/onboarding/invite")
  }

  if (loading || !workspace) return <div className="onb-shell">Loading…</div>

  return (
    <OnboardingChrome
      step={7}
      saveLabel="Saved · auto-saves"
      title={
        <>
          Your <em>metrics.</em>
        </>
      }
      subtitle="Pick up to 5 that matter most. Every brief is ranked by impact on these."
      footerMeta={`${selected.length} of ${METRIC_PICKS} metrics selected`}
      onBack={() => router.push("/onboarding/product")}
      onContinue={() => void go()}
      continueDisabled={saving}
      loading={saving}
    >
      <div ref={containerRef}>
        {error && <div className="onb-form-error">{error}</div>}

        <div className="onb-section" data-field="metrics">
          {errors.metrics && <p className="onb-field-error">{errors.metrics}</p>}

          <div className="metric-chips" id="suggestedMetrics" data-max={METRIC_PICKS}>
            {candidates.length > 0 ? (
              candidates.map((c) => {
                const isSel = selected.some(
                  (s) => s.toLowerCase() === c.name.toLowerCase(),
                )
                const atMaxUnselected = !isSel && selected.length >= METRIC_PICKS
                return (
                  <button
                    type="button"
                    key={c.name}
                    className={`metric ${isSel ? "sel" : ""}`}
                    data-metric={c.name}
                    aria-pressed={isSel}
                    aria-selected={isSel}
                    aria-disabled={atMaxUnselected}
                    onClick={() => toggle(c.name)}
                  >
                    {isSel && (
                      <span className="mt-ic" aria-hidden>
                        <Check style={{ width: 11, height: 11 }} />
                      </span>
                    )}
                    {c.name}
                  </button>
                )
              })
            ) : (
              <p className="mt-targets-empty">
                No candidate metrics yet — add your own below.
              </p>
            )}
          </div>

          <div className="metric-other-row" style={{ marginTop: 12 }}>
            <input
              className="inp"
              id="customMetricInput"
              value={customMetric}
              onChange={(e) => setCustomMetric(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault()
                  addCustom()
                }
              }}
              placeholder="Add your own metric…"
              maxLength={80}
              aria-label="Custom metric name"
            />
            <button
              type="button"
              className="btn btn-secondary"
              onClick={addCustom}
              disabled={!customMetric.trim()}
            >
              <Plus style={{ width: 13, height: 13 }} aria-hidden /> Add
            </button>
          </div>

          {limitWarning && (
            <p className="onb-field-error" role="alert" aria-live="polite">
              {limitWarning}
            </p>
          )}

          <div className="metric-note">
            <span className="mt-ic" aria-hidden>
              <InfoCircle style={{ width: 14, height: 14 }} />
            </span>
            <span>
              These are how Sprntly{" "}
              <strong>prioritizes which issues and ideas to surface</strong> —
              every brief is ranked by impact on the metrics you pick.
            </span>
          </div>
        </div>

        <div className="onb-section" style={{ marginTop: 18 }} data-field="framework">
          <div className="onb-section-h">
            How does your team prioritize? <span className="req">*</span>
          </div>
          {errors.framework && <p className="onb-field-error">{errors.framework}</p>}
          <select
            className={`inp ${errors.framework ? "has-error" : ""}`}
            value={framework}
            onChange={(e) => {
              setFramework(e.target.value)
              clearError("framework")
            }}
            aria-label="Prioritization framework"
          >
            <option value="">Select a framework</option>
            {PRIORITIZATION_FRAMEWORKS.map((f) => (
              <option key={f.value} value={f.value}>
                {f.label}
              </option>
            ))}
          </select>
          <div className="metric-note" style={{ marginTop: 10 }}>
            <span className="mt-ic" aria-hidden>
              <InfoCircle style={{ width: 14, height: 14 }} />
            </span>
            <span>Sprntly weighs recommendations using your framework.</span>
          </div>
        </div>
      </div>
    </OnboardingChrome>
  )
}
