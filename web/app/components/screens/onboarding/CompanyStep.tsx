"use client"

import { useEffect, useRef, useState } from "react"
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
import {
  createWorkspace,
  updateWorkspace,
  upsertPrimaryProduct,
} from "../../../lib/onboarding/store"
import { PLANNING_CYCLES } from "../../../lib/onboarding/types"
import { saveDraft, loadDraft, clearDraft } from "../../../lib/onboarding/useFormDraft"
import { companyDocsApi } from "../../../lib/api"
import { Check } from "../../auth/icons"

const DRAFT_KEY = "company-step"

/**
 * Onboarding step 01 — "Tell us about your company" (v6 screenshot spec
 * 2026-07-17).
 *
 * Fields: company name* (the only mandatory one), company website, mission &
 * vision, and strategy / OKRs (typed, or a doc upload alongside), plus an
 * "Add more" disclosure holding portfolio and planning cycle.
 *
 * On Continue we persist the company (+ product website seed), kick the
 * website analysis in the BACKGROUND (no interstitial — the result lands on
 * the onboarding context for later steps/settings), and advance to product.
 */
export function CompanyStep() {
  const auth = useAuth()
  const { workspace, setWorkspace, startWebsiteAnalysis, loading } = useOnboarding()
  const router = useRouter()

  const draft = loadDraft(DRAFT_KEY)
  const [companyName, setCompanyName] = useState((draft?.companyName as string) ?? "")
  const [website, setWebsite] = useState((draft?.website as string) ?? "")
  const [mission, setMission] = useState((draft?.mission as string) ?? "")
  const [strategy, setStrategy] = useState((draft?.strategy as string) ?? "")
  const [portfolio, setPortfolio] = useState((draft?.portfolio as string) ?? "")
  const [planningCycle, setPlanningCycle] = useState(
    (draft?.planningCycle as string) ?? "",
  )

  // Optional strategy-doc upload next to the typed field (doc_type
  // company_strategy — same endpoint the old strategy step used).
  const strategyFileRef = useRef<HTMLInputElement | null>(null)
  const [strategyDocNotice, setStrategyDocNotice] = useState<string | null>(null)
  const [strategyDocUploading, setStrategyDocUploading] = useState(false)

  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Seed from the saved workspace on first load (draft takes priority).
  useEffect(() => {
    if (!workspace) return
    if (draft) return
    setCompanyName(workspace.display_name)
    setWebsite(workspace.product?.website ?? "")
    setMission(workspace.mission ?? "")
    setStrategy(workspace.strategy ?? "")
    setPortfolio(workspace.portfolio ?? "")
    setPlanningCycle(workspace.planning_cycle ?? "")
  }, [workspace]) // eslint-disable-line react-hooks/exhaustive-deps

  // Save draft on visibility change (tab switch / minimize) — not per keystroke.
  useEffect(() => {
    const onHide = () => {
      if (document.hidden)
        saveDraft(DRAFT_KEY, {
          companyName,
          website,
          mission,
          strategy,
          portfolio,
          planningCycle,
        })
    }
    document.addEventListener("visibilitychange", onHide)
    return () => document.removeEventListener("visibilitychange", onHide)
  }, [companyName, website, mission, strategy, portfolio, planningCycle])

  const { errors, validate, clearError, containerRef } = useFieldValidation(() => [
    {
      key: "companyName",
      valid: companyName.trim().length > 0,
      message: "Enter your company name.",
    },
  ])

  async function onPickStrategyDoc(file: File | null) {
    if (!file) return
    setStrategyDocNotice(null)
    setStrategyDocUploading(true)
    try {
      await companyDocsApi.upload(file, "company_strategy")
      setStrategyDocNotice(`${file.name} · uploaded just now.`)
    } catch {
      setStrategyDocNotice(
        `Couldn't upload "${file.name}" just now — you can re-try here or add it later in Settings.`,
      )
    } finally {
      setStrategyDocUploading(false)
    }
  }

  async function save() {
    if (auth.kind !== "authed") return
    setError(null)
    if (!validate().ok) return
    // Shape-check the website whenever one was typed.
    const websiteErr = validateProductWebsite(website)
    if (websiteErr) {
      setError(websiteErr)
      return
    }
    const normalizedSite = normalizeProductWebsite(website)
    setSaving(true)
    try {
      let ws = workspace
      if (workspace) {
        const updated = await updateWorkspace(workspace.id, {
          display_name: companyName.trim(),
          mission: mission.trim() || null,
          strategy: strategy.trim() || null,
          portfolio: portfolio.trim() || null,
          planning_cycle: planningCycle || null,
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
          accountType: "company",
          mission,
          strategy,
          userId: auth.user.id,
        })
        // Portfolio + planning cycle aren't createWorkspace params — patch them
        // on right after (still one Continue for the user).
        ws =
          portfolio.trim() || planningCycle
            ? {
                ...(await updateWorkspace(created.id, {
                  portfolio: portfolio.trim() || null,
                  planning_cycle: planningCycle || null,
                })),
                product: created.product,
              }
            : created
        setWorkspace(ws)
      }
      clearDraft(DRAFT_KEY)
      // Kick off the website analysis in the BACKGROUND and move on. The job
      // runs server-side; the provider outlives this navigation.
      const analysisSite = ws?.product?.website ?? normalizedSite
      if (ws && analysisSite) startWebsiteAnalysis(analysisSite, ws.id)
      router.push("/onboarding/import-context")
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
      subtitle="Add what you can — expand the optional sections to make it sharper."
      footerMeta="Company"
      onContinue={() => void save()}
      continueLabel="Next"
      continueDisabled={saving}
      loading={saving}
    >
      <div ref={containerRef}>
        {error && <div className="onb-form-error">{error}</div>}

        <div className="form-grid">
          <div className="field" data-field="companyName">
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

          <div className="field" data-field="website">
            <div className="field-l">
              Company website <span className="opt">optional</span>
            </div>
            <input
              className="inp"
              type="url"
              value={website}
              onChange={(e) => setWebsite(e.target.value)}
              placeholder="https://yourcompany.com"
              autoComplete="url"
            />
            <p className="onb-field-hint">
              We&apos;ll read this to draft your business context in the
              background.
            </p>
          </div>

          <div className="field full" data-field="strategy">
            <div
              className="field-l"
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
              }}
            >
              <span>
                Strategy / OKRs <span className="opt">optional</span>
              </span>
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
              className="inp"
              rows={4}
              value={strategy}
              onChange={(e) => setStrategy(e.target.value)}
              maxLength={2000}
              placeholder="Type your strategy, or upload a strategy doc / board deck."
            />
            <input
              ref={strategyFileRef}
              type="file"
              style={{ display: "none" }}
              onChange={(e) => void onPickStrategyDoc(e.target.files?.[0] ?? null)}
              aria-label="Strategy document"
            />
            {strategyDocNotice && (
              <p className="onb-field-hint" role="status">
                {strategyDocNotice}
              </p>
            )}
          </div>
        </div>

        <OptionalDisclosure label="Add more ">
          <div className="form-grid">
            <div className="field full">
              <div className="field-l">
                Mission &amp; vision <span className="opt">optional</span>
              </div>
              <textarea
                className="inp"
                rows={3}
                value={mission}
                onChange={(e) => setMission(e.target.value)}
                maxLength={500}
                placeholder="Why the company exists, in a sentence or two"
              />
            </div>
            <div className="field full">
              <div className="field-l">
                Portfolio <span className="opt">— products in your portfolio</span>
              </div>
              <textarea
                className="inp"
                rows={2}
                value={portfolio}
                onChange={(e) => setPortfolio(e.target.value)}
                maxLength={500}
                placeholder="e.g. the apps, devices, and services in your family"
              />
            </div>
            <div className="field full" data-field="planningCycle">
              <div className="field-l">Planning cycle</div>
              <div className="metric-chips">
                {PLANNING_CYCLES.map((opt) => {
                  const isSel = planningCycle === opt.value
                  return (
                    <button
                      type="button"
                      key={opt.value}
                      className={`metric ${isSel ? "sel" : ""}`}
                      aria-pressed={isSel}
                      onClick={() => setPlanningCycle(isSel ? "" : opt.value)}
                    >
                      {isSel && (
                        <span className="mt-ic" aria-hidden>
                          <Check style={{ width: 11, height: 11 }} />
                        </span>
                      )}
                      {opt.label}
                    </button>
                  )
                })}
              </div>
            </div>
          </div>
        </OptionalDisclosure>
      </div>
    </OnboardingChrome>
  )
}
