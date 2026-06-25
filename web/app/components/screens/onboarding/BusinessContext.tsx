"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { OnboardingChrome } from "../../onboarding/OnboardingChrome"
import { useOnboarding } from "../../../context/OnboardingContext"
import { advanceOnboardingStep, updateWorkspace } from "../../../lib/onboarding/store"
import { businessContextApi, type BusinessContextDoc } from "../../../lib/api"
import {
  INDUSTRIES,
  BUSINESS_TYPES,
  TECH_STACK_OPTIONS,
} from "../../../lib/onboarding/types"
import { Sparkles } from "../../auth/icons"
import {
  buildLayers,
  type BcLayer,
} from "../app/settings/BusinessContextSettings"

/**
 * Onboarding step 04 — "Your business context" (design scene onbctx).
 *
 * REUSES the #450 Business Context surface end-to-end:
 *   - the structured 8-layer doc + `businessContextApi` (GET/PUT
 *     /v1/company/business-context), and
 *   - the `buildLayers` field model the Settings pane edits.
 *
 * The doc is auto-drafted server-side from the website + connectors during the
 * earlier steps; here the PM reviews and edits it inline. A 404 from GET means
 * "not generated yet" — we surface a Generate affordance and a friendly empty
 * state, and the step stays skippable (the design's onbctx never blocks).
 *
 * On Continue we PUT any edits (when a doc exists) and advance to the strategy
 * step (index 5). The pane mirrors the Settings editor: edit only each leaf's
 * `.value`; the backend stamps edited leaves src="user".
 */

/** Seed the editable string values from a freshly loaded doc. */
function valuesFromDoc(doc: BusinessContextDoc): Record<string, string> {
  const out: Record<string, string> = {}
  for (const layer of buildLayers(doc)) {
    for (const field of layer.fields) {
      const v = field.leaf?.value
      out[field.path] =
        v == null
          ? ""
          : Array.isArray(v)
            ? v.join(", ")
            : typeof v === "boolean"
              ? v
                ? "true"
                : "false"
              : String(v)
    }
  }
  return out
}

/** Apply edited string values back onto a clone of the doc (mirrors the
 *  Settings pane's applyEdits — list-shaped leaves split on comma). */
function applyEdits(
  doc: BusinessContextDoc,
  values: Record<string, string>,
): BusinessContextDoc {
  const next = JSON.parse(JSON.stringify(doc)) as BusinessContextDoc
  for (const [path, raw] of Object.entries(values)) {
    const parts = path.split(".")
    let cursor: unknown = next
    for (let i = 0; i < parts.length - 1; i++) {
      cursor = (cursor as Record<string, unknown>)[parts[i]]
      if (cursor == null) break
    }
    if (cursor == null) continue
    const leafKey = parts[parts.length - 1]
    const leaf = (cursor as Record<string, { value: unknown }>)[leafKey]
    if (!leaf || typeof leaf !== "object") continue
    const trimmed = raw.trim()
    if (Array.isArray(leaf.value)) {
      leaf.value = trimmed ? trimmed.split(",").map((s) => s.trim()).filter(Boolean) : []
    } else {
      leaf.value = trimmed === "" ? null : trimmed
    }
  }
  return next
}

// ── Company-shape sub-view (relocated from onb1) ──────────────────────────────
// The tech-stack chips + predicted industry / business-type dropdowns used to
// live on onb1. The onb1 design ends at the metric note, so they moved here to
// the business-context step. These edit WORKSPACE-level company fields
// (companies.tech_stack / industry / business_type), persisted via
// updateWorkspace — separate from the structured business-context doc.
export type CompanyShapeViewProps = {
  industry: string
  businessType: string
  techStack: string[]
  onChangeIndustry: (value: string) => void
  onChangeBusinessType: (value: string) => void
  onToggleTechStack: (tech: string) => void
}

