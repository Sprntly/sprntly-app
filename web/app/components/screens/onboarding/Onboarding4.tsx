"use client"

import { useEffect, useMemo, useState } from "react"
import { useRouter } from "next/navigation"
import { InterviewLayout, useFieldValidation } from "../../onboarding/InterviewLayout"
import { useOnboarding } from "../../../context/OnboardingContext"
import { advanceOnboardingStep, updateWorkspace } from "../../../lib/onboarding/store"
import { INDUSTRIES, BUSINESS_TYPES } from "../../../lib/onboarding/types"
import type { SuggestedMetric } from "../../../lib/api"
import {
  buildKpiTreePayload,
  canSaveKpiTree,
  kpiTreeApi,
  MAX_PRIMARY_METRICS,
  MAX_SECONDARY_SIGNALS,
  type SupportingMetric,
} from "../../../lib/onboarding/kpiTreeApi"

/**
 * Onboarding page 04 — "Success metrics" (single consolidated step; merges the
 * old KPI-tree + success-metrics pages into one).
 *
 * Renders the website-analysis `suggested_metrics` as SELECTABLE options (each
 * showing metric + description), lets the user add their own {metric,
 * description}, and shows the predicted industry + business_type as ALWAYS
 * editable dropdowns (pre-filled from the analysis; the user can override
 * anytime). On save we persist the confirmed industry/business_type to the
 * company and the selected + custom metrics to the KPI tree
 * (PUT /v1/company/kpi-tree).
 */

const MAX_SUPPORTING = MAX_PRIMARY_METRICS + MAX_SECONDARY_SIGNALS

// Fallback North Star suggestions, tailored loosely by industry, shown when
// the website analysis didn't return suggested metrics.
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

export type MetricsSetupViewProps = {
  industry: string
  businessType: string
  northStar: string
  northStarDescription: string
  northStarHints: string[]
  /** Suggested metrics from the website analysis (may be empty). */
  suggestedMetrics: SuggestedMetric[]
  /** Selected supporting metrics (from suggestions and/or added by hand). */
  supporting: SupportingMetric[]
  customMetric: string
  customDescription: string
  errors: Record<string, string | undefined>
  error: string | null
  onChangeIndustry: (value: string) => void
  onChangeBusinessType: (value: string) => void
  onChangeNorthStar: (value: string) => void
  onChangeNorthStarDescription: (value: string) => void
  onPickNorthStar: (value: string) => void
  onToggleSuggested: (metric: SuggestedMetric) => void
  onChangeSupportingDescription: (metric: string, description: string) => void
  onChangeCustomMetric: (value: string) => void
  onChangeCustomDescription: (value: string) => void
  onAddCustom: () => void
}

/**
 * Pure presentational view (props only, no hooks) so it renders to static
 * markup in tests — the established onboarding View pattern.
 */
