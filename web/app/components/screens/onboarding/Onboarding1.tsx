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
import { STAGES, TECH_STACK_OPTIONS } from "../../../lib/onboarding/types"
import {
  createWorkspace,
  markSkippedFields,
  updateWorkspace,
  upsertPrimaryProduct,
} from "../../../lib/onboarding/store"
import { onboardingApi } from "../../../lib/api"

export function Onboarding1() {
  const auth = useAuth()
  const { workspace, refresh, setWorkspace, setWebsiteAnalysis, loading } =
    useOnboarding()
  const router = useRouter()
  const [companyName, setCompanyName] = useState("")
  const [productName, setProductName] = useState("")
  const [productWebsite, setProductWebsite] = useState("")
  const [stage, setStage] = useState("Growth")
  const [teamSize, setTeamSize] = useState("")
  const [techStack, setTechStack] = useState<string[]>([])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!workspace) return
    setCompanyName(workspace.display_name)
    setProductName(workspace.product?.name ?? workspace.display_name)
    setProductWebsite(workspace.product?.website ?? "")
    setStage(workspace.stage ?? "Growth")
    if (workspace.team_size) setTeamSize(String(workspace.team_size))
    setTechStack(workspace.tech_stack ?? [])
  }, [workspace])

  const canContinue =
    companyName.trim().length > 0 && productName.trim().length > 0

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
    ],
  )

  // Kick off website analysis in the BACKGROUND. Never blocks navigation and
  // never throws into the save flow — the endpoint always answers 200, and we
  // additionally swallow transport failures so onboarding completes even when
  // analysis never returns. The result is stashed on the onboarding context
  // for later steps (business context, success metrics) to read.
  function startWebsiteAnalysis(website: string | null) {
    if (!website) return
    void onboardingApi
      .analyzeWebsite(website)
      .then((res) => setWebsiteAnalysis(res))
      .catch(() => {
        /* best-effort: leave analysis null → manual fallback downstream */
      })
  }

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
        stage,
        teamSize: teamSize ? Number(teamSize) : null,
        techStack,
      }
      if (workspace) {
        const updated = await updateWorkspace(workspace.id, {
          display_name: companyPayload.companyName.trim(),
          stage: companyPayload.stage,
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
      // Fire-and-forget; intentionally NOT awaited so navigation isn't blocked.
      startWebsiteAnalysis(website)
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
      agentMessage="A company can have multiple products over time — we'll start with your primary product. Drop in your website and I'll read it in the background to pre-fill your industry, business type, and context as we go."
      rightPane={
        <PreviewCard
          title="Workspace preview"
          lines={[
            companyName && `Company: ${companyName}`,
            productName && `Product: ${productName}`,
            productWebsite && `Website: ${productWebsite}`,
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
        <p className="field-hint">
          We&apos;ll read this to draft your industry, business type, and
          context — you can confirm or change everything later.
        </p>
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
