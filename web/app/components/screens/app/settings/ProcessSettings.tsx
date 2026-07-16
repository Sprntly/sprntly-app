"use client"

import { useEffect, useState } from "react"
import { profileDisplayName, useWorkspace } from "../../../../context/WorkspaceContext"
import { updateWorkspace } from "../../../../lib/onboarding/store"
import {
  PLANNING_CYCLES,
  PRIORITIZATION_FRAMEWORKS,
} from "../../../../lib/onboarding/types"
import { SettingsMessage, SettingsPaneBar, SettingsSection } from "./SettingsLayout"

const FORM_ID = "pset-process-form"

/**
 * Settings → Process & Planning (registration spec 2026-07).
 *
 * Team-section process choices: team scope, prioritization framework (also
 * collected in onboarding for company accounts), plus the settings-only
 * planning cycle and sizing methodology (a select with an Other free-text —
 * the spec resolved it to settings-only, OPTIONAL).
 */

const SIZING_PRESETS = ["T-shirt sizes", "Story points", "Person-weeks"] as const

type Fields = {
  teamScope: string
  framework: string
  planningCycle: string
  sizing: string
  sizingOther: string
}

/** Split a stored sizing value into (preset, other) for the select+input pair. */
function splitSizing(stored: string): { sizing: string; sizingOther: string } {
  if (!stored) return { sizing: "", sizingOther: "" }
  if ((SIZING_PRESETS as readonly string[]).includes(stored)) {
    return { sizing: stored, sizingOther: "" }
  }
  return { sizing: "Other", sizingOther: stored }
}

export function ProcessSettings() {
  const { workspace, profile, loading, refresh } = useWorkspace()
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [teamScope, setTeamScope] = useState("")
  const [framework, setFramework] = useState("")
  const [planningCycle, setPlanningCycle] = useState("")
  const [sizing, setSizing] = useState("")
  const [sizingOther, setSizingOther] = useState("")
  const [snapshot, setSnapshot] = useState<Fields | null>(null)

  useEffect(() => {
    if (!workspace) return
    const split = splitSizing(workspace.sizing_methodology ?? "")
    const loaded: Fields = {
      teamScope: workspace.team_scope ?? "",
      framework: workspace.prioritization_framework ?? "",
      planningCycle: workspace.planning_cycle ?? "",
      sizing: split.sizing,
      sizingOther: split.sizingOther,
    }
    setTeamScope(loaded.teamScope)
    setFramework(loaded.framework)
    setPlanningCycle(loaded.planningCycle)
    setSizing(loaded.sizing)
    setSizingOther(loaded.sizingOther)
    setSnapshot(loaded)
  }, [workspace])

  const current: Fields = { teamScope, framework, planningCycle, sizing, sizingOther }
  const dirty =
    snapshot != null &&
    (Object.keys(current) as (keyof Fields)[]).some((k) => current[k] !== snapshot[k])

  function onDiscard() {
    if (!snapshot) return
    setTeamScope(snapshot.teamScope)
    setFramework(snapshot.framework)
    setPlanningCycle(snapshot.planningCycle)
    setSizing(snapshot.sizing)
    setSizingOther(snapshot.sizingOther)
    setError(null)
  }

  async function onSave(e: React.FormEvent) {
    e.preventDefault()
    if (!workspace) return
    setSaving(true)
    setError(null)
    setSaved(false)
    try {
      const resolvedSizing =
        sizing === "Other" ? sizingOther.trim() : sizing.trim()
      await updateWorkspace(workspace.id, {
        team_scope: teamScope.trim() || null,
        prioritization_framework: framework || null,
        planning_cycle: planningCycle || null,
        sizing_methodology: resolvedSizing || null,
      })
      setSnapshot(current)
      setSaved(true)
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save process settings")
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <div className="pset">
        <div className="pset-body">
          <p className="settings-loading">Loading process settings…</p>
        </div>
      </div>
    )
  }
  if (!workspace) {
    return (
      <div className="pset">
        <div className="pset-body">
          <SettingsSection
            title="Process & Planning"
            sub="Complete onboarding to create your workspace."
          >
            <p className="settings-placeholder">
              <a href="/onboarding/company">Continue onboarding →</a>
            </p>
          </SettingsSection>
        </div>
      </div>
    )
  }

  const identityMeta =
    [profileDisplayName(profile ?? null, profile?.email), profile?.email]
      .filter(Boolean)
      .join(" · ") || null

  return (
    <div className="pset">
      <SettingsPaneBar
        title="Process & Planning"
        meta={identityMeta}
        saved={saved}
        dirty={dirty}
        saving={saving}
        onDiscard={onDiscard}
        formId={FORM_ID}
      />

      <div className="pset-body">
        <h2 className="pset-title">Process &amp; Planning</h2>
        <p className="pset-sub">
          How your team scopes, prioritizes, and plans — the agents rank and
          size work the way you do.
        </p>

        <form id={FORM_ID} className="pset-card" onSubmit={onSave}>
          <div className="pset-grid">
            <div className="pset-field">
              <label className="pset-label" htmlFor="pr-scope">Team scope</label>
              <input
                id="pr-scope"
                className="input"
                value={teamScope}
                onChange={(e) => setTeamScope(e.target.value)}
                maxLength={100}
                placeholder="The exact product area, e.g. notifications"
              />
            </div>
            <div className="pset-field">
              <label className="pset-label" htmlFor="pr-framework">
                Prioritization framework
              </label>
              <select
                id="pr-framework"
                className="input"
                value={framework}
                onChange={(e) => setFramework(e.target.value)}
              >
                <option value="">Not set</option>
                {PRIORITIZATION_FRAMEWORKS.map((f) => (
                  <option key={f.value} value={f.value}>
                    {f.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="pset-field">
              <label className="pset-label" htmlFor="pr-cycle">Planning cycle</label>
              <select
                id="pr-cycle"
                className="input"
                value={planningCycle}
                onChange={(e) => setPlanningCycle(e.target.value)}
              >
                <option value="">Not set</option>
                {PLANNING_CYCLES.map((c) => (
                  <option key={c.value} value={c.value}>
                    {c.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="pset-field">
              <label className="pset-label" htmlFor="pr-sizing">Sizing methodology</label>
              <select
                id="pr-sizing"
                className="input"
                value={sizing}
                onChange={(e) => setSizing(e.target.value)}
              >
                <option value="">Not set</option>
                {SIZING_PRESETS.map((s) => (
                  <option key={s}>{s}</option>
                ))}
                <option value="Other">Other</option>
              </select>
              {sizing === "Other" && (
                <input
                  className="input"
                  style={{ marginTop: 8 }}
                  value={sizingOther}
                  onChange={(e) => setSizingOther(e.target.value)}
                  maxLength={100}
                  placeholder="Your sizing approach"
                  aria-label="Sizing methodology (other)"
                />
              )}
            </div>
          </div>

          {error && (
            <div style={{ marginTop: 14 }}>
              <SettingsMessage kind="error">{error}</SettingsMessage>
            </div>
          )}
        </form>
      </div>
    </div>
  )
}
