"use client"

import { useEffect, useMemo, useState } from "react"
import { useRouter } from "next/navigation"
import { InterviewLayout, useFieldValidation } from "../../onboarding/InterviewLayout"
import { useOnboarding } from "../../../context/OnboardingContext"
import { advanceOnboardingStep } from "../../../lib/onboarding/store"
import {
  buildKpiTreePayload,
  canSaveKpiTree,
  kpiTreeApi,
  MAX_PRIMARY_METRICS,
  MAX_SECONDARY_SIGNALS,
  type SupportingMetric,
} from "../../../lib/onboarding/kpiTreeApi"

/**
 * Onboarding page 05 (design-v4) — "Set your success metrics."
 *
 * The success metrics that anchor the workspace. The North Star is
 * required; supporting metrics are picked from industry-tailored
 * suggestions or written in. The KPI tree is persisted to the backend
 * (PUT /v1/company/kpi-tree) — the canonical config entity Synthesis
 * later reads for strategic-alignment scoring.
 *
 * Product name + website are captured once on step 1 (the single source
 * of truth); this step only shows the product name for context and does
 * not re-collect it.
 */

// North Star suggestions, tailored loosely by industry. Mirrors the
// "Common for {industry}" block in the v4 mock.
const NORTH_STAR_SUGGESTIONS: Record<string, string[]> = {
  Healthtech: [
    "Day-30 active clinicians per deployment",
    "Weekly active clinicians",
    "Net revenue retention",
  ],
  "B2B SaaS": ["Net revenue retention", "Weekly active teams", "Activation rate"],
  B2C: ["Day-30 retention", "DAU/MAU ratio", "Conversion rate"],
  Fintech: ["Transaction volume", "Net revenue retention", "Activated accounts"],
  default: ["Weekly active users", "Day-30 retention", "Net revenue retention"],
}

// Supporting-metric suggestions ("Primary leads to…") — a flat pool the PM
// toggles. Industry-tailored where we have a list, else a sensible default.
const SUPPORTING_SUGGESTIONS: Record<string, string[]> = {
  Healthtech: [
    "Shift-handoff completion rate",
    "Care plans co-authored / week",
    "Time-to-first-handoff",
    "Weekly active clinicians",
    "EHR session depth",
    "Cross-location context views",
    "Activation rate (week 2)",
    "Average deployment ramp",
  ],
  default: [
    "Activation rate (week 2)",
    "Weekly active users",
    "Feature adoption",
    "Time-to-value",
    "Expansion revenue",
    "Net promoter score",
    "Support tickets / 100 accounts",
    "Churn rate",
  ],
}

const MAX_SUPPORTING = MAX_PRIMARY_METRICS + MAX_SECONDARY_SIGNALS

export type SuccessMetricsViewProps = {
  productName: string
  industry: string
  northStar: string
  northStarDescription: string
  supporting: SupportingMetric[]
  customMetric: string
  northStarHints: string[]
  supportingHints: string[]
  errors: Record<string, string | undefined>
  error: string | null
  onChangeNorthStar: (value: string) => void
  onChangeNorthStarDescription: (value: string) => void
  onPickNorthStar: (value: string) => void
  onToggleSupporting: (metric: string) => void
  onChangeSupportingDescription: (metric: string, description: string) => void
  onChangeCustomMetric: (value: string) => void
  onAddCustom: () => void
}

/**
 * Pure presentational view for step 05 — the success-metrics picker. Kept
 * free of hooks/context so it can be rendered to static markup in tests.
 */
