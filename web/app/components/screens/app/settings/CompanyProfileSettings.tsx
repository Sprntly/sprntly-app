"use client"

import { useEffect, useState } from "react"
import { profileDisplayName, useWorkspace } from "../../../../context/WorkspaceContext"
import { updateWorkspace } from "../../../../lib/onboarding/store"
import { SettingsMessage, SettingsPaneBar, SettingsSection } from "./SettingsLayout"

const FORM_ID = "pset-company-profile-form"

/**
 * Settings → Company Profile — mirrors onboarding v6 step 1's company fields:
 * mission & vision, strategy / OKRs, and portfolio (planning cycle lives in
 * Process & Planning). Saved to first-class companies columns via
 * updateWorkspace. The ICP and tone & voice editors were pruned in v6 — the
 * flow no longer collects them and nothing downstream consumed them.
 */

type Fields = {
  mission: string
  strategy: string
  portfolio: string
}

export function CompanyProfileSettings() {
  const { workspace, profile, loading, refresh } = useWorkspace()
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [mission, setMission] = useState("")
  const [strategy, setStrategy] = useState("")
  const [portfolio, setPortfolio] = useState("")
  const [snapshot, setSnapshot] = useState<Fields | null>(null)

  useEffect(() => {
    if (!workspace) return
    const loaded: Fields = {
      mission: workspace.mission ?? "",
      strategy: workspace.strategy ?? "",
      portfolio: workspace.portfolio ?? "",
    }
    setMission(loaded.mission)
    setStrategy(loaded.strategy)
    setPortfolio(loaded.portfolio)
    setSnapshot(loaded)
  }, [workspace])

  const current: Fields = { mission, strategy, portfolio }
  const dirty =
    snapshot != null &&
    (Object.keys(current) as (keyof Fields)[]).some((k) => current[k] !== snapshot[k])

  function onDiscard() {
    if (!snapshot) return
    setMission(snapshot.mission)
    setStrategy(snapshot.strategy)
    setPortfolio(snapshot.portfolio)
    setError(null)
  }

  async function onSave(e: React.FormEvent) {
    e.preventDefault()
    if (!workspace) return
    setSaving(true)
    setError(null)
    setSaved(false)
    try {
      await updateWorkspace(workspace.id, {
        mission: mission.trim() || null,
        strategy: strategy.trim() || null,
        portfolio: portfolio.trim() || null,
      })
      setSnapshot(current)
      setSaved(true)
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save company profile")
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <div className="pset">
        <div className="pset-body">
          <p className="settings-loading">Loading company profile…</p>
        </div>
      </div>
    )
  }
  if (!workspace) {
    return (
      <div className="pset">
        <div className="pset-body">
          <SettingsSection
            title="Company Profile"
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
        title="Company Profile"
        meta={identityMeta}
        saved={saved}
        dirty={dirty}
        saving={saving}
        onDiscard={onDiscard}
        formId={FORM_ID}
      />

      <div className="pset-body">
        <h2 className="pset-title">Company Profile</h2>
        <p className="pset-sub">
          Mission, positioning, and voice — context the agents fold into every
          brief and PRD.
        </p>

        <form id={FORM_ID} className="pset-card" onSubmit={onSave}>
          <div className="pset-grid">
            <div className="pset-field pset-field--full">
              <label className="pset-label" htmlFor="cp-mission">Mission</label>
              <textarea
                id="cp-mission"
                className="input"
                rows={2}
                value={mission}
                onChange={(e) => setMission(e.target.value)}
                maxLength={500}
                placeholder="Why the company exists — mission and vision"
              />
            </div>
            <div className="pset-field pset-field--full">
              <label className="pset-label" htmlFor="cp-strategy">Strategy / OKRs</label>
              <textarea
                id="cp-strategy"
                className="input"
                rows={2}
                value={strategy}
                onChange={(e) => setStrategy(e.target.value)}
                maxLength={500}
                placeholder="How you plan to win — current strategy and OKRs"
              />
            </div>
            <div className="pset-field pset-field--full">
              <label className="pset-label" htmlFor="cp-portfolio">Portfolio</label>
              <textarea
                id="cp-portfolio"
                className="input"
                rows={2}
                value={portfolio}
                onChange={(e) => setPortfolio(e.target.value)}
                maxLength={500}
                placeholder="The products / business lines the company runs"
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
