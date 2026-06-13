"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "../../../lib/auth"
import { useFieldValidation } from "../../onboarding/InterviewLayout"
import { OnboardingChrome } from "../../onboarding/OnboardingChrome"
import { useOnboarding } from "../../../context/OnboardingContext"
import {
  validateProductWebsite,
  normalizeProductWebsite,
} from "../../../lib/onboarding/product-helpers"
import { STAGES, TECH_STACK_OPTIONS } from "../../../lib/onboarding/types"
import {
  completeOnboarding,
  createWorkspace,
  updateWorkspace,
  upsertPrimaryProduct,
} from "../../../lib/onboarding/store"
import { saveDraft, loadDraft, clearDraft } from "../../../lib/onboarding/useFormDraft"

const DRAFT_KEY = "business-info"

/**
 * Onboarding step 01 — "Company" page (v4 .onb-* design).
 *
 * Collects the company name, primary product name, product website, stage,
 * team size and tech stack. It NO LONGER fires the website analysis in the
 * background. Instead, on Continue it persists the workspace and then
 * navigates to the blocking `/onboarding/analyzing` interstitial, which awaits
 * the analysis before forwarding to the metrics step. This keeps the analysis
 * result deterministic for the metrics page while never trapping the user (the
 * interstitial always proceeds — see Analyzing).
 *
 * This page deliberately renders NO metric fields — those live on the metrics
 * step that follows the interstitial.
 */
export function BusinessInfo() {
  const auth = useAuth()
  const { workspace, refresh, setWorkspace, loading } = useOnboarding()
  const router = useRouter()
  // Restore draft from localStorage (survives tab switches)
  const draft = loadDraft(DRAFT_KEY)
  const [companyName, setCompanyName] = useState((draft?.companyName as string) ?? "")
  const [productName, setProductName] = useState((draft?.productName as string) ?? "")
  const [productWebsite, setProductWebsite] = useState((draft?.productWebsite as string) ?? "")
  const [stage, setStage] = useState((draft?.stage as string) ?? "Growth")
  const [teamSize, setTeamSize] = useState((draft?.teamSize as string) ?? "")
  const [techStack, setTechStack] = useState<string[]>((draft?.techStack as string[]) ?? [])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Seed from workspace on first load (only if no draft exists)
  useEffect(() => {
    if (!workspace) return
    if (draft) return // draft takes priority — user already typed something
    setCompanyName(workspace.display_name)
    setProductName(workspace.product?.name ?? workspace.display_name)
    setProductWebsite(workspace.product?.website ?? "")
    setStage(workspace.stage ?? "Growth")
    if (workspace.team_size) setTeamSize(String(workspace.team_size))
    setTechStack(workspace.tech_stack ?? [])
  }, [workspace]) // eslint-disable-line react-hooks/exhaustive-deps

  // Save draft on visibility change (tab switch / minimize) — not on every keystroke
  useEffect(() => {
    const onHide = () => {
      if (document.hidden) saveDraft(DRAFT_KEY, { companyName, productName, productWebsite, stage, teamSize, techStack })
    }
    document.addEventListener("visibilitychange", onHide)
    return () => document.removeEventListener("visibilitychange", onHide)
  }, [companyName, productName, productWebsite, stage, teamSize, techStack])

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
          // Next numbered step is the metrics page (route 2). The interstitial
          // is unnumbered, so we never persist its route as a resume target.
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
      clearDraft(DRAFT_KEY)
      if (andContinue) {
        // Route to the blocking analyzing interstitial; it runs the (now
        // awaited) website analysis, then forwards to the metrics page. No
        // background analysis is fired here anymore.
        router.push("/onboarding/analyzing")
      } else {
        await refresh()
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save workspace.")
    } finally {
      setSaving(false)
    }
  }

  const [skipping, setSkipping] = useState(false)

  async function skipToSettings() {
    if (auth.kind !== "authed") return
    setSkipping(true)
    try {
      // Save minimal workspace if it exists, then mark onboarding complete
      if (workspace) {
        await completeOnboarding(workspace.id, auth.user.id)
      }
      router.push("/settings")
    } catch {
      setSkipping(false)
    }
  }

  if (loading) return <div className="onb-shell">Loading…</div>

  return (
    <OnboardingChrome
      step={1}
      saveLabel="Saved · auto-saves"
      title={
        <>
          Let&apos;s get to know your <em>company.</em>
        </>
      }
      subtitle="A name and your website anchor the whole workspace — we'll read the site to draft your industry, metrics, and context for the next step. You can change everything later in Settings."
      footerMeta={
        <span style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span>{canContinue ? "Ready to analyze your business" : "Company & product name required"}</span>
          <button
            type="button"
            onClick={skipToSettings}
            disabled={skipping || !workspace}
            style={{
              background: "none", border: "none", cursor: "pointer",
              fontSize: 12, color: "var(--accent, #179463)", textDecoration: "underline",
              padding: 0, fontWeight: 500,
            }}
          >
            {skipping ? "Redirecting…" : "Skip to Settings →"}
          </button>
        </span>
      }
      onContinue={() => save(true)}
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

          <div className="field full" data-field="productName">
            <div className="field-l">
              Primary product <span className="req">*</span>
            </div>
            <input
              className={`inp ${errors.productName ? "has-error" : ""}`}
              value={productName}
              onChange={(e) => {
                setProductName(e.target.value)
                clearError("productName")
              }}
              maxLength={100}
              placeholder="The product you're onboarding (you can add more later)"
            />
            <p className="onb-field-hint">
              One company can have multiple products; this is your primary one.
            </p>
            {errors.productName && (
              <p className="onb-field-error">{errors.productName}</p>
            )}
          </div>

          <div className="field full">
            <div className="field-l">
              Product website <span className="opt">optional</span>
            </div>
            <input
              className="inp"
              type="url"
              value={productWebsite}
              onChange={(e) => setProductWebsite(e.target.value)}
              placeholder="https://yourproduct.com"
              autoComplete="url"
            />
            <p className="onb-field-hint">
              We&apos;ll read this to draft your industry, business type, and
              metrics — you can confirm or change everything on the next step.
            </p>
          </div>
        </div>

        <div className="onb-section" style={{ marginTop: 22 }}>
          <div className="onb-section-h">Stage</div>
          <div className="onb-chip-row">
            {STAGES.map((s) => (
              <button
                key={s}
                type="button"
                className={`onb-chip ${stage === s ? "sel" : ""}`}
                aria-pressed={stage === s}
                onClick={() => setStage(s)}
              >
                {s}
              </button>
            ))}
          </div>
        </div>

        <div className="onb-section">
          <div className="onb-section-h">
            Team size <span className="opt">optional</span>
          </div>
          <input
            type="number"
            className="inp"
            min={1}
            value={teamSize}
            onChange={(e) => setTeamSize(e.target.value)}
            placeholder="Total headcount"
            style={{ maxWidth: 220 }}
          />
        </div>

        <div className="onb-section">
          <div className="onb-section-h">
            Tech stack <span className="opt">optional</span>
          </div>
          <div className="onb-chip-row">
            {TECH_STACK_OPTIONS.map((t) => (
              <button
                key={t}
                type="button"
                className={`onb-chip ${techStack.includes(t) ? "sel" : ""}`}
                aria-pressed={techStack.includes(t)}
                onClick={() =>
                  setTechStack((prev) =>
                    prev.includes(t)
                      ? prev.filter((x) => x !== t)
                      : [...prev, t],
                  )
                }
              >
                {t}
              </button>
            ))}
          </div>
        </div>
      </div>
    </OnboardingChrome>
  )
}
