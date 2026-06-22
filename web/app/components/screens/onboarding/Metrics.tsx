"use client"

import { useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { useFieldValidation } from "../../onboarding/InterviewLayout"
import { OnboardingChrome } from "../../onboarding/OnboardingChrome"
import { useOnboarding } from "../../../context/OnboardingContext"
import { advanceOnboardingStep, updateWorkspace } from "../../../lib/onboarding/store"
import { saveDraft, loadDraft, clearDraft } from "../../../lib/onboarding/useFormDraft"
import { INDUSTRIES, BUSINESS_TYPES } from "../../../lib/onboarding/types"
import { InfoCircle, Plus, Sparkles, Trash } from "../../auth/icons"
import {
  buildKpiTreePayload,
  canSaveKpiTree,
  kpiTreeApi,
  MAX_PRIMARY_METRICS,
  MAX_SECONDARY_SIGNALS,
  type SupportingMetric,
} from "../../../lib/onboarding/kpiTreeApi"

/**
 * Onboarding metrics page (route /onboarding/metrics in the new flow). Restyled
 * to the v4 `.metric-tree` design.
 *
 * The website-analysis `suggested_metrics` are PRE-SEEDED on load (all of them
 * seed `supporting` once, via a ref guard mirroring the industry/business
 * touched-guards). The seeded supporting metrics live INSIDE the metric-tree as
 * `.mt-targets` branching off the North-Star `.mt-source` — each target shows
 * the metric name, an editable description, and a delete control. The user can
 * also add their own {metric, description} via `.metric-other` ("write your
 * own"), and re-add any deleted metric the same way. Industry + business_type
 * show as
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
    "Incremental revenue",
  ],
  "B2B SaaS": ["Incremental revenue", "Weekly active teams", "Activation rate"],
  B2C: ["Day-30 retention", "DAU/MAU ratio", "Conversion rate"],
  Fintech: ["Transaction volume", "Incremental revenue", "Activated accounts"],
  default: ["Weekly active users", "Day-30 retention", "Incremental revenue"],
}

// Default suggested supporting metrics by business type, used when the website
// analysis returned no suggestions. SaaS defaults are product-curated.
const DEFAULT_METRICS_BY_BUSINESS_TYPE: Record<string, string[]> = {
  SaaS: ["Incremental revenue", "Number of new subscribers", "Conversion rate"],
}

export type MetricsSetupViewProps = {
  industry: string
  businessType: string
  northStar: string
  northStarDescription: string
  northStarHints: string[]
  /** Supporting metrics (pre-seeded from the website analysis and/or added by hand). */
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
  onChangeSupportingDescription,
  onRemoveSupporting,
  onChangeCustomMetric,
  onChangeCustomDescription,
  onAddCustom,
}: MetricsSetupViewProps) {
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
                  className="mt-target sel"
                  data-metric={m.name}
                  aria-selected="true"
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
              No supporting metrics yet — add your own below.
            </p>
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

export function Metrics() {
  const DRAFT_KEY = "metrics"
  const { workspace, setWorkspace, websiteAnalysis, loading } = useOnboarding()
  const router = useRouter()
  const mdraft = loadDraft(DRAFT_KEY)
  const [industry, setIndustry] = useState<string>((mdraft?.industry as string) ?? INDUSTRIES[0])
  const [businessType, setBusinessType] = useState<string>((mdraft?.businessType as string) ?? BUSINESS_TYPES[0])
  const [northStar, setNorthStar] = useState((mdraft?.northStar as string) ?? "")
  const [northStarDescription, setNorthStarDescription] = useState((mdraft?.northStarDescription as string) ?? "")
  const [supporting, setSupporting] = useState<SupportingMetric[]>((mdraft?.supporting as SupportingMetric[]) ?? [])
  const [customMetric, setCustomMetric] = useState("")
  const [customDescription, setCustomDescription] = useState("")
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Save draft on tab switch only
  useEffect(() => {
    const onHide = () => {
      if (document.hidden) saveDraft(DRAFT_KEY, { industry, businessType, northStar, northStarDescription, supporting })
    }
    document.addEventListener("visibilitychange", onHide)
    return () => document.removeEventListener("visibilitychange", onHide)
  }, [industry, businessType, northStar, northStarDescription, supporting])
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
    if (suggestedMetrics.length > 0) {
      supportingSeeded.current = true
      setSupporting(
        suggestedMetrics
          .filter((m) => m.metric)
          .slice(0, MAX_SUPPORTING)
          .map((m) => ({ name: m.metric, description: m.description ?? "" })),
      )
      return
    }
    // No analysis suggestions → fall back to the business-type defaults (e.g.
    // SaaS → Incremental revenue, Number of new subscribers, Conversion rate).
    // Guarded by the same `supportingSeeded` ref so a user's later edits stick.
    // Resolve the business type from the saved workspace / analysis (which is
    // what the dropdown also settles to), not the local state — that local
    // value starts at BUSINESS_TYPES[0] and is only corrected by an effect, so
    // reading it here would race the seed and mis-key the defaults.
    const resolvedBusinessType =
      workspace?.business_type || websiteAnalysis?.business_type || businessType
    const defaults = DEFAULT_METRICS_BY_BUSINESS_TYPE[resolvedBusinessType]
    if (defaults && defaults.length > 0) {
      supportingSeeded.current = true
      setSupporting(
        defaults.slice(0, MAX_SUPPORTING).map((name) => ({ name, description: "" })),
      )
    }
  }, [suggestedMetrics, businessType, workspace?.business_type, websiteAnalysis?.business_type])
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

  function changeSupportingDescription(metric: string, description: string) {
    setSupporting((prev) =>
      prev.map((m) => (m.name === metric ? { ...m, description } : m)),
    )
  }

  // Remove a supporting metric from the tree. A removed metric can always be
  // re-added by hand via "write your own". The `.metric-count` stays in sync
  // since it reads `supporting.length`.
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
      clearDraft(DRAFT_KEY)
      // Next numbered step is connectors (index 3 in ONBOARDING_STEP_SLUGS).
      const updated = await advanceOnboardingStep(workspace.id, 3)
      const product = updated.product ?? workspace.product
      setWorkspace({ ...updated, product })
      router.push("/onboarding/connectors")
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
    if (!loading && !workspace) router.replace("/onboarding/business-info")
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
      subtitle="Success metrics anchor the whole workspace. We've drafted a starting set and predicted your business from your website — edit or remove what doesn't fit, add your own, and set the North Star they all ladder up to."
      footerMeta={
        northStar
          ? `North Star + ${supporting.length} supporting metric${supporting.length === 1 ? "" : "s"} captured`
          : "Set a North Star to continue"
      }
      onBack={() => router.push("/onboarding/business-info")}
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