export function SuccessMetricsView({
  productName,
  industry,
  northStar,
  northStarDescription,
  supporting,
  customMetric,
  northStarHints,
  supportingHints,
  errors,
  error,
  onChangeNorthStar,
  onChangeNorthStarDescription,
  onPickNorthStar,
  onToggleSupporting,
  onChangeSupportingDescription,
  onChangeCustomMetric,
  onAddCustom,
}: SuccessMetricsViewProps) {
  const selectedCount = supporting.length
  const selectedNames = supporting.map((m) => m.name)

  return (
    <>
      {error && <div className="ob-form-error">{error}</div>}

      {productName && (
        <p className="ob-metrics-context">
          Success metrics for <strong>{productName}</strong>
        </p>
      )}

      <div className={`field ${errors.northStar ? "has-error" : ""}`} data-field="northStar">
        <label className="field-label">Primary metric — your North Star *</label>
        <input
          className="input"
          value={northStar}
          onChange={(e) => onChangeNorthStar(e.target.value)}
          placeholder="The one metric that best captures product value"
        />
        {errors.northStar && <p className="field-error">{errors.northStar}</p>}
        <div className="ob-ns-hints">
          <span className="ob-ns-hints-label">
            Common for {industry || "your stage"}:
          </span>
          {northStarHints.map((h) => (
            <button
              key={h}
              type="button"
              className="metric-chip"
              onClick={() => onPickNorthStar(h)}
            >
              {h}
            </button>
          ))}
        </div>
        <textarea
          className="input ob-metric-desc"
          value={northStarDescription}
          onChange={(e) => onChangeNorthStarDescription(e.target.value)}
          placeholder="Describe what this metric means and why it matters (context for goal-fit scoring)"
          rows={2}
          maxLength={400}
        />
      </div>

      <div className="field">
        <label className="field-label">
          Supporting metrics — pick what fits, or write your own
        </label>
        <p className="field-hint">Primary leads to…</p>
        <div className="ob-chip-row">
          {supportingHints.map((m) => (
            <button
              key={m}
              type="button"
              className={`metric-chip ${selectedNames.includes(m) ? "selected" : ""}`}
              onClick={() => onToggleSupporting(m)}
            >
              {m}
            </button>
          ))}
          {selectedNames
            .filter((m) => !supportingHints.includes(m))
            .map((m) => (
              <button
                key={m}
                type="button"
                className="metric-chip selected"
                onClick={() => onToggleSupporting(m)}
              >
                {m}
              </button>
            ))}
        </div>
        <div className="ob-custom-metric">
          <input
            className="input"
            value={customMetric}
            onChange={(e) => onChangeCustomMetric(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault()
                onAddCustom()
              }
            }}
            placeholder="Or write your own"
            maxLength={80}
          />
          <button
            type="button"
            className="btn btn-sm"
            onClick={onAddCustom}
            disabled={!customMetric.trim() || supporting.length >= MAX_SUPPORTING}
          >
            Add
          </button>
        </div>
        <p className="ob-metric-count">
          {selectedCount} supporting metric{selectedCount === 1 ? "" : "s"}{" "}
          selected · suggestions tailored to your industry
        </p>
        {supporting.length > 0 && (
          <div className="ob-metric-desc-list">
            {supporting.map((m) => (
              <div key={m.name} className="ob-metric-desc-block" data-metric={m.name}>
                <label className="ob-metric-desc-label">{m.name}</label>
                <textarea
                  className="input ob-metric-desc"
                  value={m.description}
                  onChange={(e) => onChangeSupportingDescription(m.name, e.target.value)}
                  placeholder="Describe what this metric means and why it matters"
                  rows={2}
                  maxLength={400}
                />
              </div>
            ))}
          </div>
        )}
      </div>

      <style jsx>{`
        .ob-metrics-context {
          font-size: 13px;
          color: var(--muted);
          margin: 0 0 18px;
        }
        .ob-ns-hints {
          display: flex;
          flex-wrap: wrap;
          align-items: center;
          gap: 8px;
          margin-top: 10px;
        }
        .ob-ns-hints-label {
          font-size: 12px;
          color: var(--muted);
        }
        .ob-custom-metric {
          display: flex;
          gap: 8px;
          margin-top: 10px;
        }
        .ob-custom-metric :global(.input) {
          flex: 1;
        }
        .ob-metric-count {
          font-size: 12px;
          color: var(--muted);
          margin: 10px 0 0;
        }
        .ob-metric-desc {
          width: 100%;
          margin-top: 10px;
          resize: vertical;
        }
        .ob-metric-desc-list {
          display: flex;
          flex-direction: column;
          gap: 12px;
          margin-top: 14px;
        }
        .ob-metric-desc-label {
          display: block;
          font-size: 13px;
          font-weight: 600;
        }
      `}</style>
    </>
  )
}

