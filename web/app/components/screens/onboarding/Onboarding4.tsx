"use client"

import { useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { useFieldValidation } from "../../onboarding/InterviewLayout"
import { OnboardingChrome } from "../../onboarding/OnboardingChrome"
import { useOnboarding } from "../../../context/OnboardingContext"
import { advanceOnboardingStep, updateWorkspace } from "../../../lib/onboarding/store"
import { INDUSTRIES, BUSINESS_TYPES } from "../../../lib/onboarding/types"
import type { SuggestedMetric } from "../../../lib/api"
import { Check, InfoCircle, Plus, Sparkles, Trash } from "../../auth/icons"
import {
  buildKpiTreePayload,
  canSaveKpiTree,
  kpiTreeApi,
  MAX_PRIMARY_METRICS,
  MAX_SECONDARY_SIGNALS,
  type SupportingMetric,
} from "../../../lib/onboarding/kpiTreeApi"

/**
 * Onboarding metrics page (route /onboarding/2 in the new flow; component name
 * kept as Onboarding4 to avoid churning the other-PR-owned screens). Restyled
 * to the v4 `.metric-tree` design.
 *
 * The website-analysis `suggested_metrics` are PRE-SELECTED on load (all of
 * them seed `supporting` once, via a ref guard mirroring the industry/business
 * touched-guards) and render as selectable suggestion chips. The selected
 * supporting metrics live INSIDE the metric-tree as `.mt-targets` branching off
 * the North-Star `.mt-source` — each target shows the metric name, an editable
 * description, and a delete control. The user can also add their own
 * {metric, description} via `.metric-other`. Industry + business_type show as
 * ALWAYS-editable dropdowns (pre-filled from the analysis; the user can
 * override anytime, guarded by a `touched` flag). On save we persist the
 * confirmed industry/business_type to the company and the supporting metrics to
 * the KPI tree (PUT /v1/company/kpi-tree).
 *
 * The analysis is now produced by the BLOCKING `/onboarding/analyzing`
 * interstitial that precedes this page, so by the time we render the result is
 * already on context (or null → graceful manual fallback).
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
  onRemoveSupporting: (metric: string) => void
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
  onRemoveSupporting,
  onChangeCustomMetric,
  onChangeCustomDescription,
  onAddCustom,
}: MetricsSetupViewProps) {
  const selectedNames = supporting.map((m) => m.name)
  const isSelected = (name: string) => selectedNames.includes(name)

  return (
    <>
      {error && <div className="onb-form-error">{error}</div>}

      <div className="onb-section">
        <div className="onb-section-h">
          Your business <span className="opt">— predicted from your website, edit if it&apos;s off</span>
        </div>
        <div className="form-grid">
          <div className="field" data-field="industry">
            <div className="field-l">Industry</div>
            <select
              className="inp"
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
            <div className="field-l">Business type</div>
            <select
              className="inp"
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
      </div>

      <div className="onb-section" data-field="northStar">
        <div className="onb-section-h">
          Primary metric <span className="opt">— your North Star, required</span>
        </div>
        <input
          className={`inp ${errors.northStar ? "has-error" : ""}`}
          value={northStar}
          onChange={(e) => onChangeNorthStar(e.target.value)}
          placeholder="The one metric that best captures product value"
        />
        {errors.northStar && <p className="onb-field-error">{errors.northStar}</p>}
        <div className="metric-other-l" style={{ marginTop: 12 }}>
          Common for {industry || "your stage"}
        </div>
        <div className="onb-chip-row">
          {northStarHints.map((h) => (
            <button
              key={h}
              type="button"
              className="onb-chip"
              onClick={() => onPickNorthStar(h)}
            >
              {h}
            </button>
          ))}
        </div>
        <textarea
          className="inp"
          style={{ marginTop: 12 }}
          value={northStarDescription}
          onChange={(e) => onChangeNorthStarDescription(e.target.value)}
          placeholder="Describe what this metric means and why it matters (context for goal-fit scoring)"
          rows={2}
          maxLength={400}
        />
      </div>

      <div className="onb-section">
        <div className="onb-section-h">
          Supporting metrics <span className="opt">— pick what fits, or write your own</span>
        </div>

        <div className="metric-tree">
          <div className="mt-source">
            <div className="mt-source-dot" />
            <div className="mt-source-lbl">Primary leads to…</div>
          </div>

          {/* Selected supporting metrics ARE the tree targets: name + editable
              description + delete, branching off the North-Star source. */}
          {supporting.length > 0 ? (
            <div className="mt-targets mt-targets-cards" id="supportingMetrics">
              {supporting.map((m, i) => (
                <div
                  key={m.name}
                  className="mt-target"
                  data-metric={m.name}
                  style={{ ["--d" as string]: `${0.05 * (i + 1)}s` }}
                >
                  <button
                    type="button"
                    className="mt-target-del"
                    aria-label={`Remove ${m.name}`}
                    onClick={() => onRemoveSupporting(m.name)}
                  >
                    <Trash style={{ width: 14, height: 14 }} aria-hidden />
                  </button>
                  <div className="mt-target-name">
                    <span className="mt-ic" aria-hidden>
                      <Sparkles style={{ width: 12, height: 12 }} />
                    </span>
                    {m.name}
                  </div>
                  <textarea
                    className="inp"
                    value={m.description}
                    onChange={(e) => onChangeSupportingDescription(m.name, e.target.value)}
                    placeholder="Describe what this metric means and why it matters"
                    rows={2}
                    maxLength={400}
                    aria-label={`Description for ${m.name}`}
                  />
                </div>
              ))}
            </div>
          ) : (
            <p className="mt-targets-empty">
              {suggestedMetrics.length > 0
                ? "No supporting metrics yet — pick a suggestion below or add your own."
                : "No suggestions yet — add your own metrics below."}
            </p>
          )}

          {/* Suggestion chips: selecting toggles a metric in/out of the targets
              above. A chip is "sel" when its metric is currently a target. */}
          {suggestedMetrics.length > 0 && (
            <div className="mt-targets" id="suggestedMetrics" style={{ marginTop: 16 }}>
              {suggestedMetrics.map((m, i) => {
                const sel = isSelected(m.metric)
                return (
                  <button
                    key={m.metric}
                    type="button"
                    className={`metric mt-suggested ${sel ? "sel" : ""}`}
                    style={{ ["--d" as string]: `${0.05 * (i + 1)}s` }}
                    aria-pressed={sel}
                    data-metric={m.metric}
                    title={m.description || undefined}
                    onClick={() => onToggleSuggested(m)}
                  >
                    <span className="mt-ic" aria-hidden>
                      {sel ? (
                        <Check style={{ width: 12, height: 12 }} />
                      ) : (
                        <Sparkles style={{ width: 12, height: 12 }} />
                      )}
                    </span>
                    {m.metric}
                  </button>
                )
              })}
            </div>
          )}
        </div>

        <div className="metric-other">
          <div className="metric-other-l">Or write your own</div>
          <div className="metric-other-row">
            <input
              className="inp"
              value={customMetric}
              onChange={(e) => onChangeCustomMetric(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault()
                  onAddCustom()
                }
              }}
              placeholder="e.g., Net new enterprise logos per quarter"
              maxLength={80}
              aria-label="Custom metric name"
            />
            <button
              type="button"
              className="btn btn-secondary"
              onClick={onAddCustom}
              disabled={!customMetric.trim() || supporting.length >= MAX_SUPPORTING}
            >
              <Plus style={{ width: 13, height: 13 }} aria-hidden /> Add
            </button>
          </div>
          <textarea
            className="inp"
            style={{ marginTop: 10 }}
            value={customDescription}
            onChange={(e) => onChangeCustomDescription(e.target.value)}
            placeholder="Describe what this metric means and why it matters (optional)"
            rows={2}
            maxLength={400}
            aria-label="Custom metric description"
          />
        </div>

        <div className="metric-count">
          <span className="mt-ic" aria-hidden>
            <InfoCircle style={{ width: 13, height: 13 }} />
          </span>
          <span>
            <strong>{supporting.length}</strong> supporting metric
            {supporting.length === 1 ? "" : "s"} selected
          </span>
        </div>
      </div>
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
  // The supporting-metrics list is seeded exactly once — either from a KPI tree
  // already saved on the workspace, or (failing that) from the website-analysis
  // suggestions (all pre-selected). After the first seed this ref stays true so
  // a late re-render never re-adds a metric the user has since edited/deleted.
  const supportingSeeded = useRef(false)

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
      // A previously-saved tree is the source of truth — adopt it and mark the
      // supporting list as seeded so the suggestion seed below won't run.
      supportingSeeded.current = true
      setSupporting(
        tree.metrics
          .filter((m) => m.name)
          .map((m) => ({ name: m.name, description: m.description ?? "" })),
      )
    }
  }, [workspace])

  const suggestedMetrics = websiteAnalysis?.suggested_metrics ?? []

  // Pre-select ALL website-analysis suggestions on load: the user starts with
  // every suggested metric already in their supporting list. Guarded by
  // `supportingSeeded` (mirrors the industry/business-type touched-guards) so it
  // runs at most once and never clobbers the user's later edits/deletions — and
  // skipped entirely if a saved KPI tree already hydrated the list above. With
  // no suggestions the list stays empty and the tree shows its empty state.
  useEffect(() => {
    if (supportingSeeded.current) return
    if (suggestedMetrics.length === 0) return
    supportingSeeded.current = true
    setSupporting(
      suggestedMetrics
        .filter((m) => m.metric)
        .slice(0, MAX_SUPPORTING)
        .map((m) => ({ name: m.metric, description: m.description ?? "" })),
    )
  }, [suggestedMetrics])
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

  // Remove a supporting metric. Because the suggestion chips derive their
  // selected state from `supporting`, removing a metric that matches a
  // suggestion also un-selects its chip (re-addable); a custom metric just
  // drops. The `.metric-count` stays in sync since it reads `supporting.length`.
  function removeSupporting(metric: string) {
    setSupporting((prev) => prev.filter((m) => m.name !== metric))
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
      // Next numbered step is the optimizing-for page (route 3).
      const updated = await advanceOnboardingStep(workspace.id, 3)
      const product = updated.product ?? workspace.product
      setWorkspace({ ...updated, product })
      router.push("/onboarding/3")
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save your metrics.")
    } finally {
      setSaving(false)
    }
  }

  // Redirect when there's no workspace to anchor the step. Done in an effect
  // (not during render) so navigation never fires as a render side-effect —
  // that path surfaces in production as a client-side exception / error
  // boundary. Render returns the loading shell until the redirect lands.
  useEffect(() => {
    if (!loading && !workspace) router.replace("/onboarding/1")
  }, [loading, workspace, router])

  if (loading || !workspace) return <div className="onb-shell">Loading…</div>

  return (
    <OnboardingChrome
      step={2}
      saveLabel="Saved · auto-saves"
      title={
        <>
          Set your success <em>metrics.</em>
        </>
      }
      subtitle="Success metrics anchor the whole workspace. We've drafted suggestions and predicted your business from your website — confirm what fits, add your own, and set the North Star they all ladder up to."
      footerMeta={
        northStar
          ? `North Star + ${supporting.length} supporting metric${supporting.length === 1 ? "" : "s"} captured`
          : "Set a North Star to continue"
      }
      onBack={() => router.push("/onboarding/1")}
      onContinue={persist}
      continueDisabled={saving}
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
          onRemoveSupporting={removeSupporting}
          onChangeCustomMetric={setCustomMetric}
          onChangeCustomDescription={setCustomDescription}
          onAddCustom={addCustom}
        />
      </div>
    </OnboardingChrome>
  )
}
