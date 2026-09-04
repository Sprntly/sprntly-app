"use client"

import { useEffect, useRef, useState } from "react"
import { profileDisplayName, useWorkspace } from "../../../../context/WorkspaceContext"
import { updateWorkspace } from "../../../../lib/onboarding/store"
import { companyDocsApi } from "../../../../lib/api"
import { SettingsMessage, SettingsPaneBar, SettingsSection } from "./SettingsLayout"

const FORM_ID = "pset-company-profile-form"

/**
 * Settings → Company Profile — company website, mission & vision, strategy /
 * OKRs, and portfolio (planning cycle lives in Process & Planning). Saved to
 * first-class companies columns via updateWorkspace. The ICP and tone & voice
 * editors were pruned in v6 — the flow no longer collects them and nothing
 * downstream consumed them.
 *
 * THIS IS NOW THE ONLY PLACE THREE OF THESE ARE EDITED. Onboarding's first step
 * was cut back to company name / website and product name / website on
 * 2026-09-03, so mission, strategy and portfolio are no longer asked for during
 * signup — someone who has not seen the product yet cannot usefully write down
 * their OKRs, and this is where they come back to once they can. No data moved:
 * these are the same columns that step always wrote, so every company onboarded
 * before the change finds exactly what it entered, here.
 *
 * The strategy DOC UPLOAD came with them. It used to exist only on that step,
 * which meant cutting the step would have removed the ability to attach a
 * strategy doc or board deck from the product entirely; it posts to the same
 * `company_strategy` endpoint it always did.
 */

type Fields = {
  website: string
  mission: string
  strategy: string
  portfolio: string
}

export function CompanyProfileSettings() {
  const { workspace, profile, loading, refresh } = useWorkspace()
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [website, setWebsite] = useState("")
  const [mission, setMission] = useState("")
  const [strategy, setStrategy] = useState("")
  const [portfolio, setPortfolio] = useState("")
  const [snapshot, setSnapshot] = useState<Fields | null>(null)

  useEffect(() => {
    if (!workspace) return
    const loaded: Fields = {
      // Falls back to the PRODUCT's site for a company onboarded before the two
      // became separate columns (migration 20260903150000) — theirs is recorded
      // on the product row, and showing this blank would read as having lost it.
      website: workspace.website ?? workspace.product?.website ?? "",
      mission: workspace.mission ?? "",
      strategy: workspace.strategy ?? "",
      portfolio: workspace.portfolio ?? "",
    }
    setWebsite(loaded.website)
    setMission(loaded.mission)
    setStrategy(loaded.strategy)
    setPortfolio(loaded.portfolio)
    setSnapshot(loaded)
  }, [workspace])

  const current: Fields = { website, mission, strategy, portfolio }
  const dirty =
    snapshot != null &&
    (Object.keys(current) as (keyof Fields)[]).some((k) => current[k] !== snapshot[k])

  function onDiscard() {
    if (!snapshot) return
    setWebsite(snapshot.website)
    setMission(snapshot.mission)
    setStrategy(snapshot.strategy)
    setPortfolio(snapshot.portfolio)
    setError(null)
  }

  // The strategy doc upload, moved here with the field it sits beside. Same
  // endpoint and same `company_strategy` doc_type the onboarding step used, and
  // deliberately OUTSIDE the form's save: it posts on pick, so a failed upload
  // never blocks saving the text, and saving the text never re-posts the file.
  const strategyFileRef = useRef<HTMLInputElement | null>(null)
  const [strategyDocNotice, setStrategyDocNotice] = useState<string | null>(null)
  const [strategyDocUploading, setStrategyDocUploading] = useState(false)

  async function onPickStrategyDoc(file: File | null) {
    if (!file) return
    setStrategyDocNotice(null)
    setStrategyDocUploading(true)
    try {
      await companyDocsApi.upload(file, "company_strategy")
      setStrategyDocNotice(`${file.name} · uploaded just now.`)
    } catch {
      setStrategyDocNotice(`Couldn't upload "${file.name}" just now — try again.`)
    } finally {
      setStrategyDocUploading(false)
    }
  }

  async function onSave(e: React.FormEvent) {
    e.preventDefault()
    if (!workspace) return
    setSaving(true)
    setError(null)
    setSaved(false)
    try {
      await updateWorkspace(workspace.id, {
        website: website.trim() || null,
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
          Website, mission, strategy and portfolio — context the agents fold
          into every brief and PRD.
        </p>

        <form id={FORM_ID} className="pset-card" onSubmit={onSave}>
          <div className="pset-grid">
            <div className="pset-field pset-field--full">
              <label className="pset-label" htmlFor="cp-website">Company website</label>
              <input
                id="cp-website"
                className="input"
                type="url"
                value={website}
                onChange={(e) => setWebsite(e.target.value)}
                maxLength={300}
                placeholder="https://yourcompany.com"
                autoComplete="url"
              />
            </div>
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
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                }}
              >
                <label className="pset-label" htmlFor="cp-strategy">Strategy / OKRs</label>
                <button
                  type="button"
                  className="btn btn-ghost"
                  onClick={() => strategyFileRef.current?.click()}
                  disabled={strategyDocUploading}
                >
                  {strategyDocUploading ? "Uploading…" : "Upload"}
                </button>
              </div>
              <textarea
                id="cp-strategy"
                className="input"
                rows={2}
                value={strategy}
                onChange={(e) => setStrategy(e.target.value)}
                maxLength={500}
                placeholder="How you plan to win — current strategy and OKRs"
              />
              <input
                ref={strategyFileRef}
                type="file"
                style={{ display: "none" }}
                onChange={(e) => void onPickStrategyDoc(e.target.files?.[0] ?? null)}
                aria-label="Strategy document"
              />
              {strategyDocNotice && (
                <p className="pset-hint" role="status">
                  {strategyDocNotice}
                </p>
              )}
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