export function MetricsSetupView({
  industry,
  businessType,
  northStar,
  northStarDescription,
  northStarHints,
  suggestedMetrics,
  supporting,
  customMetric,
  customDescription,
  errors,
  error,
  onChangeIndustry,
  onChangeBusinessType,
  onChangeNorthStar,
  onChangeNorthStarDescription,
  onPickNorthStar,
  onToggleSuggested,
  onChangeSupportingDescription,
  onChangeCustomMetric,
  onChangeCustomDescription,
  onAddCustom,
}: MetricsSetupViewProps) {
  const selectedNames = supporting.map((m) => m.name)
  const isSelected = (name: string) => selectedNames.includes(name)

  return (
    <>
      {error && <div className="ob-form-error">{error}</div>}

      <div className="ob-predicted-grid">
        <div className="field" data-field="industry">
          <label className="field-label">Industry</label>
          <p className="field-hint">Predicted from your website — change if it&apos;s off.</p>
          <select
            className="input"
            value={industry}
            onChange={(e) => onChangeIndustry(e.target.value)}
            aria-label="Industry"
          >
            {INDUSTRIES.map((i) => (
              <option key={i}>{i}</option>
            ))}
          </select>
        </div>
        <div className="field" data-field="businessType">
          <label className="field-label">Business type</label>
          <p className="field-hint">Predicted from your website — change if it&apos;s off.</p>
          <select
            className="input"
            value={businessType}
            onChange={(e) => onChangeBusinessType(e.target.value)}
            aria-label="Business type"
          >
            {BUSINESS_TYPES.map((b) => (
              <option key={b}>{b}</option>
            ))}
          </select>
        </div>
      </div>

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
          <span className="ob-ns-hints-label">Common for {industry || "your stage"}:</span>
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
        <label className="field-label">Suggested supporting metrics</label>
        {suggestedMetrics.length > 0 ? (
          <>
            <p className="field-hint">
              Drafted from your website — select the ones that fit.
            </p>
            <ul className="ob-suggested-list">
              {suggestedMetrics.map((m) => {
                const sel = isSelected(m.metric)
                return (
                  <li key={m.metric}>
                    <button
                      type="button"
                      className={`ob-suggested-card ${sel ? "selected" : ""}`}
                      aria-pressed={sel}
                      data-metric={m.metric}
                      onClick={() => onToggleSuggested(m)}
                    >
                      <span className="ob-suggested-check" aria-hidden>
                        {sel ? "✓" : "+"}
                      </span>
                      <span className="ob-suggested-body">
                        <span className="ob-suggested-name">{m.metric}</span>
                        {m.description && (
                          <span className="ob-suggested-desc">{m.description}</span>
                        )}
                      </span>
                    </button>
                  </li>
                )
              })}
            </ul>
          </>
        ) : (
          <p className="field-hint ob-no-suggestions">
            No suggestions yet — add your own metrics below.
          </p>
        )}
      </div>

      <div className="field">
        <label className="field-label">Add your own</label>
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
            placeholder="Metric name"
            maxLength={80}
            aria-label="Custom metric name"
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
        <textarea
          className="input ob-metric-desc"
          value={customDescription}
          onChange={(e) => onChangeCustomDescription(e.target.value)}
          placeholder="Describe what this metric means and why it matters (optional)"
          rows={2}
          maxLength={400}
          aria-label="Custom metric description"
        />
      </div>

      <div className="field">
        <p className="ob-metric-count">
          {supporting.length} supporting metric{supporting.length === 1 ? "" : "s"} selected
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
        .ob-predicted-grid {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 14px;
        }
        @media (max-width: 560px) {
          .ob-predicted-grid {
            grid-template-columns: 1fr;
          }
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
        .ob-suggested-list {
          list-style: none;
          margin: 8px 0 0;
          padding: 0;
          display: flex;
          flex-direction: column;
          gap: 8px;
        }
        .ob-suggested-card {
          display: flex;
          align-items: flex-start;
          gap: 10px;
          width: 100%;
          text-align: left;
          padding: 12px 14px;
          border: 1px solid var(--line);
          border-radius: 10px;
          background: var(--surface);
          cursor: pointer;
          transition: border-color 0.15s, background 0.15s;
        }
        .ob-suggested-card:hover {
          border-color: var(--accent);
        }
        .ob-suggested-card.selected {
          border-color: var(--accent);
          background: var(--accent-soft, rgba(15, 111, 78, 0.06));
        }
        .ob-suggested-check {
          font-weight: 600;
          color: var(--accent);
          flex-shrink: 0;
        }
        .ob-suggested-body {
          display: flex;
          flex-direction: column;
          gap: 2px;
        }
        .ob-suggested-name {
          font-size: 14px;
          font-weight: 600;
        }
        .ob-suggested-desc {
          font-size: 12.5px;
          color: var(--ink-3);
          line-height: 1.4;
        }
        .ob-custom-metric {
          display: flex;
          gap: 8px;
        }
        .ob-custom-metric :global(.input) {
          flex: 1;
        }
        .ob-metric-count {
          font-size: 12px;
          color: var(--muted);
          margin: 0;
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

export function Onboarding4() {
  const { workspace, setWorkspace, websiteAnalysis, loading } = useOnboarding()
  const router = useRouter()
  const [industry, setIndustry] = useState<string>(INDUSTRIES[0])
  const [businessType, setBusinessType] = useState<string>(BUSINESS_TYPES[0])
  const [northStar, setNorthStar] = useState("")
  const [northStarDescription, setNorthStarDescription] = useState("")
  const [supporting, setSupporting] = useState<SupportingMetric[]>([])
  const [customMetric, setCustomMetric] = useState("")
  const [customDescription, setCustomDescription] = useState("")
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Once the user touches the industry/business-type dropdowns we stop
  // overwriting their choice when a late analysis result arrives.
  const [industryTouched, setIndustryTouched] = useState(false)
  const [businessTypeTouched, setBusinessTypeTouched] = useState(false)

  // Seed industry / business type from the saved company first, then from the
  // website analysis (which may arrive later) — but never clobber a value the
  // user has already changed by hand.
  useEffect(() => {
    if (industryTouched) return
    const fromWs = workspace?.industry
    const fromAnalysis = websiteAnalysis?.industry
    const next = fromWs || fromAnalysis
    if (next && INDUSTRIES.includes(next as (typeof INDUSTRIES)[number])) {
      setIndustry(next)
    } else if (next) {
      // Inferred value outside our option list → fall back to "Other".
      setIndustry("Other")
    }
  }, [workspace?.industry, websiteAnalysis?.industry, industryTouched])

  useEffect(() => {
    if (businessTypeTouched) return
    const fromWs = workspace?.business_type
    const fromAnalysis = websiteAnalysis?.business_type
    const next = fromWs || fromAnalysis
    if (next && BUSINESS_TYPES.includes(next as (typeof BUSINESS_TYPES)[number])) {
      setBusinessType(next)
    }
  }, [workspace?.business_type, websiteAnalysis?.business_type, businessTypeTouched])

  // Hydrate any KPI tree already saved on the workspace.
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

  const suggestedMetrics = websiteAnalysis?.suggested_metrics ?? []
  const northStarHints =
    NORTH_STAR_SUGGESTIONS[industry] ?? NORTH_STAR_SUGGESTIONS.default

  const { errors, validate, clearError, containerRef } = useFieldValidation(
    () => [
      {
        key: "northStar",
        valid: canSaveKpiTree(northStar, supporting),
        message: "Set a North Star metric to anchor your KPI tree.",
      },
    ],
  )

  function toggleSuggested(metric: SuggestedMetric) {
    setSupporting((prev) => {
      if (prev.some((m) => m.name === metric.metric)) {
        return prev.filter((m) => m.name !== metric.metric)
      }
      if (prev.length >= MAX_SUPPORTING) return prev
      return [...prev, { name: metric.metric, description: metric.description ?? "" }]
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
    setSupporting((prev) => [...prev, { name: m, description: customDescription.trim() }])
    setCustomMetric("")
    setCustomDescription("")
  }

  async function persist() {
    if (!workspace) return
    setError(null)
    if (!validate().ok) return
    setSaving(true)
    try {
      // 1) Confirmed industry / business type → company.
      await updateWorkspace(workspace.id, {
        industry,
        business_type: businessType,
      })
      // 2) Selected + custom metrics → KPI tree (canonical config entity).
      await kpiTreeApi.put(
        buildKpiTreePayload(northStar, northStarDescription, supporting),
      )
      const updated = await advanceOnboardingStep(workspace.id, 5)
      const product = updated.product ?? workspace.product
      setWorkspace({ ...updated, product })
      router.push("/onboarding/5")
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
      step={4}
      eyebrow="Saved · auto-saves after every step"
      title="Set your success metrics"
      agentMessage="Success metrics anchor the whole workspace. I've drafted suggestions and predicted your industry from your website — confirm what fits, add your own, and set the North Star they all ladder up to."
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
      onBack={() => router.push("/onboarding/3")}
      onContinue={persist}
      loading={saving}
    >
      <div ref={containerRef}>
        <MetricsSetupView
          industry={industry}
          businessType={businessType}
          northStar={northStar}
          northStarDescription={northStarDescription}
          northStarHints={northStarHints}
          suggestedMetrics={suggestedMetrics}
          supporting={supporting}
          customMetric={customMetric}
          customDescription={customDescription}
          errors={errors}
          error={error}
          onChangeIndustry={(value) => {
            setIndustryTouched(true)
            setIndustry(value)
          }}
          onChangeBusinessType={(value) => {
            setBusinessTypeTouched(true)
            setBusinessType(value)
          }}
          onChangeNorthStar={(value) => {
            setNorthStar(value)
            clearError("northStar")
          }}
          onChangeNorthStarDescription={setNorthStarDescription}
          onPickNorthStar={(value) => {
            setNorthStar(value)
            clearError("northStar")
          }}
          onToggleSuggested={toggleSuggested}
          onChangeSupportingDescription={changeSupportingDescription}
          onChangeCustomMetric={setCustomMetric}
          onChangeCustomDescription={setCustomDescription}
          onAddCustom={addCustom}
        />
      </div>
    </InterviewLayout>
  )
}