export function Onboarding5() {
  const { workspace, setWorkspace, loading } = useOnboarding()
  const router = useRouter()
  const [northStar, setNorthStar] = useState("")
  const [northStarDescription, setNorthStarDescription] = useState("")
  const [supporting, setSupporting] = useState<SupportingMetric[]>([])
  const [customMetric, setCustomMetric] = useState("")
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!workspace) return
    const tree = workspace.kpi_tree
    if (tree.north_star) setNorthStar(tree.north_star)
    if (tree.north_star_description) setNorthStarDescription(tree.north_star_description)
    if (tree.metrics.length) {
      setSupporting(
        tree.metrics
          .filter((m) => m.name)
          .map((m) => ({ name: m.name, description: m.description ?? "" })),
      )
    }
  }, [workspace])

  const industry = workspace?.industry ?? ""
  const productName = workspace?.product?.name ?? workspace?.display_name ?? ""
  const northStarHints =
    NORTH_STAR_SUGGESTIONS[industry] ?? NORTH_STAR_SUGGESTIONS.default
  const supportingHints =
    SUPPORTING_SUGGESTIONS[industry] ?? SUPPORTING_SUGGESTIONS.default

  const { errors, validate, clearError, containerRef } = useFieldValidation(
    () => [
      {
        key: "northStar",
        valid: canSaveKpiTree(northStar, supporting),
        message: "Set a North Star metric to anchor your KPI tree.",
      },
    ],
  )

  function toggleSupporting(metric: string) {
    setSupporting((prev) => {
      if (prev.some((m) => m.name === metric)) {
        return prev.filter((m) => m.name !== metric)
      }
      if (prev.length >= MAX_SUPPORTING) return prev
      return [...prev, { name: metric, description: "" }]
    })
  }

  function changeSupportingDescription(metric: string, description: string) {
    setSupporting((prev) =>
      prev.map((m) => (m.name === metric ? { ...m, description } : m)),
    )
  }

  function addCustom() {
    const m = customMetric.trim()
    if (!m || supporting.some((s) => s.name === m) || supporting.length >= MAX_SUPPORTING)
      return
    setSupporting((prev) => [...prev, { name: m, description: "" }])
    setCustomMetric("")
  }

  async function persist() {
    if (!workspace) return
    setError(null)
    if (!validate().ok) return
    setSaving(true)
    try {
      await kpiTreeApi.put(
        buildKpiTreePayload(northStar, northStarDescription, supporting),
      )
      const updated = await advanceOnboardingStep(workspace.id, 6)
      const product = updated.product ?? workspace.product
      setWorkspace({ ...updated, product })
      router.push("/onboarding/6")
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save your metrics.")
    } finally {
      setSaving(false)
    }
  }

  const previewMetrics = useMemo(
    () => supporting.slice(0, MAX_SUPPORTING),
    [supporting],
  )

  // Redirect when there's no workspace to anchor the step. Done in an effect
  // (not during render) so navigation never fires as a render side-effect —
  // that path surfaces in production as a client-side exception / error
  // boundary. Render returns the loading shell until the redirect lands.
  useEffect(() => {
    if (!loading && !workspace) router.replace("/onboarding/1")
  }, [loading, workspace, router])

  if (loading || !workspace) return <div className="ob-shell">Loading…</div>

  return (
    <InterviewLayout
      step={5}
      eyebrow="Saved · auto-saves after every step"
      title="Set your success metrics"
      agentMessage="Success metrics anchor the whole workspace. Pick your North Star and the supporting metrics it leads to — or write your own."
      rightPane={
        <div>
          <div className="ob-preview-label">Success metrics</div>
          {!northStar ? (
            <p className="ob-preview-empty">
              Set a North Star and supporting metrics to see your KPI tree take
              shape.
            </p>
          ) : (
            <ul className="ob-preview-list">
              <li>
                <strong>North Star:</strong> {northStar}
              </li>
              {previewMetrics.map((m) => (
                <li key={m.name}>{m.name}</li>
              ))}
            </ul>
          )}
        </div>
      }
      onBack={() => router.push("/onboarding/4")}
      onContinue={persist}
      loading={saving}
    >
      <div ref={containerRef}>
        <SuccessMetricsView
          productName={productName}
          industry={industry}
          northStar={northStar}
          northStarDescription={northStarDescription}
          supporting={supporting}
          customMetric={customMetric}
          northStarHints={northStarHints}
          supportingHints={supportingHints}
          errors={errors}
          error={error}
          onChangeNorthStar={(value) => {
            setNorthStar(value)
            clearError("northStar")
          }}
          onChangeNorthStarDescription={setNorthStarDescription}
          onPickNorthStar={setNorthStar}
          onToggleSupporting={toggleSupporting}
          onChangeSupportingDescription={changeSupportingDescription}
          onChangeCustomMetric={setCustomMetric}
          onAddCustom={addCustom}
        />
      </div>
    </InterviewLayout>
  )
}
