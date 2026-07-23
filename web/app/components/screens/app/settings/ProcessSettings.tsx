"use client"

import { useEffect, useState } from "react"
import { profileDisplayName, useWorkspace } from "../../../../context/WorkspaceContext"
import { updateWorkspace } from "../../../../lib/onboarding/store"
import { workspacesApi } from "../../../../lib/api"
import {
  PLANNING_CYCLES,
  PRIORITIZATION_FRAMEWORKS,
} from "../../../../lib/onboarding/types"
import { SettingsMessage, SettingsPaneBar, SettingsSection } from "./SettingsLayout"

const FORM_ID = "pset-process-form"

/**
 * Settings → Process & Planning — the team/process fields the v6 wizard
 * collects: team name + scope of work (step 5), prioritization framework
 * (step 3), planning cycle (step 1), the steps-6/7 typed blocks (team
 * strategy, team roadmap, decision process, additional context), plus the
 * deliberately settings-only sizing methodology (July 16 decision: optional,
 * never in onboarding).
 */

const SIZING_PRESETS = ["T-shirt sizes", "Story points", "Person-weeks"] as const

type Fields = {
  teamName: string
  teamScope: string
  framework: string
  planningCycle: string
  sizing: string
  sizingOther: string
  teamStrategy: string
  teamRoadmap: string
  decisionProcess: string
  additionalContext: string
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
  const { workspace, workspaces, profile, loading, refresh } = useWorkspace()
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [teamName, setTeamName] = useState("")
  const [teamScope, setTeamScope] = useState("")
  const [framework, setFramework] = useState("")
  const [planningCycle, setPlanningCycle] = useState("")
  const [sizing, setSizing] = useState("")
  const [sizingOther, setSizingOther] = useState("")
  const [teamStrategy, setTeamStrategy] = useState("")
  const [teamRoadmap, setTeamRoadmap] = useState("")
  const [decisionProcess, setDecisionProcess] = useState("")
  const [additionalContext, setAdditionalContext] = useState("")
  const [snapshot, setSnapshot] = useState<Fields | null>(null)

  useEffect(() => {
    if (!workspace) return
    const split = splitSizing(workspace.sizing_methodology ?? "")
    const loaded: Fields = {
      teamName: workspace.team_name ?? "",
      teamScope: workspace.team_scope ?? "",
      framework: workspace.prioritization_framework ?? "",
      planningCycle: workspace.planning_cycle ?? "",
      sizing: split.sizing,
      sizingOther: split.sizingOther,
      teamStrategy: workspace.team_strategy ?? "",
      teamRoadmap: workspace.team_roadmap ?? "",
      decisionProcess: workspace.decision_process ?? "",
      additionalContext: workspace.additional_context ?? "",
    }
    setTeamName(loaded.teamName)
    setTeamScope(loaded.teamScope)
    setFramework(loaded.framework)
    setPlanningCycle(loaded.planningCycle)
    setSizing(loaded.sizing)
    setSizingOther(loaded.sizingOther)
    setTeamStrategy(loaded.teamStrategy)
    setTeamRoadmap(loaded.teamRoadmap)
    setDecisionProcess(loaded.decisionProcess)
    setAdditionalContext(loaded.additionalContext)
    setSnapshot(loaded)
  }, [workspace])

  const current: Fields = {
    teamName, teamScope, framework, planningCycle, sizing, sizingOther,
    teamStrategy, teamRoadmap, decisionProcess, additionalContext,
  }
  const dirty =
    snapshot != null &&
    (Object.keys(current) as (keyof Fields)[]).some((k) => current[k] !== snapshot[k])

  function onDiscard() {
    if (!snapshot) return
    setTeamName(snapshot.teamName)
    setTeamScope(snapshot.teamScope)
    setFramework(snapshot.framework)
    setPlanningCycle(snapshot.planningCycle)
    setSizing(snapshot.sizing)
    setSizingOther(snapshot.sizingOther)
    setTeamStrategy(snapshot.teamStrategy)
    setTeamRoadmap(snapshot.teamRoadmap)
    setDecisionProcess(snapshot.decisionProcess)
    setAdditionalContext(snapshot.additionalContext)
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
      // The six "Your workspace" fields live on the DEFAULT workspace row
      // (2026-07-22 — moved off companies), a single source of truth shared with
      // onboarding. name → the workspaces.name the switcher shows.
      const defaultWorkspaceId = workspaces.find((w) => w.is_default)?.id ?? null
      if (!defaultWorkspaceId) {
        throw new Error(
          "Your workspace isn't ready yet — reload and try again.",
        )
      }
      // Split the write: workspace-owned fields → the workspace row; the rest
      // (prioritization framework, planning cycle, decision process) → companies.
      await Promise.all([
        workspacesApi.update(defaultWorkspaceId, {
          name: teamName.trim() || undefined,
          team_scope: teamScope.trim() || null,
          sizing_methodology: resolvedSizing || null,
          team_strategy: teamStrategy.trim() || null,
          team_roadmap: teamRoadmap.trim() || null,
          additional_context: additionalContext.trim() || null,
        }),
        updateWorkspace(workspace.id, {
          prioritization_framework: framework || null,
          planning_cycle: planningCycle || null,
          decision_process: decisionProcess.trim() || null,
        }),
      ])
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
              <label className="pset-label" htmlFor="pr-team-name">Team name</label>
              <input
                id="pr-team-name"
                className="input"
                value={teamName}
                onChange={(e) => setTeamName(e.target.value)}
                maxLength={100}
                placeholder="e.g. Nutrition & Sleep"
              />
            </div>
            <div className="pset-field">
              <label className="pset-label" htmlFor="pr-scope">Scope of work</label>
              <input
                id="pr-scope"
                className="input"
                value={teamScope}
                onChange={(e) => setTeamScope(e.target.value)}
                maxLength={1000}
                placeholder="What this team owns end to end"
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
            <div className="pset-field pset-field--full">
              <label className="pset-label" htmlFor="pr-team-strategy">Team strategy</label>
              <textarea
                id="pr-team-strategy"
                className="input"
                rows={3}
                value={teamStrategy}
                onChange={(e) => setTeamStrategy(e.target.value)}
                maxLength={4000}
                placeholder="What the team is trying to achieve this half, and why"
              />
            </div>
            <div className="pset-field pset-field--full">
              <label className="pset-label" htmlFor="pr-team-roadmap">Team roadmap</label>
              <textarea
                id="pr-team-roadmap"
                className="input"
                rows={3}
                value={teamRoadmap}
                onChange={(e) => setTeamRoadmap(e.target.value)}
                maxLength={4000}
                placeholder="What is committed, in progress, and planned"
              />
            </div>
            <div className="pset-field pset-field--full">
              <label className="pset-label" htmlFor="pr-decisions">How the team decides</label>
              <textarea
                id="pr-decisions"
                className="input"
                rows={3}
                value={decisionProcess}
                onChange={(e) => setDecisionProcess(e.target.value)}
                maxLength={4000}
                placeholder="How you weigh trade-offs, who approves, how disagreements resolve"
              />
            </div>
            <div className="pset-field pset-field--full">
              <label className="pset-label" htmlFor="pr-extra">Anything else</label>
              <textarea
                id="pr-extra"
                className="input"
                rows={3}
                value={additionalContext}
                onChange={(e) => setAdditionalContext(e.target.value)}
                maxLength={4000}
                placeholder="Sizing detail, glossary & terminology, key technologies, research"
              />
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
