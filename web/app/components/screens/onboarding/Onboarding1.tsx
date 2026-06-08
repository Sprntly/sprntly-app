"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "../../../lib/auth"
import { InterviewLayout, useFieldValidation } from "../../onboarding/InterviewLayout"
import { useOnboarding } from "../../../context/OnboardingContext"
import {
  validateProductWebsite,
  normalizeProductWebsite,
} from "../../../lib/onboarding/product-helpers"
import {
  BUSINESS_TYPES,
  INDUSTRIES,
  STAGES,
  TECH_STACK_OPTIONS,
} from "../../../lib/onboarding/types"
import {
  createWorkspace,
  markSkippedFields,
  updateWorkspace,
  upsertPrimaryProduct,
} from "../../../lib/onboarding/store"

export function Onboarding1() {
  const auth = useAuth()
  const { workspace, refresh, setWorkspace, loading } = useOnboarding()
  const router = useRouter()
  const [companyName, setCompanyName] = useState("")
  const [productName, setProductName] = useState("")
  const [productWebsite, setProductWebsite] = useState("")
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
    setProductName(workspace.product?.name ?? workspace.display_name)
    setProductWebsite(workspace.product?.website ?? "")
    setIndustry(workspace.industry ?? "B2B SaaS")
    setStage(workspace.stage ?? "Growth")
    setBusinessType(workspace.business_type ?? "SaaS")
    if (workspace.team_size) setTeamSize(String(workspace.team_size))
    setTechStack(workspace.tech_stack ?? [])
  }, [workspace])

  const resolvedIndustry = industry === "Other" ? industryOther.trim() : industry
  const canContinue =
    companyName.trim().length > 0 &&
    productName.trim().length > 0 &&
    resolvedIndustry.length > 0

  const { errors, validate, clearError, containerRef } = useFieldValidation(
    () => [
      {
        key: "companyName",
        valid: companyName.trim().length > 0,
        message: "Enter your company name.",
      },
      {
        key: "productName",
        valid: productName.trim().length > 0,
        message: "Enter your primary product name.",
      },
      {
        key: "industry",
        valid: resolvedIndustry.length > 0,
        message: "Tell us your industry.",
      },
    ],
  )

  async function save(andContinue: boolean) {
    if (auth.kind !== "authed") return
    setError(null)
    if (andContinue && !validate().ok) return
    const websiteErr = validateProductWebsite(productWebsite)
    if (websiteErr) {
      setError(websiteErr)
      return
    }
    const website = normalizeProductWebsite(productWebsite)
    setSaving(true)
    try {
      const companyPayload = {
        companyName,
        productName,
        productWebsite: website,
        industry: resolvedIndustry,
        stage,
        businessType,
        teamSize: teamSize ? Number(teamSize) : null,
        techStack,
      }
      if (workspace) {
        const updated = await updateWorkspace(workspace.id, {
          display_name: companyPayload.companyName.trim(),
          industry: companyPayload.industry,
          stage: companyPayload.stage,
          business_type: companyPayload.businessType,
          team_size: companyPayload.teamSize,
          tech_stack: companyPayload.techStack,
          onboarding_step: andContinue ? 2 : workspace.onboarding_step,
        })
        const product = await upsertPrimaryProduct(workspace.id, {
          name: companyPayload.productName,
          website: companyPayload.productWebsite,
        })
        setWorkspace({ ...updated, product })
      } else {
        const created = await createWorkspace({
          ...companyPayload,
          userId: auth.user.id,
        })
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
      title="Tell me about your company and product"
      agentMessage="A company can have multiple products over time — we'll start with your primary product. Company name, product name, and where you are in your journey seed your Knowledge Graph workspace."
      rightPane={
        <PreviewCard
          title="Workspace preview"
          lines={[
            companyName && `Company: ${companyName}`,
            productName && `Product: ${productName}`,
            productWebsite && `Website: ${productWebsite}`,
            resolvedIndustry && `Industry: ${resolvedIndustry}`,
            stage && `Stage: ${stage}`,
          ].filter(Boolean) as string[]}
        />
      }
      onContinue={() => save(true)}
      onSkip={onSkip}
      loading={saving}
    >
      <div ref={containerRef}>
      {error && <div className="ob-form-error">{error}</div>}
      <div className={`field ${errors.companyName ? "has-error" : ""}`} data-field="companyName">
        <label className="field-label">Company name *</label>
        <input
          className="input"
          value={companyName}
          onChange={(e) => {
            setCompanyName(e.target.value)
            clearError("companyName")
          }}
          maxLength={100}
          placeholder="Legal or brand name of your organization"
        />
        {errors.companyName && <p className="field-error">{errors.companyName}</p>}
      </div>
      <div className={`field ${errors.productName ? "has-error" : ""}`} data-field="productName">
        <label className="field-label">Product name *</label>
        <input
          className="input"
          value={productName}
          onChange={(e) => {
            setProductName(e.target.value)
            clearError("productName")
          }}
          maxLength={100}
          placeholder="The product you're onboarding (you can add more later)"
        />
        <p className="field-hint">One company can have multiple products; this is your primary one.</p>
        {errors.productName && <p className="field-error">{errors.productName}</p>}
      </div>
      <div className="field">
        <label className="field-label">Product website</label>
        <input
          className="input"
          type="url"
          value={productWebsite}
          onChange={(e) => setProductWebsite(e.target.value)}
          placeholder="https://yourproduct.com"
          autoComplete="url"
        />
      </div>
      <div className={`field ${errors.industry ? "has-error" : ""}`} data-field="industry">
        <label className="field-label">Industry *</label>
        <select
          className="input"
          value={industry}
          onChange={(e) => {
            setIndustry(e.target.value)
            clearError("industry")
          }}
        >
          {INDUSTRIES.map((i) => (
            <option key={i}>
              {i}
            </option>
          ))}
        </select>
        {industry === "Other" && (
          <input
            className="input"
            style={{ marginTop: 8 }}
            value={industryOther}
            onChange={(e) => {
              setIndustryOther(e.target.value)
              clearError("industry")
            }}
            placeholder="Your industry"
          />
        )}
        {errors.industry && <p className="field-error">{errors.industry}</p>}
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
          {BUSINESS_TYPES.map((b) => (
            <option key={b}>
              {b}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label className="field-label">Team size (optional)</label>
        <input
          type="number"
          className="input"
          min={1}
          value={teamSize}
          onChange={(e) => setTeamSize(e.target.value)}
          placeholder="Total headcount"
        />
      </div>
      <div className="field">
        <label className="field-label">Tech stack (optional)</label>
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
        <ul className="ob-preview-list">
          {lines.map((l) => (
            <li key={l}>{l}</li>
          ))}
        </ul>
      )}
    </div>
  )
}
