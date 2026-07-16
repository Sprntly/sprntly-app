"use client"

import { useEffect, useState } from "react"
import { KpiTreeEditor, cleanKpiMetrics } from "../../../onboarding/KpiTreeEditor"
import { KpiTreePreview } from "../../../onboarding/KpiTreePreview"
import { useWorkspace } from "../../../../context/WorkspaceContext"
import { saveKpiTree } from "../../../../lib/onboarding/store"
import type { KpiMetric, KpiTree } from "../../../../lib/onboarding/types"
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

  useEffect(() => {
    if (!workspace) return
    setNorthStar(workspace.kpi_tree.north_star)
    setNorthStarDescription(workspace.kpi_tree.north_star_description)
    if (workspace.kpi_tree.metrics.length) setMetrics(workspace.kpi_tree.metrics)
  }, [workspace])

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
      <SettingsSection title="Preview" sub="How your tree appears in Briefs.">
        <KpiTreePreview tree={tree} />
      </SettingsSection>
    </>
  )
}