export function CompanyShapeView({
  industry,
  businessType,
  techStack,
  onChangeIndustry,
  onChangeBusinessType,
  onToggleTechStack,
}: CompanyShapeViewProps) {
  return (
    <div data-bc-company-shape>
      <div className="onb-section">
        <div className="onb-section-h">
          Your business{" "}
          <span className="opt">— predicted from your website, edit if it&apos;s off</span>
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

      <div className="onb-section">
        <div className="onb-section-h">
          Tech stack <span className="opt">optional</span>
        </div>
        <div className="onb-chip-row">
          {TECH_STACK_OPTIONS.map((t) => (
            <button
              key={t}
              type="button"
              className={`onb-chip ${techStack.includes(t) ? "sel" : ""}`}
              aria-pressed={techStack.includes(t)}
              onClick={() => onToggleTechStack(t)}
            >
              {t}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}

// ── pure view (props in, JSX out — unit-testable via renderToStaticMarkup) ────
export type BusinessContextStepViewProps = {
  loading: boolean
  loadError: string | null
  /** null = GET returned 404 (not generated yet). */
  doc: BusinessContextDoc | null
  values: Record<string, string>
  generating: boolean
  generateError: string | null
  onChangeField: (path: string, value: string) => void
  onGenerate: () => void
  /** Relocated company-shape fields (industry / business type / tech stack). */
  companyShape: CompanyShapeViewProps
}

export function BusinessContextStepView({
  loading,
  loadError,
  doc,
  values,
  generating,
  generateError,
  onChangeField,
  onGenerate,
  companyShape,
}: BusinessContextStepViewProps) {
  if (loading) {
    return <p className="onb-field-hint">Loading your business context…</p>
  }
  if (loadError) {
    return <div className="onb-form-error">Could not load business context: {loadError}</div>
  }

  // Empty / not-generated state — never blocks the step. The relocated
  // company-shape fields still render so industry / business type / tech stack
  // can be confirmed even when the structured doc hasn't been drafted yet.
  if (!doc) {
    return (
      <>
        <CompanyShapeView {...companyShape} />
        <div className="onb-section" data-bc-state="empty">
        <div className="ctx-ai-flag">
          <Sparkles style={{ width: 13, height: 13 }} aria-hidden /> Your business
          context hasn&apos;t been drafted yet — it&apos;s normally built from your
          website and connectors.
        </div>
        {generateError && <p className="onb-field-error">{generateError}</p>}
        <button
          type="button"
          className="btn btn-secondary"
          onClick={onGenerate}
          disabled={generating}
          style={{ marginTop: 12 }}
        >
          {generating ? "Drafting…" : "Draft my business context"}
        </button>
        <p className="onb-field-hint" style={{ marginTop: 10 }}>
          You can skip this for now and fill it in later in Settings → Business
          Context.
        </p>
        </div>
      </>
    )
  }

  const layers: BcLayer[] = buildLayers(doc)

  return (
    <div data-bc-state="ready">
      <CompanyShapeView {...companyShape} />

      <div className="ctx-ai-flag">
        <Sparkles style={{ width: 13, height: 13 }} aria-hidden /> AI-drafted from
        your website and connectors. Edit anything — it&apos;s the lens every
        agent reasons through.
      </div>

      {layers.map((layer) => (
        <div key={layer.key} className="onb-section bc-layer" data-layer={layer.key}>
          <div className="onb-section-h">
            {layer.title} <span className="opt">— {layer.sub}</span>
          </div>
          {layer.fields.length === 0 && <p className="onb-field-hint">No entries.</p>}
          {layer.fields.map((field) => (
            <div className="field full bc-field" key={field.path} data-field={field.path}>
              <div className="field-l">{field.label}</div>
              {field.multiline ? (
                <textarea
                  className="inp"
                  rows={3}
                  value={values[field.path] ?? ""}
                  onChange={(e) => onChangeField(field.path, e.target.value)}
                  aria-label={field.label}
                />
              ) : (
                <input
                  className="inp"
                  value={values[field.path] ?? ""}
                  onChange={(e) => onChangeField(field.path, e.target.value)}
                  aria-label={field.label}
                />
              )}
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}

// ── container ─────────────────────────────────────────────────────────────────
export function BusinessContext() {
  const { workspace, setWorkspace, websiteAnalysis, loading } = useOnboarding()
  const router = useRouter()

  const [doc, setDoc] = useState<BusinessContextDoc | null>(null)
  const [bcLoading, setBcLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [values, setValues] = useState<Record<string, string>>({})
  const [generating, setGenerating] = useState(false)
  const [generateError, setGenerateError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  // ── relocated company-shape fields (moved off onb1) ─────────────────────────
  // industry / business_type / tech_stack are WORKSPACE-level company fields.
  // Seeded from the saved workspace first, then any website analysis. A user
  // edit stops later analysis results from clobbering the choice.
  const [industry, setIndustry] = useState<string>(INDUSTRIES[0])
  const [businessType, setBusinessType] = useState<string>(BUSINESS_TYPES[0])
  const [techStack, setTechStack] = useState<string[]>([])
  const [industryTouched, setIndustryTouched] = useState(false)
  const [businessTypeTouched, setBusinessTypeTouched] = useState(false)

  useEffect(() => {
    if (industryTouched) return
    const next = workspace?.industry || websiteAnalysis?.industry
    if (next && INDUSTRIES.includes(next as (typeof INDUSTRIES)[number])) setIndustry(next)
    else if (next) setIndustry("Other")
  }, [workspace?.industry, websiteAnalysis?.industry, industryTouched])

  useEffect(() => {
    if (businessTypeTouched) return
    const next = workspace?.business_type || websiteAnalysis?.business_type
    if (next && BUSINESS_TYPES.includes(next as (typeof BUSINESS_TYPES)[number]))
      setBusinessType(next)
  }, [workspace?.business_type, websiteAnalysis?.business_type, businessTypeTouched])

  const techStackSeeded = useRef(false)
  useEffect(() => {
    if (techStackSeeded.current) return
    if (!workspace) return
    techStackSeeded.current = true
    setTechStack(workspace.tech_stack ?? [])
  }, [workspace])

  const load = useCallback(async () => {
    setBcLoading(true)
    setLoadError(null)
    try {
      const d = await businessContextApi.get()
      setDoc(d)
      setValues(d ? valuesFromDoc(d) : {})
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e))
    } finally {
      setBcLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!workspace) return
    void load()
  }, [workspace, load])

  // Redirect when there's no workspace to anchor the step.
  useEffect(() => {
    if (!loading && !workspace) router.replace("/onboarding/business-info")
  }, [loading, workspace, router])

  function onChangeField(path: string, value: string) {
    setSaveError(null)
    setValues((prev) => ({ ...prev, [path]: value }))
  }

  function onGenerate() {
    void (async () => {
      setGenerating(true)
      setGenerateError(null)
      try {
        await businessContextApi.refresh()
        await load()
      } catch (e) {
        setGenerateError(
          e instanceof Error ? e.message : "Could not draft your business context.",
        )
      } finally {
        setGenerating(false)
      }
    })()
  }

  async function next() {
    if (!workspace) return
    setSaving(true)
    setSaveError(null)
    try {
      // Persist the relocated company-shape fields (industry / business type /
      // tech stack) onto the workspace — these moved here off onb1 and still
      // seed the workspace + metric candidates downstream.
      const updated = await updateWorkspace(workspace.id, {
        industry,
        business_type: businessType,
        tech_stack: techStack,
      })
      setWorkspace({ ...updated, product: updated.product ?? workspace.product })
      // Persist any inline edits when a doc exists (skippable when it doesn't).
      if (doc) {
        await businessContextApi.update(applyEdits(doc, values))
      }
      await advanceOnboardingStep(workspace.id, 5)
      router.push("/onboarding/strategy")
    } catch (e) {
      setSaveError(
        e instanceof Error ? e.message : "Couldn't save your business context.",
      )
      setSaving(false)
    }
  }

  async function skip() {
    if (!workspace) return
    setSaving(true)
    try {
      // Skip only skips the structured business-context doc edits; the relocated
      // company-shape fields are still persisted so they're never lost.
      const updated = await updateWorkspace(workspace.id, {
        industry,
        business_type: businessType,
        tech_stack: techStack,
      })
      setWorkspace({ ...updated, product: updated.product ?? workspace.product })
      await advanceOnboardingStep(workspace.id, 5)
      router.push("/onboarding/strategy")
    } finally {
      setSaving(false)
    }
  }

  if (loading || !workspace) return <div className="onb-shell">Loading…</div>

  return (
    <OnboardingChrome
      step={4}
      saveLabel="Saved · auto-saves"
      title={
        <>
          Your <em>business context.</em>
        </>
      }
      subtitle="We drafted this from your website and connectors. Edit anything — it's the lens every Sprntly agent reasons through. You can refine it any time in Settings."
      footerMeta={
        <>
          Step 4 of 5 · business context —{" "}
          <button
            type="button"
            className="onb-skip-link"
            onClick={() => void skip()}
            disabled={saving}
          >
            Skip for now
          </button>
        </>
      }
      onBack={() => router.push("/onboarding/connectors")}
      onContinue={() => void next()}
      continueDisabled={saving}
      loading={saving}
    >
      {saveError && <div className="onb-form-error">{saveError}</div>}
      <BusinessContextStepView
        loading={bcLoading}
        loadError={loadError}
        doc={doc}
        values={values}
        generating={generating}
        generateError={generateError}
        onChangeField={onChangeField}
        onGenerate={onGenerate}
        companyShape={{
          industry,
          businessType,
          techStack,
          onChangeIndustry: (v) => {
            setIndustryTouched(true)
            setSaveError(null)
            setIndustry(v)
          },
          onChangeBusinessType: (v) => {
            setBusinessTypeTouched(true)
            setSaveError(null)
            setBusinessType(v)
          },
          onToggleTechStack: (t) => {
            setSaveError(null)
            setTechStack((prev) =>
              prev.includes(t) ? prev.filter((x) => x !== t) : [...prev, t],
            )
          },
        }}
      />
    </OnboardingChrome>
  )
}
