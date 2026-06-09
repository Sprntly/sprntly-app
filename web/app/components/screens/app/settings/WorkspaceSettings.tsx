"use client"

import { useEffect, useState } from "react"
import { useWorkspace } from "../../../../context/WorkspaceContext"
import {
  normalizeProductWebsite,
  validateProductWebsite,
} from "../../../../lib/onboarding/product-helpers"
import {
  updateWorkspace,
  upsertPrimaryProduct,
} from "../../../../lib/onboarding/store"
import {
  BUSINESS_TYPES,
  INDUSTRIES,
  STAGES,
  TECH_STACK_OPTIONS,
} from "../../../../lib/onboarding/types"
import { SettingsMessage, SettingsSection } from "./SettingsLayout"

export function WorkspaceSettings() {
  const { workspace, loading, refresh } = useWorkspace()
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [companyName, setCompanyName] = useState("")
  const [productName, setProductName] = useState("")
  const [productWebsite, setProductWebsite] = useState("")
  const [industry, setIndustry] = useState("B2B SaaS")
  const [industryOther, setIndustryOther] = useState("")
  const [stage, setStage] = useState("Growth")
  const [businessType, setBusinessType] = useState("SaaS")
  const [teamSize, setTeamSize] = useState("")
  const [techStack, setTechStack] = useState<string[]>([])
  const [competitors, setCompetitors] = useState("")

  useEffect(() => {
    if (!workspace) return
    setCompanyName(workspace.display_name)
    setProductName(workspace.product?.name ?? "")
    setProductWebsite(workspace.product?.website ?? "")
    setIndustry(workspace.industry ?? "B2B SaaS")
    setStage(workspace.stage ?? "Growth")
    setBusinessType(workspace.business_type ?? "SaaS")
    setTeamSize(workspace.team_size ? String(workspace.team_size) : "")
    setTechStack(workspace.tech_stack ?? [])
    setCompetitors((workspace.competitors ?? []).join(", "))
  }, [workspace])

  const resolvedIndustry = industry === "Other" ? industryOther.trim() : industry

  async function onSave(e: React.FormEvent) {
    e.preventDefault()
    if (!workspace) return
    const websiteErr = validateProductWebsite(productWebsite)
    if (websiteErr) {
      setError(websiteErr)
      return
    }
    setSaving(true)
    setError(null)
    setSaved(false)
    try {
      await updateWorkspace(workspace.id, {
        display_name: companyName.trim(),
        industry: resolvedIndustry,
        stage,
        business_type: businessType,
        team_size: teamSize ? Number(teamSize) : null,
        tech_stack: techStack,
        competitors: competitors
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean)
          .slice(0, 5),
      })
      await upsertPrimaryProduct(workspace.id, {
        name: productName.trim(),
        website: normalizeProductWebsite(productWebsite),
      })
      await refresh()
      setSaved(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save workspace")
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <p className="settings-loading">Loading workspace…</p>
  if (!workspace) {
    return (
      <SettingsSection title="Workspace" sub="Complete onboarding to create your workspace.">
        <p className="settings-placeholder">
          <a href="/onboarding/business-info">Continue onboarding →</a>
        </p>
      </SettingsSection>
    )
  }

  return (
    <SettingsSection
      title="Workspace"
      sub="Company and primary product context used for Briefs and recommendations."
    >
      <form onSubmit={onSave}>
        <div className="field">
          <label className="field-label">Company name</label>
          <input className="input" value={companyName} onChange={(e) => setCompanyName(e.target.value)} maxLength={100} required />
        </div>
        <div className="field">
          <label className="field-label">Product name</label>
          <input className="input" value={productName} onChange={(e) => setProductName(e.target.value)} maxLength={100} required />
        </div>
        <div className="field">
          <label className="field-label">Product website</label>
          <input className="input" type="url" value={productWebsite} onChange={(e) => setProductWebsite(e.target.value)} placeholder="https://…" />
        </div>
        <div className="field">
          <label className="field-label">Industry</label>
          <select className="input" value={industry} onChange={(e) => setIndustry(e.target.value)}>
            {INDUSTRIES.map((i) => (
              <option key={i}>{i}</option>
            ))}
          </select>
          {industry === "Other" && (
            <input className="input" style={{ marginTop: 8 }} value={industryOther} onChange={(e) => setIndustryOther(e.target.value)} />
          )}
        </div>
        <div className="field">
          <label className="field-label">Stage</label>
          <select className="input" value={stage} onChange={(e) => setStage(e.target.value)}>
            {STAGES.map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </div>
        <div className="field">
          <label className="field-label">Business type</label>
          <select className="input" value={businessType} onChange={(e) => setBusinessType(e.target.value)}>
            {BUSINESS_TYPES.map((b) => (
              <option key={b}>{b}</option>
            ))}
          </select>
        </div>
        <div className="field">
          <label className="field-label">Team size</label>
          <input type="number" className="input" min={1} value={teamSize} onChange={(e) => setTeamSize(e.target.value)} />
        </div>
        <div className="field">
          <label className="field-label">Primary competitors</label>
          <input className="input" value={competitors} onChange={(e) => setCompetitors(e.target.value)} placeholder="Up to 5, comma-separated" />
        </div>
        <div className="field">
          <label className="field-label">Tech stack</label>
          <div className="ob-chip-row">
            {TECH_STACK_OPTIONS.map((t) => (
              <button
                key={t}
                type="button"
                className={`metric-chip ${techStack.includes(t) ? "selected" : ""}`}
                onClick={() =>
                  setTechStack((prev) =>
                    prev.includes(t) ? prev.filter((x) => x !== t) : [...prev, t],
                  )
                }
              >
                {t}
              </button>
            ))}
          </div>
        </div>
        {error && <SettingsMessage kind="error">{error}</SettingsMessage>}
        {saved && <SettingsMessage kind="success">Workspace saved.</SettingsMessage>}
        <button type="submit" className="btn btn-primary" disabled={saving}>
          {saving ? "Saving…" : "Save workspace"}
        </button>
      </form>
    </SettingsSection>
  )
}
