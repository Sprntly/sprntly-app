"use client"

import { useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "../../../lib/auth"
import { useFieldValidation } from "../../onboarding/InterviewLayout"
import { OnboardingChrome } from "../../onboarding/OnboardingChrome"
import { useOnboarding } from "../../../context/OnboardingContext"
import { requiredFor } from "../../../lib/onboarding/validation"
import { INDUSTRIES, BUSINESS_TYPES } from "../../../lib/onboarding/types"
import {
  advanceOnboardingStep,
  markSkippedFields,
} from "../../../lib/onboarding/store"
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

/** The picker selects EXACTLY this many success metrics (design onb1 rule). */
const METRIC_PICKS = 3

function canSaveMetrics(picked: SupportingMetric[]): boolean {
  return picked.filter((m) => m.name.trim().length > 0).length === METRIC_PICKS
}

/**
 * Onboarding step 03 — "Your metrics" (split out of the old combined
 * business-info step; same pick-exactly-3 picker + seeding).
 *
 * Mandatory for COMPANY accounts (Continue blocks until 3 picked); PERSONAL
 * accounts get a skip link (no KPI PUT, recorded in skipped_fields).
 *
 * Seeding order: saved KPI tree → website-analysis suggestions → business-type
 * / industry defaults — identical to the old business-info behavior.
 */
export function MetricsStep() {
  const auth = useAuth()
  const { workspace, profile, setWorkspace, websiteAnalysis, loading } = useOnboarding()
  const router = useRouter()

  const draft = loadDraft(DRAFT_KEY)
  const [candidates, setCandidates] = useState<MetricCandidate[]>(
    (draft?.candidates as MetricCandidate[]) ?? [],
  )
  const [selected, setSelected] = useState<string[]>((draft?.selected as string[]) ?? [])
  const [customMetric, setCustomMetric] = useState("")
  const candidatesSeeded = useRef(false)
  const [limitWarning, setLimitWarning] = useState<string | null>(null)
  const [limitNonce, setLimitNonce] = useState(0)

  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const isCompany = (profile?.account_type ?? "company") === "company"

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
      if (document.hidden) saveDraft(DRAFT_KEY, { candidates, selected })
    }
    document.addEventListener("visibilitychange", onHide)
    return () => document.removeEventListener("visibilitychange", onHide)
  }, [candidates, selected])

  // Redirect when there's no workspace to anchor the step.
  useEffect(() => {
    if (!loading && !workspace) router.replace("/onboarding/company")
  }, [loading, workspace, router])

  // Hydrate the picker from a KPI tree already saved on the workspace.
  useEffect(() => {
    if (!workspace) return
    if (candidatesSeeded.current) return
    if (draft?.candidates) {
      candidatesSeeded.current = true
      return
    }
    const named = workspace.kpi_tree.metrics.filter((m) => m.name.trim().length > 0)
    if (named.length) {
      candidatesSeeded.current = true
      const pool = mergeCandidates(
        named.map((m) => ({ name: m.name, description: m.description ?? "" })),
      )
      setCandidates(pool)
      setSelected(pool.slice(0, METRIC_PICKS).map((c) => c.name))
    }
  }, [workspace]) // eslint-disable-line react-hooks/exhaustive-deps

  const suggestedMetrics = websiteAnalysis?.suggested_metrics ?? []

  // Seed the candidate pool from analysis suggestions, else defaults.
  useEffect(() => {
    if (candidatesSeeded.current) return

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
    candidatesSeeded.current = true
    setCandidates(pool)
    setSelected(pool.slice(0, METRIC_PICKS).map((c) => c.name))
  }, [suggestedMetrics, resolvedBusinessType, resolvedIndustry])

  const { errors, validate, clearError, containerRef } = useFieldValidation(() => [
    requiredFor(isCompany, {
      key: "metrics",
      valid: canSaveMetrics(selectedAsMetrics(candidates, selected)),
      message: `Pick exactly ${METRIC_PICKS} metrics to continue.`,
    }),
  ])

  function toggle(name: string) {
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

  async function go(skipped: boolean) {
    if (!workspace || auth.kind !== "authed") return
    setError(null)
    if (!skipped && !validate().ok) return
    setSaving(true)
    try {
      if (skipped) {
        await markSkippedFields(auth.user.id, ["metrics"])
      } else {
        const picks: SupportingMetric[] = selectedAsMetrics(candidates, selected)
        if (canSaveMetrics(picks)) {
          await kpiTreeApi.putFromSelection(buildSelectionPayload(picks))
        }
      }
      const updated = await advanceOnboardingStep(workspace.id, 4)
      setWorkspace(updated)
      clearDraft(DRAFT_KEY)
      router.push("/onboarding/api-key")
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save your metrics.")
      setSaving(false)
    }
  }

  if (loading || !workspace) return <div className="onb-shell">Loading…</div>

  const ready = selected.length === METRIC_PICKS

  return (
    <OnboardingChrome
      step={3}
      saveLabel="Saved · auto-saves"
      title={
        <>
          Your <em>metrics.</em>
        </>
      }
      subtitle="Pick the numbers that define success — every brief is ranked by impact on these."
      footerMeta={
        isCompany ? (
          ready ? (
            `${selected.length} of ${METRIC_PICKS} metrics selected — ready to continue`
          ) : (
            `Pick ${Math.max(METRIC_PICKS - selected.length, 0)} more metric${
              METRIC_PICKS - selected.length === 1 ? "" : "s"
            } to continue`
          )
        ) : (
          <>
            Optional for personal accounts —{" "}
            <button
              type="button"
              className="onb-skip-link"
              onClick={() => void go(true)}
              disabled={saving}
            >
              skip for now
            </button>
          </>
        )
      }
      onBack={() => router.push("/onboarding/product")}
      onContinue={() => void go(false)}
      continueDisabled={saving}
      loading={saving}
    >
      <div ref={containerRef}>
        {error && <div className="onb-form-error">{error}</div>}

        <div className="onb-section" data-field="metrics">
          <div className="onb-section-h">
            Your metrics{" "}
            <span className="opt">— pick up to {METRIC_PICKS} that matter most</span>
          </div>

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
      </div>
    </OnboardingChrome>
  )
}
