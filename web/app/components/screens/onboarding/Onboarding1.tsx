"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "../../../lib/auth"
import { InterviewLayout } from "../../onboarding/InterviewLayout"
import { useOnboarding } from "../../../context/OnboardingContext"
import {
  BUSINESS_TYPES,
  INDUSTRIES,
  STAGES,
  TECH_STACK_OPTIONS,
} from "../../../lib/onboarding/types"
import { createWorkspace, updateWorkspace } from "../../../lib/onboarding/store"
import { markSkippedFields } from "../../../lib/onboarding/store"

export function Onboarding1() {
  const auth = useAuth()
  const { workspace, refresh, setWorkspace, loading } = useOnboarding()
  const router = useRouter()
  const [companyName, setCompanyName] = useState("")
  const [productDescription, setProductDescription] = useState("")
  const [industry, setIndustry] = useState("B2B SaaS")
  const [industryOther, setIndustryOther] = useState("")
  const [stage, setStage] = useState("Growth")
  const [businessType, setBusinessType] = useState("SaaS")
  const [teamSize, setTeamSize] = useState("")
  const [techStack, setTechStack] = useState<string[]>([])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!workspace) return
    setCompanyName(workspace.display_name)
    setProductDescription(workspace.product_description ?? "")
    setIndustry(workspace.industry ?? "B2B SaaS")
    setStage(workspace.stage ?? "Growth")
    setBusinessType(workspace.business_type ?? "SaaS")
    if (workspace.team_size) setTeamSize(String(workspace.team_size))
    setTechStack(workspace.tech_stack ?? [])
  }, [workspace])

  const resolvedIndustry = industry === "Other" ? industryOther.trim() : industry
  const canContinue =
    companyName.trim().length > 0 &&
    productDescription.trim().length > 0 &&
    resolvedIndustry.length > 0

  async function save(andContinue: boolean) {
    if (auth.kind !== "authed") return
    setError(null)
    setSaving(true)
    try {
      const payload = {
        companyName,
        productDescription,
        industry: resolvedIndustry,
        stage,
        businessType,
        teamSize: teamSize ? Number(teamSize) : null,
        techStack,
      }
      if (workspace) {
        const updated = await updateWorkspace(workspace.id, {
          display_name: payload.companyName.trim(),
          product_description: payload.productDescription.trim(),
          industry: payload.industry,
          stage: payload.stage,
          business_type: payload.businessType,
          team_size: payload.teamSize,
          tech_stack: payload.techStack,
          onboarding_step: andContinue ? 2 : workspace.onboarding_step,
        })
        setWorkspace(updated)
      } else {
        const created = await createWorkspace({ ...payload, userId: auth.user.id })
        setWorkspace(created)
      }
      if (andContinue) router.push("/onboarding/2")
      else await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save workspace.")
    } finally {
      setSaving(false)
    }
  }

  async function onSkip() {
    if (auth.kind !== "authed") return
    await markSkippedFields(auth.user.id, ["team_size", "tech_stack"])
    if (workspace) {
      await updateWorkspace(workspace.id, { onboarding_step: 2 })
      router.push("/onboarding/2")
    } else if (canContinue) {
      await save(true)
    }
  }

  if (loading) return <div className="ob-shell">Loading…</div>

  return (
    <InterviewLayout
      step={1}
      eyebrow="Company & product context"
      title="Tell me about your product"
      agentMessage="I'll use this to seed your Knowledge Graph workspace — company name, what you build, and where you are in your journey. This is the foundation for your first Brief."
      rightPane={
        <PreviewCard
          title="Workspace preview"
          lines={[
            companyName && `Company: ${companyName}`,
            resolvedIndustry && `Industry: ${resolvedIndustry}`,
            stage && `Stage: ${stage}`,
            productDescription && `“${productDescription.slice(0, 120)}${productDescription.length > 120 ? "…" : ""}”`,
          ].filter(Boolean) as string[]}
        />
      }
      onContinue={() => save(true)}
      onSkip={onSkip}
      continueDisabled={!canContinue}
      loading={saving}
    >
      {error && <div className="ob-form-error">{error}</div>}
      <div className="field">
        <label className="field-label">Company name *</label>
        <input className="input" value={companyName} onChange={(e) => setCompanyName(e.target.value)} maxLength={100} />
      </div>
      <div className="field">
        <label className="field-label">Product description *</label>
        <textarea className="textarea" value={productDescription} onChange={(e) => setProductDescription(e.target.value)} maxLength={500} rows={4} placeholder="What your product does, who it serves, core value proposition…" />
      </div>
      <div className="field">
        <label className="field-label">Industry *</label>
        <select className="input" value={industry} onChange={(e) => setIndustry(e.target.value)}>
          {INDUSTRIES.map((i) => <option key={i}>{i}</option>)}
        </select>
        {industry === "Other" && (
          <input className="input" style={{ marginTop: 8 }} value={industryOther} onChange={(e) => setIndustryOther(e.target.value)} placeholder="Your industry" />
        )}
      </div>
      <div className="field">
        <label className="field-label">Stage *</label>
        <div className="ob-radio-row">
          {STAGES.map((s) => (
            <label key={s} className={`ob-radio-chip ${stage === s ? "on" : ""}`}>
              <input type="radio" name="stage" checked={stage === s} onChange={() => setStage(s)} />
              {s}
            </label>
          ))}
        </div>
      </div>
      <div className="field">
        <label className="field-label">Business type *</label>
        <select className="input" value={businessType} onChange={(e) => setBusinessType(e.target.value)}>
          {BUSINESS_TYPES.map((b) => <option key={b}>{b}</option>)}
        </select>
      </div>
      <div className="field">
        <label className="field-label">Team size (optional)</label>
        <input type="number" className="input" min={1} value={teamSize} onChange={(e) => setTeamSize(e.target.value)} placeholder="Total headcount" />
      </div>
      <div className="field">
        <label className="field-label">Tech stack (optional)</label>
        <div className="ob-chip-row">
          {TECH_STACK_OPTIONS.map((t) => (
            <button
              key={t}
              type="button"
              className={`metric-chip ${techStack.includes(t) ? "selected" : ""}`}
              onClick={() => setTechStack((prev) => prev.includes(t) ? prev.filter((x) => x !== t) : [...prev, t])}
            >
              {t}
            </button>
          ))}
        </div>
      </div>
    </InterviewLayout>
  )
}

function PreviewCard({ title, lines }: { title: string; lines: string[] }) {
  return (
    <div>
      <div className="ob-preview-label">Live preview</div>
      <h3 className="ob-preview-title">{title}</h3>
      {lines.length === 0 ? (
        <p className="ob-preview-empty">Fill in the form to see your workspace take shape.</p>
      ) : (
        <ul className="ob-preview-list">{lines.map((l) => <li key={l}>{l}</li>)}</ul>
      )}
    </div>
  )
}
