// DORMANT (commit B, 2026-06-01): This pane is no longer linked from
// SETTINGS_NAV per the sprntly_Design-3 reset. The component still
// renders if reached via /settings?section=strategic but the URL falls
// back to Profile through the default branch in SettingsScreen.tsx —
// no shim. Backend persistence still works; bring back the SETTINGS_NAV
// entry to restore the visible pane.
"use client"

import { useEffect, useState } from "react"
import { useWorkspace } from "../../../../context/WorkspaceContext"
import { saveStrategicContext } from "../../../../lib/onboarding/store"
import { SettingsMessage, SettingsSection } from "./SettingsLayout"

export function StrategicSettings() {
  const { workspace, loading, refresh } = useWorkspace()
  const [okrs, setOkrs] = useState("")
  const [recentDecisions, setRecentDecisions] = useState("")
  const [deadEnds, setDeadEnds] = useState("")
  const [biggestRisk, setBiggestRisk] = useState("")
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!workspace) return
    setOkrs(workspace.okrs ?? "")
    setRecentDecisions(workspace.recent_decisions ?? "")
    setDeadEnds((workspace.dead_ends ?? []).join(", "))
    setBiggestRisk(workspace.biggest_risk ?? "")
  }, [workspace])

  async function onSave(e: React.FormEvent) {
    e.preventDefault()
    if (!workspace) return
    setSaving(true)
    setError(null)
    setSaved(false)
    try {
      await saveStrategicContext(
        workspace.id,
        {
          okrs,
          recent_decisions: recentDecisions || null,
          dead_ends: deadEnds.split(",").map((s) => s.trim()).filter(Boolean),
          biggest_risk: biggestRisk || null,
        },
        workspace.onboarding_step,
      )
      await refresh()
      setSaved(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save")
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <p className="settings-loading">Loading…</p>
  if (!workspace) {
    return (
      <SettingsSection title="Strategic context" sub="Complete onboarding first.">
        {/* The dedicated strategic-context onboarding page was removed in the
            semantic-routes refactor. There's no longer a step to deep-link to,
            so the no-workspace CTA points at the start of onboarding. */}
        <p className="settings-placeholder">
          <a href="/onboarding/company">Start onboarding →</a>
        </p>
      </SettingsSection>
    )
  }

  return (
    <SettingsSection
      title="Strategic context"
      sub="OKRs, decisions, and exclusions weight how Sprntly ranks recommendations."
    >
      <form onSubmit={onSave}>
        <div className="field">
          <label className="field-label">Current OKRs / priorities</label>
          <textarea className="textarea" rows={4} maxLength={1000} value={okrs} onChange={(e) => setOkrs(e.target.value)} />
        </div>
        <div className="field">
          <label className="field-label">Recent major decisions</label>
          <textarea className="textarea" rows={3} value={recentDecisions} onChange={(e) => setRecentDecisions(e.target.value)} />
        </div>
        <div className="field">
          <label className="field-label">Known dead ends</label>
          <input className="input" value={deadEnds} onChange={(e) => setDeadEnds(e.target.value)} placeholder="Comma-separated" />
        </div>
        <div className="field">
          <label className="field-label">Biggest risk / uncertainty</label>
          <textarea className="textarea" rows={2} maxLength={500} value={biggestRisk} onChange={(e) => setBiggestRisk(e.target.value)} />
        </div>
        {error && <SettingsMessage kind="error">{error}</SettingsMessage>}
        {saved && <SettingsMessage kind="success">Strategic context saved.</SettingsMessage>}
        <button type="submit" className="btn btn-primary" disabled={saving}>
          {saving ? "Saving…" : "Save strategic context"}
        </button>
      </form>
    </SettingsSection>
  )
}
