"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "../../../lib/auth"
import { useFieldValidation } from "../../onboarding/InterviewLayout"
import { OnboardingChrome } from "../../onboarding/OnboardingChrome"
import { OptionalDisclosure } from "../../onboarding/OptionalDisclosure"
import { useOnboarding } from "../../../context/OnboardingContext"
import {
  validateProductWebsite,
  normalizeProductWebsite,
} from "../../../lib/onboarding/product-helpers"
import { requiredFor } from "../../../lib/onboarding/validation"
import {
  createWorkspace,
  updateWorkspace,
  upsertPrimaryProduct,
  markSkippedFields,
} from "../../../lib/onboarding/store"
import { saveDraft, loadDraft, clearDraft } from "../../../lib/onboarding/useFormDraft"

const DRAFT_KEY = "company-step"

/**
 * Onboarding step 01 — "Tell us about your company" (registration spec 2026-07,
 * Company section). Split out of the old combined business-info step.
 *
 * Fields: company name* and company website* (mandatory for COMPANY accounts,
 * skippable for PERSONAL — see requiredFor), with mission + strategy behind an
 * optional disclosure. The website doubles as the primary product's initial
 * website (the products row is this codebase's single source of truth for
 * "the company's website"); the product step lets the PM change it.
 *
 * On Continue we persist the company (+ product website seed), kick the
 * website analysis in the BACKGROUND (no interstitial — the result lands on
 * the onboarding context for later steps/settings), and advance to product.
 */
