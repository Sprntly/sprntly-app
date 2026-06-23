"use client"

import { useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { useFieldValidation } from "../../onboarding/InterviewLayout"
import { OnboardingChrome } from "../../onboarding/OnboardingChrome"
import { useOnboarding } from "../../../context/OnboardingContext"
import { advanceOnboardingStep, updateWorkspace } from "../../../lib/onboarding/store"
import { saveDraft, loadDraft, clearDraft } from "../../../lib/onboarding/useFormDraft"
import { INDUSTRIES, BUSINESS_TYPES } from "../../../lib/onboarding/types"
import { Check, InfoCircle, Plus, Sparkles } from "../../auth/icons"
import {
  buildKpiTreePayloadFromPicks,
  canSavePickedMetrics,
  kpiTreeApi,
  REQUIRED_METRIC_PICKS,
} from "../../../lib/onboarding/kpiTreeApi"

/**
 * Onboarding metrics page (route /onboarding/metrics).
 *
 * REDESIGN (product-approved): the explicit North-Star + supporting-metric
 * split is GONE. The user now picks EXACTLY 3 metrics from a FLAT list of
 * candidates — the North Star is inferred SERVER-SIDE from the three (we never
 * ask). Each candidate is a `{ name, description }` card; selected cards carry
 * the green `.sel` selected state (`aria-selected="true"`). The user can also
 * write their own candidate (it lands selected if there's still room).
 *
 * Candidates are seeded once from (in priority order):
 *   1. a KPI tree already saved on the workspace (its metrics become the pool,
 *      the first 3 pre-selected), else
 *   2. the website-analysis `suggested_metrics`, else
 *   3. business-type defaults (e.g. SaaS — product-curated) merged with a small
 *      industry-tailored fallback pool.
 * Up to 3 of the seeded candidates are pre-selected; the user adjusts to land
 * on exactly 3, which is what "Continue" requires.
 *
 * Industry + business_type stay as ALWAYS-editable dropdowns (pre-filled from
 * the analysis, overridable). On save we persist the confirmed
 * industry/business_type to the company and the 3 picks to the KPI tree
 * (PUT /v1/company/kpi-tree) — north_star is a placeholder until server-side
 * inference ships (see kpiTreeApi.buildKpiTreePayloadFromPicks).
 */

export type MetricCandidate = {
  name: string
  description: string
}

// Industry-tailored fallback candidate pool, used to round out the flat list
// when the website analysis returned no suggestions.
const FALLBACK_CANDIDATES_BY_INDUSTRY: Record<string, string[]> = {
  Healthtech: [
    "Weekly active clinicians",
    "Day-30 active clinicians per deployment",
    "Incremental revenue",
    "Activation rate",
  ],
  "B2B SaaS": [
    "Incremental revenue",
    "Weekly active teams",
    "Activation rate",
    "Conversion rate",
  ],
  B2C: ["Day-30 retention", "DAU/MAU ratio", "Conversion rate", "Weekly active users"],
  Fintech: [
    "Transaction volume",
    "Incremental revenue",
    "Activated accounts",
    "Conversion rate",
  ],
  default: [
    "Weekly active users",
    "Day-30 retention",
    "Incremental revenue",
    "Conversion rate",
  ],
}

// Default candidate metrics by business type, used when the website analysis
// returned no suggestions. SaaS defaults are product-curated.
const DEFAULT_METRICS_BY_BUSINESS_TYPE: Record<string, string[]> = {
  SaaS: ["Incremental revenue", "Number of new subscribers", "Conversion rate"],
}

/** Merge name lists into a deduped (case-insensitive) candidate pool. */
function mergeCandidates(...lists: MetricCandidate[][]): MetricCandidate[] {
  const seen = new Set<string>()
  const out: MetricCandidate[] = []
  for (const list of lists) {
    for (const c of list) {
      const name = c.name.trim()
      if (!name) continue
      const key = name.toLowerCase()
      if (seen.has(key)) continue
      seen.add(key)
      out.push({ name, description: c.description })
    }
  }
  return out
}

export type MetricsSetupViewProps = {
  industry: string
  businessType: string
  /** The full flat list of candidate metrics shown as toggleable cards. */
  candidates: MetricCandidate[]
  /** Names (lowercased keys handled by caller) of the currently-selected picks. */
  selected: string[]
  customMetric: string
  errors: Record<string, string | undefined>
  error: string | null
  onChangeIndustry: (value: string) => void
  onChangeBusinessType: (value: string) => void
  onToggle: (name: string) => void
  onChangeCustomMetric: (value: string) => void
  onAddCustom: () => void
}

/**
 * Pure presentational view (props only, no hooks) so it renders to static
 * markup in tests — the established onboarding View pattern.
 */
export function MetricsSetupView({
  industry,
  businessType,
  candidates,
  selected,
  customMetric,
  errors,
  error,
  onChangeIndustry,
  onChangeBusinessType,
  onToggle,
  onChangeCustomMetric,
  onAddCustom,
}: MetricsSetupViewProps) {
  const selectedSet = new Set(selected.map((s) => s.toLowerCase()))
  const selectedCount = selected.length
  const atLimit = selectedCount >= REQUIRED_METRIC_PICKS

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

      <div className="onb-section" data-field="metrics">
        <div className="onb-section-h">
          Pick your {REQUIRED_METRIC_PICKS} success metrics{" "}
          <span className="opt">— the ones that best capture product value</span>
        </div>

        {errors.metrics && <p className="onb-field-error">{errors.metrics}</p>}

        <div className="metric-pick" id="metricCandidates">
          {candidates.length > 0 ? (
            candidates.map((c, i) => {
              const isSel = selectedSet.has(c.name.toLowerCase())
              // A non-selected card is disabled once we've hit the pick limit;
              // selected cards always stay clickable so they can be toggled off.
              const disabled = !isSel && atLimit
              return (
                <button
                  type="button"
                  key={c.name}
                  className={`mt-target metric-card ${isSel ? "sel" : ""}`}
                  data-metric={c.name}
                  aria-pressed={isSel}
                  aria-selected={isSel}
                  disabled={disabled}
                  onClick={() => onToggle(c.name)}
                  style={{ ["--d" as string]: `${0.04 * (i + 1)}s` }}
                >
                  <span className="mt-ic" aria-hidden>
                    {isSel ? (
                      <Check style={{ width: 13, height: 13 }} />
                    ) : (
                      <Sparkles style={{ width: 12, height: 12 }} />
                    )}
                  </span>
                  <span className="mt-target-name">{c.name}</span>
                  {c.description && (
                    <span className="metric-card-desc">{c.description}</span>
                  )}
                </button>
              )
            })
          ) : (
            <p className="mt-targets-empty">
              No candidate metrics yet — add your own below.
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
              disabled={!customMetric.trim() || atLimit}
            >
              <Plus style={{ width: 13, height: 13 }} aria-hidden /> Add
            </button>
          </div>
        </div>

        <div className="metric-count">
          <span className="mt-ic" aria-hidden>
            <InfoCircle style={{ width: 13, height: 13 }} />
          </span>
          <span>
            <strong>{selectedCount}</strong> of {REQUIRED_METRIC_PICKS} metrics
            selected
            {selectedCount === REQUIRED_METRIC_PICKS
              ? " — ready"
              : selectedCount < REQUIRED_METRIC_PICKS
                ? ` — pick ${REQUIRED_METRIC_PICKS - selectedCount} more`
                : ""}
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
  const [candidates, setCandidates] = useState<MetricCandidate[]>(
    (mdraft?.candidates as MetricCandidate[]) ?? [],
  )
  const [selected, setSelected] = useState<string[]>((mdraft?.selected as string[]) ?? [])
  const [customMetric, setCustomMetric] = useState("")
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Save draft on tab switch only
  useEffect(() => {
    const onHide = () => {
      if (document.hidden)
        saveDraft(DRAFT_KEY, { industry, businessType, candidates, selected })
    }
    document.addEventListener("visibilitychange", onHide)
    return () => document.removeEventListener("visibilitychange", onHide)
  }, [industry, businessType, candidates, selected])

  // Once the user touches the industry/business-type dropdowns we stop
  // overwriting their choice when a late analysis result arrives.
  const [industryTouched, setIndustryTouched] = useState(false)
  const [businessTypeTouched, setBusinessTypeTouched] = useState(false)
  // The candidate pool is seeded exactly once — from a saved KPI tree, else the
  // website-analysis suggestions, else business-type / industry defaults. After
  // the first seed this ref stays true so a late re-render never clobbers the
  // user's edits/selections.
  const candidatesSeeded = useRef(false)

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

  // Hydrate from a KPI tree already saved on the workspace: its metrics become
  // the candidate pool, the first 3 pre-selected. Marks the pool as seeded so
  // the suggestion seed below won't run.
  useEffect(() => {
    if (!workspace) return
    if (candidatesSeeded.current) return
    const tree = workspace.kpi_tree
    const named = tree.metrics.filter((m) => m.name.trim().length > 0)
    if (named.length) {
      candidatesSeeded.current = true
      const pool = mergeCandidates(
        named.map((m) => ({ name: m.name, description: m.description ?? "" })),
      )
      setCandidates(pool)
      setSelected(pool.slice(0, REQUIRED_METRIC_PICKS).map((c) => c.name))
    }
  }, [workspace])

  const suggestedMetrics = websiteAnalysis?.suggested_metrics ?? []

  // Seed the candidate pool from the analysis suggestions (else business-type /
  // industry defaults). Guarded by `candidatesSeeded` so it runs at most once
  // and never clobbers the user's later edits — and skipped entirely if a saved
  // KPI tree already hydrated the pool above. Up to 3 candidates are
  // pre-selected; the user adjusts to land on exactly 3.
  useEffect(() => {
    if (candidatesSeeded.current) return

    const resolvedBusinessType =
      workspace?.business_type || websiteAnalysis?.business_type || businessType
    const resolvedIndustry = workspace?.industry || websiteAnalysis?.industry || industry

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

    // Always offer a pool (analysis + curated defaults + industry fallback) so
    // the user has more than 3 candidates to choose between.
    const pool = mergeCandidates(fromAnalysis, fromBizDefaults, fromIndustryFallback)
    if (pool.length === 0) return
    candidatesSeeded.current = true
    setCandidates(pool)
    setSelected(pool.slice(0, REQUIRED_METRIC_PICKS).map((c) => c.name))
  }, [
    suggestedMetrics,
    businessType,
    industry,
    workspace?.business_type,
    workspace?.industry,
    websiteAnalysis?.business_type,
    websiteAnalysis?.industry,
  ])

  const { errors, validate, clearError, containerRef } = useFieldValidation(
    () => [
      {
        key: "metrics",
        valid: canSavePickedMetrics(selectedAsMetrics(candidates, selected)),
        message: `Pick exactly ${REQUIRED_METRIC_PICKS} metrics to continue.`,
      },
    ],
  )

  // Toggle a candidate in/out of the selection. Selecting is blocked once 3 are
  // already picked; deselecting always works.
  function toggle(name: string) {
    setSelected((prev) => {
      const key = name.toLowerCase()
      if (prev.some((s) => s.toLowerCase() === key)) {
        return prev.filter((s) => s.toLowerCase() !== key)
      }
      if (prev.length >= REQUIRED_METRIC_PICKS) return prev
      clearError("metrics")
      return [...prev, name]
    })
  }

  // Add a custom candidate; it lands selected if there's still room (< 3).
  function addCustom() {
    const m = customMetric.trim()
    if (!m) return
    const key = m.toLowerCase()
    setCandidates((prev) =>
      prev.some((c) => c.name.toLowerCase() === key)
        ? prev
        : [...prev, { name: m, description: "" }],
    )
    setSelected((prev) =>
      prev.some((s) => s.toLowerCase() === key) || prev.length >= REQUIRED_METRIC_PICKS
        ? prev
        : [...prev, m],
    )
    clearError("metrics")
    setCustomMetric("")
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
      // 2) The 3 picks → KPI tree. North Star is inferred server-side; we send
      //    a placeholder north_star (the first pick) until that ships.
      await kpiTreeApi.put(
        buildKpiTreePayloadFromPicks(selectedAsMetrics(candidates, selected)),
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

  const ready = selected.length === REQUIRED_METRIC_PICKS

  return (
    <OnboardingChrome
      step={2}
      saveLabel="Saved · auto-saves"
      title={
        <>
          Set your success <em>metrics.</em>
        </>
      }
      subtitle={`Success metrics anchor the whole workspace. Pick the ${REQUIRED_METRIC_PICKS} that best capture product value — we've predicted your business and drafted a starting set from your website. Sprntly figures out which one is your North Star.`}
      footerMeta={
        ready
          ? `${REQUIRED_METRIC_PICKS} metrics selected — ready to continue`
          : `Pick ${REQUIRED_METRIC_PICKS - selected.length} more to continue`
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
          candidates={candidates}
          selected={selected}
          customMetric={customMetric}
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
          onToggle={toggle}
          onChangeCustomMetric={setCustomMetric}
          onAddCustom={addCustom}
        />
      </div>
    </OnboardingChrome>
  )
}

/** Resolve the selected names to {name, description} pairs from the pool. A
 *  selected name not in the pool (defensive) still maps to a bare entry. */
function selectedAsMetrics(
  candidates: MetricCandidate[],
  selected: string[],
): MetricCandidate[] {
  const byKey = new Map(candidates.map((c) => [c.name.toLowerCase(), c]))
  return selected.map(
    (name) => byKey.get(name.toLowerCase()) ?? { name, description: "" },
  )
}
