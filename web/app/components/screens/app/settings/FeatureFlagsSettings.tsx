// DORMANT (commit B, 2026-06-01): This pane is no longer linked from
// SETTINGS_NAV per the sprntly_Design-3 reset. Backend persistence still
// works (saveFeatureFlags + the feature_flags column on onboarding_workspace).
// Bring back the SETTINGS_NAV entry to restore the visible pane.
"use client"

import { useEffect, useState } from "react"
import { useWorkspace } from "../../../../context/WorkspaceContext"
import { DEFAULT_FEATURE_FLAGS, type FeatureFlags } from "../../../../lib/onboarding/types"
import { saveFeatureFlags } from "../../../../lib/onboarding/store"
import { SettingsMessage, SettingsSection } from "./SettingsLayout"

const FLAG_META: { key: keyof FeatureFlags; label: string; desc: string }[] = [
  { key: "weekly_brief", label: "Weekly Brief", desc: "Monday-morning ranked priorities." },
  { key: "on_demand_analysis", label: "On-Demand Analysis", desc: "Ask Sprntly with cited evidence." },
  { key: "auto_prd_generation", label: "Auto-PRD generation", desc: "Draft PRDs from Brief findings." },
  { key: "engineer_agent", label: "Engineer Agent", desc: "Package context for Claude Code / Cursor." },
  { key: "research_agent", label: "Research Agent", desc: "Monitor competitors and market signals." },
  { key: "claude_code_handoff", label: "Claude Code handoff", desc: "Structured engineering handoff from PRDs." },
]

export function FeatureFlagsSettings() {
  const { workspace, loading, refresh } = useWorkspace()
  const [flags, setFlags] = useState<FeatureFlags>({ ...DEFAULT_FEATURE_FLAGS })
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (workspace?.feature_flags) {
      setFlags({ ...DEFAULT_FEATURE_FLAGS, ...workspace.feature_flags })
    }
  }, [workspace])

  async function onSave(e: React.FormEvent) {
    e.preventDefault()
    if (!workspace) return
    setSaving(true)
    setError(null)
    setSaved(false)
    try {
      await saveFeatureFlags(workspace.id, flags, workspace.onboarding_step)
      await refresh()
      setSaved(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save flags")
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <p className="settings-loading">Loading…</p>
  if (!workspace) {
    return (
      <SettingsSection title="Feature flags" sub="Complete onboarding first.">
        <p className="settings-placeholder">
          <a href="/onboarding/connectors">Configure agents →</a>
        </p>
      </SettingsSection>
    )
  }

  return (
    <SettingsSection title="Feature flags" sub="Control which Sprntly agents run for this workspace.">
      <form onSubmit={onSave}>
        <div className="ob-flag-grid">
          {FLAG_META.map((f) => (
            <label key={f.key} className={`ob-flag-row ${flags[f.key] ? "on" : ""}`}>
              <input
                type="checkbox"
                checked={flags[f.key]}
                onChange={() => setFlags((prev) => ({ ...prev, [f.key]: !prev[f.key] }))}
              />
              <div>
                <div className="ob-flag-label">{f.label}</div>
                <div className="ob-flag-desc">{f.desc}</div>
              </div>
            </label>
          ))}
        </div>
        {error && <SettingsMessage kind="error">{error}</SettingsMessage>}
        {saved && <SettingsMessage kind="success">Feature flags saved.</SettingsMessage>}
        <button type="submit" className="btn btn-primary" disabled={saving} style={{ marginTop: 16 }}>
          {saving ? "Saving…" : "Save feature flags"}
        </button>
      </form>
    </SettingsSection>
  )
}
