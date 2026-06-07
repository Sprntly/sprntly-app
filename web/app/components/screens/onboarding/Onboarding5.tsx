"use client"

import { useEffect, useMemo, useState } from "react"
import { useRouter } from "next/navigation"
import { InterviewLayout, useFieldValidation } from "../../onboarding/InterviewLayout"
import { useOnboarding } from "../../../context/OnboardingContext"
import {
  advanceOnboardingStep,
  upsertPrimaryProduct,
} from "../../../lib/onboarding/store"
import {
  validateProductWebsite,
  normalizeProductWebsite,
} from "../../../lib/onboarding/product-helpers"
import {
  buildKpiTreePayload,
  canSaveKpiTree,
  kpiTreeApi,
  MAX_PRIMARY_METRICS,
  MAX_SECONDARY_SIGNALS,
} from "../../../lib/onboarding/kpiTreeApi"

/**
 * Onboarding page 05 (design-v4) — "Tell us about your product."
 *
 * A product name + the success metrics that anchor the workspace. The
 * North Star is required; supporting metrics are picked from
 * industry-tailored suggestions or written in. The KPI tree is persisted
 * to the backend (PUT /v1/company/kpi-tree) — the canonical config entity
 * Synthesis later reads for strategic-alignment scoring.
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

export function Onboarding5() {
  const { workspace, setWorkspace, loading } = useOnboarding()
  const router = useRouter()
  const [productName, setProductName] = useState("")
  const [productWebsite, setProductWebsite] = useState("")
  const [northStar, setNorthStar] = useState("")
  const [supporting, setSupporting] = useState<string[]>([])
  const [customMetric, setCustomMetric] = useState("")
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!workspace) return
    setProductName(workspace.product?.name ?? workspace.display_name)
    setProductWebsite(workspace.product?.website ?? "")
    const tree = workspace.kpi_tree
    if (tree.north_star) setNorthStar(tree.north_star)
    if (tree.metrics.length) {
      setSupporting(tree.metrics.map((m) => m.name).filter(Boolean))
    }
  }, [workspace])

  const industry = workspace?.industry ?? ""
  const northStarHints =
    NORTH_STAR_SUGGESTIONS[industry] ?? NORTH_STAR_SUGGESTIONS.default
  const supportingHints =
    SUPPORTING_SUGGESTIONS[industry] ?? SUPPORTING_SUGGESTIONS.default

  const { errors, validate, clearError, containerRef } = useFieldValidation(
    () => [
      {
        key: "productName",
        valid: productName.trim().length > 0,
        message: "Add a product name.",
      },
      {
        key: "northStar",
        valid: canSaveKpiTree(northStar, supporting),
        message: "Set a North Star metric to anchor your KPI tree.",
      },
    ],
  )

  function toggleSupporting(metric: string) {
    setSupporting((prev) => {
      if (prev.includes(metric)) return prev.filter((m) => m !== metric)
      if (prev.length >= MAX_SUPPORTING) return prev
      return [...prev, metric]
    })
  }

  function addCustom() {
    const m = customMetric.trim()
    if (!m || supporting.includes(m) || supporting.length >= MAX_SUPPORTING) return
    setSupporting((prev) => [...prev, m])
    setCustomMetric("")
  }

  async function persist() {
    if (!workspace) return
    setError(null)
    if (!validate().ok) return
    const websiteErr = validateProductWebsite(productWebsite)
    if (websiteErr) {
      setError(websiteErr)
      return
    }
    setSaving(true)
    try {
      await upsertPrimaryProduct(workspace.id, {
        name: productName,
        website: normalizeProductWebsite(productWebsite),
      })
      await kpiTreeApi.put(buildKpiTreePayload(northStar, supporting))
      const updated = await advanceOnboardingStep(workspace.id, 6)
      const product = updated.product ?? workspace.product
      setWorkspace({ ...updated, product })
      router.push("/onboarding/6")
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save your product.")
    } finally {
      setSaving(false)
    }
  }

  const selectedCount = supporting.length

  const previewMetrics = useMemo(
    () => supporting.slice(0, MAX_SUPPORTING),
    [supporting],
  )

  if (loading) return <div className="ob-shell">Loading…</div>
  if (!workspace) {
    router.replace("/onboarding/1")
    return null
  }

  return (
    <InterviewLayout
      step={5}
      eyebrow="Saved · auto-saves after every step"
      title="Tell us about your product"
      agentMessage="A name and your success metrics anchor the whole workspace. Pick your North Star and the supporting metrics it leads to — or write your own. You'll add the full description in Settings."
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
                <li key={m}>{m}</li>
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
      {error && <div className="ob-form-error">{error}</div>}

      <div className={`field ${errors.productName ? "has-error" : ""}`} data-field="productName">
        <label className="field-label">Product name *</label>
        <input
          className="input"
          value={productName}
          onChange={(e) => {
            setProductName(e.target.value)
            clearError("productName")
          }}
          maxLength={100}
          placeholder="The product this workspace is about"
        />
        {errors.productName && <p className="field-error">{errors.productName}</p>}
      </div>
      <div className="field">
        <label className="field-label">Product website (optional)</label>
        <input
          className="input"
          type="url"
          value={productWebsite}
          onChange={(e) => setProductWebsite(e.target.value)}
          placeholder="https://yourproduct.com"
          autoComplete="url"
        />
      </div>

      <div className={`field ${errors.northStar ? "has-error" : ""}`} data-field="northStar">
        <label className="field-label">Primary metric — your North Star *</label>
        <input
          className="input"
          value={northStar}
          onChange={(e) => {
            setNorthStar(e.target.value)
            clearError("northStar")
          }}
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
              onClick={() => setNorthStar(h)}
            >
              {h}
            </button>
          ))}
        </div>
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
              className={`metric-chip ${supporting.includes(m) ? "selected" : ""}`}
              onClick={() => toggleSupporting(m)}
            >
              {m}
            </button>
          ))}
          {supporting
            .filter((m) => !supportingHints.includes(m))
            .map((m) => (
              <button
                key={m}
                type="button"
                className="metric-chip selected"
                onClick={() => toggleSupporting(m)}
              >
                {m}
              </button>
            ))}
        </div>
        <div className="ob-custom-metric">
          <input
            className="input"
            value={customMetric}
            onChange={(e) => setCustomMetric(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault()
                addCustom()
              }
            }}
            placeholder="Or write your own"
            maxLength={80}
          />
          <button
            type="button"
            className="btn btn-sm"
            onClick={addCustom}
            disabled={!customMetric.trim() || supporting.length >= MAX_SUPPORTING}
          >
            Add
          </button>
        </div>
        <p className="ob-metric-count">
          {selectedCount} supporting metric{selectedCount === 1 ? "" : "s"}{" "}
          selected · suggestions tailored to your industry
        </p>
      </div>
      </div>

      <style jsx>{`
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
      `}</style>
    </InterviewLayout>
  )
}
