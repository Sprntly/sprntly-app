"use client"

import { useEffect, useState } from "react"
import { profileDisplayName, useWorkspace } from "../../../../context/WorkspaceContext"
import { updateWorkspace } from "../../../../lib/onboarding/store"
import { SettingsMessage, SettingsPaneBar, SettingsSection } from "./SettingsLayout"

const FORM_ID = "pset-company-profile-form"

/**
 * Settings → Company Profile (registration spec 2026-07).
 *
 * The Company-section fields that are settings-only (blue in the spec) plus
 * the optional onboarding ones: mission, strategy, portfolio, ICP (segment /
 * buyer persona / buyer), and tone & voice (brand, tone, colors). Saved to
 * first-class companies columns (+ icp / tone_voice jsonb) via updateWorkspace.
 */

type Fields = {
  mission: string
  strategy: string
  portfolio: string
  icpSegment: string
  icpBuyerPersona: string
  icpBuyer: string
  toneBrand: string
  toneTone: string
  toneColors: string
}

export function CompanyProfileSettings() {
  const { workspace, profile, loading, refresh } = useWorkspace()
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [mission, setMission] = useState("")
  const [strategy, setStrategy] = useState("")
  const [portfolio, setPortfolio] = useState("")
  const [icpSegment, setIcpSegment] = useState("")
  const [icpBuyerPersona, setIcpBuyerPersona] = useState("")
  const [icpBuyer, setIcpBuyer] = useState("")
  const [toneBrand, setToneBrand] = useState("")
  const [toneTone, setToneTone] = useState("")
  const [toneColors, setToneColors] = useState("")
  const [snapshot, setSnapshot] = useState<Fields | null>(null)

  useEffect(() => {
    if (!workspace) return
    const loaded: Fields = {
      mission: workspace.mission ?? "",
      strategy: workspace.strategy ?? "",
      portfolio: workspace.portfolio ?? "",
      icpSegment: workspace.icp.segment ?? "",
      icpBuyerPersona: workspace.icp.buyer_persona ?? "",
      icpBuyer: workspace.icp.buyer ?? "",
      toneBrand: workspace.tone_voice.brand ?? "",
      toneTone: workspace.tone_voice.tone ?? "",
      toneColors: workspace.tone_voice.colors.join(", "),
    }
    setMission(loaded.mission)
    setStrategy(loaded.strategy)
    setPortfolio(loaded.portfolio)
    setIcpSegment(loaded.icpSegment)
    setIcpBuyerPersona(loaded.icpBuyerPersona)
    setIcpBuyer(loaded.icpBuyer)
    setToneBrand(loaded.toneBrand)
    setToneTone(loaded.toneTone)
    setToneColors(loaded.toneColors)
    setSnapshot(loaded)
  }, [workspace])

  const current: Fields = {
    mission, strategy, portfolio, icpSegment, icpBuyerPersona, icpBuyer,
    toneBrand, toneTone, toneColors,
  }
  const dirty =
    snapshot != null &&
    (Object.keys(current) as (keyof Fields)[]).some((k) => current[k] !== snapshot[k])

  function onDiscard() {
    if (!snapshot) return
    setMission(snapshot.mission)
    setStrategy(snapshot.strategy)
    setPortfolio(snapshot.portfolio)
    setIcpSegment(snapshot.icpSegment)
    setIcpBuyerPersona(snapshot.icpBuyerPersona)
    setIcpBuyer(snapshot.icpBuyer)
    setToneBrand(snapshot.toneBrand)
    setToneTone(snapshot.toneTone)
    setToneColors(snapshot.toneColors)
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
        icp: {
          segment: icpSegment.trim() || null,
          buyer_persona: icpBuyerPersona.trim() || null,
          buyer: icpBuyer.trim() || null,
        },
        tone_voice: {
          brand: toneBrand.trim() || null,
          tone: toneTone.trim() || null,
          colors: toneColors
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean),
        },
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
                placeholder="Why the company exists"
              />
            </div>
            <div className="pset-field pset-field--full">
              <label className="pset-label" htmlFor="cp-strategy">Strategy</label>
              <textarea
                id="cp-strategy"
                className="input"
                rows={2}
                value={strategy}
                onChange={(e) => setStrategy(e.target.value)}
                maxLength={500}
                placeholder="How you plan to win"
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

            <div className="pset-field">
              <label className="pset-label" htmlFor="cp-icp-segment">ICP · Segment</label>
              <input
                id="cp-icp-segment"
                className="input"
                value={icpSegment}
                onChange={(e) => setIcpSegment(e.target.value)}
                maxLength={200}
                placeholder="e.g. Mid-market B2B SaaS"
              />
            </div>
            <div className="pset-field">
              <label className="pset-label" htmlFor="cp-icp-persona">ICP · Buyer persona</label>
              <input
                id="cp-icp-persona"
                className="input"
                value={icpBuyerPersona}
                onChange={(e) => setIcpBuyerPersona(e.target.value)}
                maxLength={200}
                placeholder="e.g. Head of Product"
              />
            </div>
            <div className="pset-field pset-field--full">
              <label className="pset-label" htmlFor="cp-icp-buyer">ICP · Who is the buyer</label>
              <input
                id="cp-icp-buyer"
                className="input"
                value={icpBuyer}
                onChange={(e) => setIcpBuyer(e.target.value)}
                maxLength={200}
                placeholder="Who signs off on the purchase"
              />
            </div>

            <div className="pset-field">
              <label className="pset-label" htmlFor="cp-tone-brand">Tone &amp; Voice · Brand</label>
              <input
                id="cp-tone-brand"
                className="input"
                value={toneBrand}
                onChange={(e) => setToneBrand(e.target.value)}
                maxLength={200}
                placeholder="Brand personality in a phrase"
              />
            </div>
            <div className="pset-field">
              <label className="pset-label" htmlFor="cp-tone-tone">Tone &amp; Voice · Tone</label>
              <input
                id="cp-tone-tone"
                className="input"
                value={toneTone}
                onChange={(e) => setToneTone(e.target.value)}
                maxLength={200}
                placeholder="e.g. plain-spoken, confident, playful"
              />
            </div>
            <div className="pset-field pset-field--full">
              <label className="pset-label" htmlFor="cp-tone-colors">Tone &amp; Voice · Colors</label>
              <input
                id="cp-tone-colors"
                className="input"
                value={toneColors}
                onChange={(e) => setToneColors(e.target.value)}
                placeholder="Comma-separated, e.g. #179463, #15201B"
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
