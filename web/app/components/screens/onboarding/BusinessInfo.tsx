"use client"

import { useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "../../../lib/auth"
import { useFieldValidation } from "../../onboarding/InterviewLayout"
import { OnboardingChrome } from "../../onboarding/OnboardingChrome"
import { useOnboarding } from "../../../context/OnboardingContext"
import {
  validateProductWebsite,
  normalizeProductWebsite,
} from "../../../lib/onboarding/product-helpers"
import { INDUSTRIES, BUSINESS_TYPES } from "../../../lib/onboarding/types"
import {
  createWorkspace,
  updateWorkspace,
  upsertPrimaryProduct,
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

const DRAFT_KEY = "business-info"

/**
 * onb1 picks EXACTLY this many success metrics. The design's onb1 card reads
 * "pick up to 3" / "3 of 3", so the picker pre-selects 3, blocks a 4th (you
 * deselect to swap), and Continue requires exactly 3. This is intentionally
 * stricter than the shared 3–5 `MIN/MAX_METRIC_PICKS` used by the (now dormant,
 * unrouted) standalone metrics page — onb1 owns its own exact-3 rule.
 */
const ONB1_METRIC_PICKS = 3

/** onb1 is satisfiable iff EXACTLY ONB1_METRIC_PICKS metrics are named/picked. */
function canSaveOnb1Metrics(picked: SupportingMetric[]): boolean {
  return picked.filter((m) => m.name.trim().length > 0).length === ONB1_METRIC_PICKS
}

/**
 * Onboarding step 01 — "Tell us about your product" (design scene onb1).
 *
 * The 5-step redesign COMBINES the old business-info + metrics pages onto one
 * screen: the PM names their company + primary product (+ optional website and
 * tech stack) AND picks 3–5 success metrics, exactly as the design's onb1 card
 * does. Company stage and team size are NOT collected here — that context is
 * captured later via the business-context step.
 *
 * Flow:
 *   1. The metric picker is seeded from a saved KPI tree, else the website
 *      analysis suggestions, else business-type / industry defaults — the same
 *      seeding logic the standalone metrics page used (now lifted into the
 *      shared Metrics view + helpers).
 *   2. On Continue we (a) persist the workspace + primary product, (b) PUT the
 *      3–5 picks to the KPI tree, (c) kick off the website analysis in the
 *      BACKGROUND (fire-and-forget — no interstitial), then navigate straight to
 *      the next numbered step (workspace).
 *
 * The website analysis runs server-side and lands on the onboarding context for
 * the later business-context step to read; here we only seed the picker from
 * whatever analysis result is already available so the PM sees sensible
 * candidates immediately.
 */
export function BusinessInfo() {
  const auth = useAuth()
  const { workspace, refresh, setWorkspace, websiteAnalysis, startWebsiteAnalysis, loading } =
    useOnboarding()
  const router = useRouter()
  // Restore draft from localStorage (survives tab switches)
  const draft = loadDraft(DRAFT_KEY)
  const [companyName, setCompanyName] = useState((draft?.companyName as string) ?? "")
  const [productName, setProductName] = useState((draft?.productName as string) ?? "")
  const [productWebsite, setProductWebsite] = useState((draft?.productWebsite as string) ?? "")

  // ── metrics picker state (combined onto this screen) ───────────────────────
  // industry / business_type are RESOLVED here (from the saved workspace, then
  // any website analysis) purely to SEED the metric candidate pool. They are no
  // longer editable on onb1 — the predicted-industry / business-type dropdowns
  // (and the tech-stack chips) moved to the business-context step. We still seed
  // from them so the metric candidates match the company's shape.
  const resolvedIndustry =
    workspace?.industry || websiteAnalysis?.industry || INDUSTRIES[0]
  const resolvedBusinessType =
    workspace?.business_type || websiteAnalysis?.business_type || BUSINESS_TYPES[0]
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

  function flashLimitWarning() {
    setLimitWarning(`You can pick up to ${ONB1_METRIC_PICKS} metrics — deselect one to swap.`)
    setLimitNonce((n) => n + 1)
  }
  useEffect(() => {
    if (!limitWarning) return
    const t = setTimeout(() => setLimitWarning(null), 3500)
    return () => clearTimeout(t)
  }, [limitWarning, limitNonce])

  // Seed from workspace on first load (only if no draft exists)
  useEffect(() => {
    if (!workspace) return
    if (draft) return // draft takes priority — user already typed something
    setCompanyName(workspace.display_name)
    setProductName(workspace.product?.name ?? workspace.display_name)
    setProductWebsite(workspace.product?.website ?? "")
  }, [workspace]) // eslint-disable-line react-hooks/exhaustive-deps

  // Save draft on visibility change (tab switch / minimize) — not on every keystroke
  useEffect(() => {
    const onHide = () => {
      if (document.hidden)
        saveDraft(DRAFT_KEY, {
          companyName,
          productName,
          productWebsite,
          candidates,
          selected,
        })
    }
    document.addEventListener("visibilitychange", onHide)
    return () => document.removeEventListener("visibilitychange", onHide)
  }, [companyName, productName, productWebsite, candidates, selected])

  // Hydrate the picker from a KPI tree already saved on the workspace.
  useEffect(() => {
    if (!workspace) return
    if (candidatesSeeded.current) return
    const named = workspace.kpi_tree.metrics.filter((m) => m.name.trim().length > 0)
    if (named.length) {
      candidatesSeeded.current = true
      const pool = mergeCandidates(
        named.map((m) => ({ name: m.name, description: m.description ?? "" })),
      )
      setCandidates(pool)
      setSelected(pool.slice(0, ONB1_METRIC_PICKS).map((c) => c.name))
    }
  }, [workspace])

  const suggestedMetrics = websiteAnalysis?.suggested_metrics ?? []

  // Seed the candidate pool from analysis suggestions, else business-type /
  // industry defaults (resolved from the workspace / analysis above). Runs at
  // most once (candidatesSeeded guard).
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
    setSelected(pool.slice(0, ONB1_METRIC_PICKS).map((c) => c.name))
  }, [suggestedMetrics, resolvedBusinessType, resolvedIndustry])

  const { errors, validate, clearError, containerRef } = useFieldValidation(() => [
    {
      key: "companyName",
      valid: companyName.trim().length > 0,
      message: "Enter your company name.",
    },
    {
      key: "productName",
      valid: productName.trim().length > 0,
      message: "Enter your primary product name.",
    },
    {
      key: "metrics",
      valid: canSaveOnb1Metrics(selectedAsMetrics(candidates, selected)),
      message: `Pick exactly ${ONB1_METRIC_PICKS} metrics to continue.`,
    },
  ])

  function toggle(name: string) {
    const key = name.toLowerCase()
    const isSelected = selected.some((s) => s.toLowerCase() === key)
    if (!isSelected && selected.length >= ONB1_METRIC_PICKS) {
      flashLimitWarning()
      return
    }
    setSelected((prev) => {
      if (prev.some((s) => s.toLowerCase() === key)) {
        return prev.filter((s) => s.toLowerCase() !== key)
      }
      if (prev.length >= ONB1_METRIC_PICKS) return prev
      clearError("metrics")
      return [...prev, name]
    })
  }

  function addCustom() {
    const m = customMetric.trim()
    if (!m) return
    const key = m.toLowerCase()
    setCandidates((prev) =>
      prev.some((c) => c.name.toLowerCase() === key) ? prev : [...prev, { name: m, description: "" }],
    )
    const alreadySelected = selected.some((s) => s.toLowerCase() === key)
    if (!alreadySelected && selected.length >= ONB1_METRIC_PICKS) {
      flashLimitWarning()
      setCustomMetric("")
      return
    }
    setSelected((prev) =>
      prev.some((s) => s.toLowerCase() === key) || prev.length >= ONB1_METRIC_PICKS
        ? prev
        : [...prev, m],
    )
    clearError("metrics")
    setCustomMetric("")
  }

  async function save(andContinue: boolean) {
    if (auth.kind !== "authed") return
    setError(null)
    if (andContinue && !validate().ok) return
    const websiteErr = validateProductWebsite(productWebsite)
    if (websiteErr) {
      setError(websiteErr)
      return
    }
    const website = normalizeProductWebsite(productWebsite)
    setSaving(true)
    try {
      // onb1 captures company + product + website + the metric picks only. The
      // tech stack and predicted industry / business type are confirmed later in
      // the business-context step, so we no longer write them here. (industry /
      // business_type are still auto-drafted server-side from the website by the
      // background analysis kicked off on Continue below.)
      const companyPayload = { companyName, productName, productWebsite: website }
      let ws = workspace
      if (workspace) {
        const updated = await updateWorkspace(workspace.id, {
          display_name: companyPayload.companyName.trim(),
          // The next numbered step is workspace (route 2). The analyzing
          // interstitial is unnumbered, so we never persist its route as a
          // resume target.
          onboarding_step: andContinue ? 2 : workspace.onboarding_step,
        })
        const product = await upsertPrimaryProduct(workspace.id, {
          name: companyPayload.productName,
          website: companyPayload.productWebsite,
        })
        ws = { ...updated, product }
        setWorkspace(ws)
      } else {
        const created = await createWorkspace({
          ...companyPayload,
          userId: auth.user.id,
        })
        ws = created
        setWorkspace(created)
      }
      // Persist the metric picks to the KPI tree; the server infers which pick is
      // the North Star (PUT /v1/company/kpi-tree/from-selection).
      const picks: SupportingMetric[] = selectedAsMetrics(candidates, selected)
      if (canSaveOnb1Metrics(picks)) {
        await kpiTreeApi.putFromSelection(buildSelectionPayload(picks))
      }
      clearDraft(DRAFT_KEY)
      if (andContinue) {
        // Kick off the website analysis in the BACKGROUND (no interstitial) and
        // go straight to the next numbered step. The job runs server-side and
        // the onboarding provider outlives this navigation, so the result lands
        // on context for the business-context step to read — the PM never waits
        // on a "Gathering information…" loader.
        if (ws) startWebsiteAnalysis(ws.product?.website ?? website, ws.id)
        router.push("/onboarding/workspace")
      } else {
        await refresh()
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save workspace.")
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <div className="onb-shell">Loading…</div>

  const ready = selected.length === ONB1_METRIC_PICKS

  return (
    <OnboardingChrome
      step={1}
      saveLabel="Saved · auto-saves"
      title={
        <>
          Tell us about your <em>product.</em>
        </>
      }
      subtitle="A name and your success metrics anchor the whole workspace. You'll add the full description in Settings."
      footerMeta={
        ready
          ? `${selected.length} of ${ONB1_METRIC_PICKS} metrics selected — ready to continue`
          : `Pick ${Math.max(ONB1_METRIC_PICKS - selected.length, 0)} more metric${
              ONB1_METRIC_PICKS - selected.length === 1 ? "" : "s"
            } to continue`
      }
      onContinue={() => save(true)}
      continueDisabled={saving}
      loading={saving}
    >
      <div ref={containerRef}>
        {error && <div className="onb-form-error">{error}</div>}

        <div className="form-grid">
          <div className="field full" data-field="companyName">
            <div className="field-l">
              Company name <span className="req">*</span>
            </div>
            <input
              className={`inp ${errors.companyName ? "has-error" : ""}`}
              value={companyName}
              onChange={(e) => {
                setCompanyName(e.target.value)
                clearError("companyName")
              }}
              maxLength={100}
              placeholder="Legal or brand name of your organization"
            />
            {errors.companyName && <p className="onb-field-error">{errors.companyName}</p>}
          </div>

          <div className="field full" data-field="productName">
            <div className="field-l">
              Product name <span className="req">*</span>
            </div>
            <input
              className={`inp ${errors.productName ? "has-error" : ""}`}
              value={productName}
              onChange={(e) => {
                setProductName(e.target.value)
                clearError("productName")
              }}
              maxLength={100}
              placeholder="The product you're onboarding (you can add more later)"
            />
            {errors.productName && <p className="onb-field-error">{errors.productName}</p>}
          </div>

          <div className="field full">
            <div className="field-l">
              Product website <span className="opt">optional</span>
            </div>
            <input
              className="inp"
              type="url"
              value={productWebsite}
              onChange={(e) => setProductWebsite(e.target.value)}
              placeholder="https://yourproduct.com"
              autoComplete="url"
            />
            <p className="onb-field-hint">
              We&apos;ll read this to draft your industry, business type, and
              business context for the next steps.
            </p>
          </div>
        </div>

        {/* ── Your metrics (design scene onb1) ────────────────────────────────
            The design renders a flat wrapping row of pill chips; selected chips
            flip to the dark fill with a leading check. We keep the picker
            FUNCTION (server infers the North Star) behind that chip visual.
            Per the design's onb1, the PM picks EXACTLY 3 — the helper reads
            "pick up to 3" and the footer counts "3 of 3". */}
        <div className="onb-section" style={{ marginTop: 22 }} data-field="metrics">
          <div className="onb-section-h">
            Your metrics{" "}
            <span className="opt">— pick up to {ONB1_METRIC_PICKS} that matter most</span>
          </div>

          {errors.metrics && <p className="onb-field-error">{errors.metrics}</p>}

          <div className="metric-chips" id="suggestedMetrics" data-max={ONB1_METRIC_PICKS}>
            {candidates.length > 0 ? (
              candidates.map((c) => {
                const isSel = selected.some(
                  (s) => s.toLowerCase() === c.name.toLowerCase(),
                )
                const atMaxUnselected = !isSel && selected.length >= ONB1_METRIC_PICKS
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
        {/* The onb1 design card ENDS at the metric note. The tech-stack chips and
            the predicted industry / business-type dropdowns that used to live
            here moved to the business-context step (BusinessContext.tsx). */}
      </div>
    </OnboardingChrome>
  )
}