export function CompanyStep() {
  const auth = useAuth()
  const { workspace, profile, setWorkspace, startWebsiteAnalysis, loading } =
    useOnboarding()
  const router = useRouter()

  const draft = loadDraft(DRAFT_KEY)
  const [companyName, setCompanyName] = useState((draft?.companyName as string) ?? "")
  const [website, setWebsite] = useState((draft?.website as string) ?? "")
  const [mission, setMission] = useState((draft?.mission as string) ?? "")
  const [strategy, setStrategy] = useState((draft?.strategy as string) ?? "")

  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const isCompany = (profile?.account_type ?? "company") === "company"

  // Seed from the saved workspace on first load (draft takes priority).
  useEffect(() => {
    if (!workspace) return
    if (draft) return
    setCompanyName(workspace.display_name)
    setWebsite(workspace.product?.website ?? "")
    setMission(workspace.mission ?? "")
    setStrategy(workspace.strategy ?? "")
  }, [workspace]) // eslint-disable-line react-hooks/exhaustive-deps

  // Save draft on visibility change (tab switch / minimize) — not per keystroke.
  useEffect(() => {
    const onHide = () => {
      if (document.hidden)
        saveDraft(DRAFT_KEY, { companyName, website, mission, strategy })
    }
    document.addEventListener("visibilitychange", onHide)
    return () => document.removeEventListener("visibilitychange", onHide)
  }, [companyName, website, mission, strategy])

  const { errors, validate, clearError, containerRef } = useFieldValidation(() => [
    {
      key: "companyName",
      valid: companyName.trim().length > 0,
      message: "Enter your company name.",
    },
    requiredFor(isCompany, {
      key: "website",
      valid: website.trim().length > 0,
      message: "Enter your company website.",
    }),
  ])

  async function save() {
    if (auth.kind !== "authed") return
    setError(null)
    if (!validate().ok) return
    // Shape-check the website whenever one was typed (both account types).
    const websiteErr = validateProductWebsite(website)
    if (websiteErr) {
      setError(websiteErr)
      return
    }
    const normalizedSite = normalizeProductWebsite(website)
    setSaving(true)
    try {
      const skipped: string[] = []
      if (!isCompany && !website.trim()) skipped.push("company_website")
      let ws = workspace
      if (workspace) {
        const updated = await updateWorkspace(workspace.id, {
          display_name: companyName.trim(),
          mission: mission.trim() || null,
          strategy: strategy.trim() || null,
          onboarding_step: 2,
        })
        const product = await upsertPrimaryProduct(workspace.id, {
          name: workspace.product?.name ?? companyName.trim(),
          website: normalizedSite || workspace.product?.website || null,
        })
        ws = { ...updated, product }
        setWorkspace(ws)
      } else {
        const created = await createWorkspace({
          companyName,
          // The product step refines this; the company name is the natural seed.
          productName: companyName,
          productWebsite: normalizedSite,
          accountType: profile?.account_type ?? "company",
          mission,
          strategy,
          userId: auth.user.id,
        })
        ws = created
        setWorkspace(created)
      }
      if (skipped.length) await markSkippedFields(auth.user.id, skipped)
      clearDraft(DRAFT_KEY)
      // Kick off the website analysis in the BACKGROUND and move on. The job
      // runs server-side; the provider outlives this navigation.
      const analysisSite = ws?.product?.website ?? normalizedSite
      if (ws && analysisSite) startWebsiteAnalysis(analysisSite, ws.id)
      router.push("/onboarding/product")
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save your company.")
      setSaving(false)
    }
  }

  if (loading) return <div className="onb-shell">Loading…</div>

  return (
    <OnboardingChrome
      step={1}
      saveLabel="Saved · auto-saves"
      title={
        <>
          Tell us about your <em>company.</em>
        </>
      }
      subtitle="Just the essentials — your name and website anchor the workspace. Everything else can wait for Settings."
      footerMeta={
        isCompany
          ? "Name and website are required — the rest is optional."
          : "Everything here is optional — add what you like."
      }
      onContinue={() => void save()}
      continueDisabled={saving}
      loading={saving}
    >
      <div ref={containerRef}>
        {error && <div className="onb-form-error">{error}</div>}

        <div className="form-grid">
          <div className="field full" data-field="companyName">
            <div className="field-l">
              Company name <span className="req">*</span>
            </div>
            <input
              className={`inp ${errors.companyName ? "has-error" : ""}`}
              value={companyName}
              onChange={(e) => {
                setCompanyName(e.target.value)
                clearError("companyName")
              }}
              maxLength={100}
              placeholder="Legal or brand name of your organization"
            />
            {errors.companyName && (
              <p className="onb-field-error">{errors.companyName}</p>
            )}
          </div>

          <div className="field full" data-field="website">
            <div className="field-l">
              Company website{" "}
              {isCompany ? (
                <span className="req">*</span>
              ) : (
                <span className="opt">optional</span>
              )}
            </div>
            <input
              className={`inp ${errors.website ? "has-error" : ""}`}
              type="url"
              value={website}
              onChange={(e) => {
                setWebsite(e.target.value)
                clearError("website")
              }}
              placeholder="https://yourcompany.com"
              autoComplete="url"
            />
            {errors.website && <p className="onb-field-error">{errors.website}</p>}
            <p className="onb-field-hint">
              We&apos;ll read this to draft your industry, business type, and
              business context in the background.
            </p>
          </div>
        </div>

        <OptionalDisclosure label="Add mission & strategy">
          <div className="form-grid">
            <div className="field full">
              <div className="field-l">
                Mission <span className="opt">optional</span>
              </div>
              <textarea
                className="inp"
                rows={2}
                value={mission}
                onChange={(e) => setMission(e.target.value)}
                maxLength={500}
                placeholder="Why the company exists, in a sentence or two"
              />
            </div>
            <div className="field full">
              <div className="field-l">
                Strategy <span className="opt">optional</span>
              </div>
              <textarea
                className="inp"
                rows={2}
                value={strategy}
                onChange={(e) => setStrategy(e.target.value)}
                maxLength={500}
                placeholder="How you plan to win — the current strategic focus"
              />
            </div>
          </div>
        </OptionalDisclosure>
      </div>
    </OnboardingChrome>
  )
}
