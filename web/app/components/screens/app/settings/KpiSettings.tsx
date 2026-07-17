"use client"

import { useEffect, useState } from "react"
import { KpiTreeEditor, cleanKpiMetrics } from "../../../onboarding/KpiTreeEditor"
import { KpiTreePreview } from "../../../onboarding/KpiTreePreview"
import { useWorkspace } from "../../../../context/WorkspaceContext"
import { saveKpiTree, saveMetricDefinitions } from "../../../../lib/onboarding/store"
import type {
  KpiMetric,
  KpiTree,
  MetricDefinition,
} from "../../../../lib/onboarding/types"
import { SettingsMessage, SettingsSection } from "./SettingsLayout"

const NORTH_STAR_HINTS: Record<string, string[]> = {
  "B2B SaaS": ["Net revenue retention", "Weekly active teams", "Activation rate"],
  B2C: ["DAU/MAU ratio", "Day-30 retention", "Conversion rate"],
  default: ["Day-30 retention", "NRR", "Weekly active users"],
}

export function KpiSettings() {
  const { workspace, loading, refresh } = useWorkspace()
  const [northStar, setNorthStar] = useState("")
  const [northStarDescription, setNorthStarDescription] = useState("")
  const [metrics, setMetrics] = useState<KpiMetric[]>([
    { name: "", description: "" },
    { name: "", description: "" },
  ])
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Metric definitions (onboarding v6 define-metrics sub-flow): one row per
  // picked metric — the plain-English definition + analytics mapping.
  const [defs, setDefs] = useState<MetricDefinition[]>([])
  const [defsSaving, setDefsSaving] = useState(false)
  const [defsSaved, setDefsSaved] = useState(false)
  const [defsError, setDefsError] = useState<string | null>(null)

  useEffect(() => {
    if (!workspace) return
    setNorthStar(workspace.kpi_tree.north_star)
    setNorthStarDescription(workspace.kpi_tree.north_star_description)
    if (workspace.kpi_tree.metrics.length) setMetrics(workspace.kpi_tree.metrics)
    // Saved definitions first, then editable empty rows for any picked metric
    // still missing one.
    const saved = workspace.metric_definitions
    const have = new Set(saved.map((d) => d.metric.toLowerCase()))
    const missing = workspace.kpi_tree.metrics
      .map((m) => m.name.trim())
      .filter((n) => n && !have.has(n.toLowerCase()))
      .map((n) => ({ metric: n, definition: "", mapping: "", baseline: null }))
    setDefs([...saved, ...missing])
  }, [workspace])

  function patchDef(i: number, patch: Partial<MetricDefinition>) {
    setDefs((prev) => prev.map((d, j) => (j === i ? { ...d, ...patch } : d)))
  }

  async function onSaveDefs(e: React.FormEvent) {
    e.preventDefault()
    if (!workspace) return
    setDefsSaving(true)
    setDefsError(null)
    setDefsSaved(false)
    try {
      await saveMetricDefinitions(workspace.id, defs)
      await refresh()
      setDefsSaved(true)
    } catch (e) {
      setDefsError(
        e instanceof Error ? e.message : "Could not save metric definitions",
      )
    } finally {
      setDefsSaving(false)
    }
  }

  const hints =
    NORTH_STAR_HINTS[workspace?.industry ?? ""] ?? NORTH_STAR_HINTS.default
  const tree: KpiTree = {
    north_star: northStar,
    north_star_description: northStarDescription,
    metrics: cleanKpiMetrics(metrics),
  }
  const namedCount = metrics.filter((m) => m.name.trim()).length
  const canSave = northStar.trim().length > 0 && namedCount >= 2

  async function onSave(e: React.FormEvent) {
    e.preventDefault()
    if (!workspace || !canSave) return
    setSaving(true)
    setError(null)
    setSaved(false)
    try {
      const finalTree = {
        north_star: northStar.trim(),
        north_star_description: northStarDescription.trim(),
        metrics: cleanKpiMetrics(metrics),
      }
      await saveKpiTree(workspace.id, finalTree, workspace.onboarding_step)
      await refresh()
      setSaved(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save KPI tree")
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <p className="settings-loading">Loading…</p>
  if (!workspace) {
    return (
      <SettingsSection title="KPI tree" sub="Complete onboarding first.">
        <p className="settings-placeholder">
          <a href="/onboarding/company">Set up your KPI tree →</a>
        </p>
      </SettingsSection>
    )
  }

  return (
    <>
      <SettingsSection
        title="KPI tree"
        sub="Edits apply to the next Brief and recommendations. Each metric is a name plus a short description used for goal-fit scoring."
      >
        <form onSubmit={onSave}>
          <KpiTreeEditor
            northStar={northStar}
            northStarDescription={northStarDescription}
            metrics={metrics}
            hints={hints}
            onNorthStarChange={setNorthStar}
            onNorthStarDescriptionChange={setNorthStarDescription}
            onMetricsChange={setMetrics}
          />
          {error && <SettingsMessage kind="error">{error}</SettingsMessage>}
          {saved && <SettingsMessage kind="success">KPI tree saved.</SettingsMessage>}
          <button type="submit" className="btn btn-primary" disabled={saving || !canSave}>
            {saving ? "Saving…" : "Save KPI tree"}
          </button>
        </form>
      </SettingsSection>
      <SettingsSection
        title="Metric definitions"
        sub="How Sprntly measures each metric — the plain-English definition and the analytics event mapping confirmed at onboarding."
      >
        {defs.length === 0 ? (
          <p className="settings-placeholder">
            Pick metrics above first — definitions attach to them.
          </p>
        ) : (
          <form onSubmit={onSaveDefs}>
            {defs.map((d, i) => (
              <div key={d.metric} className="pset-grid" style={{ marginBottom: 14 }}>
                <div className="pset-field pset-field--full">
                  <label className="pset-label">{d.metric}</label>
                  <textarea
                    className="input"
                    rows={2}
                    value={d.definition}
                    onChange={(e) => patchDef(i, { definition: e.target.value })}
                    maxLength={500}
                    placeholder={`Plain-English definition of "${d.metric}"`}
                    aria-label={`${d.metric} definition`}
                  />
                  <input
                    className="input"
                    style={{ marginTop: 8, fontFamily: "var(--font-mono, monospace)" }}
                    value={d.mapping}
                    onChange={(e) => patchDef(i, { mapping: e.target.value })}
                    maxLength={300}
                    placeholder="event: session_start where feature_engaged = true"
                    aria-label={`${d.metric} analytics mapping`}
                  />
                </div>
              </div>
            ))}
            {defsError && <SettingsMessage kind="error">{defsError}</SettingsMessage>}
            {defsSaved && (
              <SettingsMessage kind="success">Metric definitions saved.</SettingsMessage>
            )}
            <button type="submit" className="btn btn-primary" disabled={defsSaving}>
              {defsSaving ? "Saving…" : "Save definitions"}
            </button>
          </form>
        )}
      </SettingsSection>
      <SettingsSection title="Preview" sub="How your tree appears in Briefs.">
        <KpiTreePreview tree={tree} />
      </SettingsSection>
    </>
  )
}
