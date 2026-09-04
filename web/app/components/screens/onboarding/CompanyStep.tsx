"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { ONBOARDING_PLAN_PATH } from "../../../lib/billingPlans"
import { useAuth } from "../../../lib/auth"
import { useFieldValidation } from "../../onboarding/InterviewLayout"
import { OnboardingChrome } from "../../onboarding/OnboardingChrome"
import { useOnboarding } from "../../../context/OnboardingContext"
import {
  validateProductWebsite,
  normalizeProductWebsite,
} from "../../../lib/onboarding/product-helpers"
import {
  createWorkspace,
  saveWorkspaceOwnedFields,
  updateWorkspace,
  upsertPrimaryProduct,
} from "../../../lib/onboarding/store"
import {
  DEFAULT_WORKSPACE_NAME,
  DEFAULT_WORKSPACE_SCOPE,
  stepForSlug,
} from "../../../lib/onboarding/types"
import { saveDraft, loadDraft, clearDraft } from "../../../lib/onboarding/useFormDraft"

const DRAFT_KEY = "company-step"

/**
 * Onboarding step 01 — "Tell us about your company and product".
 *
 * FOUR FIELDS, NOTHING ELSE (2026-09-03): company name*, company website,
 * product name, product website. Mission & vision, strategy / OKRs and the
 * "Add more" disclosure (portfolio, planning cycle) came off this page and are
 * edited in Settings instead — Company Profile owns mission / strategy /
 * portfolio, Process & Planning owns the planning cycle, and all four still
 * read and write the columns they always did. NOTHING WAS MIGRATED: the data
 * did not move, only the place you type it. A company onboarded before this
 * ship keeps every value it entered and finds it in Settings.
 *
 * WHY: this is the first screen after signup, and it was asking someone who has
 * not seen the product yet to write down their OKRs. Name and URL are what the
 * next step actually needs; strategy is something you come back and fill in
 * once you know what it is for.
 *
 * THE TWO WEBSITES ARE DIFFERENT COLUMNS, and that part is new. Until now the
 * field labelled "Company website" wrote to `products.website` — the product's
 * URL wearing a company label, which was harmless while only one of them was
 * ever shown. With both on one page it stops being harmless: whichever saved
 * last would clobber the other. `companies.website` (migration
 * 20260903150000) is the company's own, and it is nullable because every
 * company onboarded before today has its site recorded on the product instead.
 *
 * IT RUNS FIRST BECAUSE EVERYTHING ELSE IS KEYED ON WHAT IT COLLECTS: the
 * company row does not exist until this step saves, and the website analysis it
 * kicks off in the background is what the review step later reads.
 *
 * IT ALSO NAMES THE WORKSPACE. The step that used to ask was removed with the
 * rest, so this creates the company's "Main workspace" — see
 * DEFAULT_WORKSPACE_NAME — best-effort, and only while it is still unnamed.
 *
 * The workspace may already exist when this loads — a returning user, or one
 * who went back a step — so the fields seed FILL-ONLY from `workspace`, both to
 * show what was saved and so nothing landing asynchronously can overwrite what
 * is being typed.
 */
export function CompanyStep() {
  const auth = useAuth()
  const { workspace, setWorkspace, startWebsiteAnalysis, loading } = useOnboarding()
  const router = useRouter()

  const draft = loadDraft(DRAFT_KEY)
  const [companyName, setCompanyName] = useState((draft?.companyName as string) ?? "")
  const [companyWebsite, setCompanyWebsite] = useState(
    (draft?.companyWebsite as string) ?? "",
  )
  const [productName, setProductName] = useState((draft?.productName as string) ?? "")
  const [productWebsite, setProductWebsite] = useState(
    (draft?.productWebsite as string) ?? "",
  )

  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Seed from the saved workspace, filling only fields still empty — the same
  // rule the remaining steps use. It runs on every `workspace` change rather
  // than once, so a background extraction finishing while this step is open
  // pops its values in; a restored draft and anything already typed always win.
  //
  // The company website falls back to the PRODUCT's for a company onboarded
  // before the two were separate columns: their site is on the product row, and
  // showing this field blank would read as having lost it.
  useEffect(() => {
    if (!workspace) return
    setCompanyName((v) => v || workspace.display_name)
    setCompanyWebsite(
      (v) => v || workspace.website || (workspace.product?.website ?? ""),
    )
    setProductName((v) => v || (workspace.product?.name ?? ""))
    setProductWebsite((v) => v || (workspace.product?.website ?? ""))
  }, [workspace]) // eslint-disable-line react-hooks/exhaustive-deps

  // Save draft on visibility change (tab switch / minimize) — not per keystroke.
  useEffect(() => {
    const onHide = () => {
      if (document.hidden)
        saveDraft(DRAFT_KEY, {
          companyName,
          companyWebsite,
          productName,
          productWebsite,
        })
    }
    document.addEventListener("visibilitychange", onHide)
    return () => document.removeEventListener("visibilitychange", onHide)
  }, [companyName, companyWebsite, productName, productWebsite])

  const { errors, validate, clearError, containerRef } = useFieldValidation(() => [
    {
      key: "companyName",
      valid: companyName.trim().length > 0,
      message: "Enter your company name.",
    },
  ])

  async function save() {
    if (auth.kind !== "authed") return
    setError(null)
    if (!validate().ok) return
    // Shape-check whichever URLs were typed. `validateProductWebsite` is named
    // for the field it was written for; the rule is a plain URL check and the
    // company site has to clear the same bar.
    const companySiteErr = validateProductWebsite(companyWebsite)
    if (companySiteErr) {
      setError(companySiteErr.replace(/product website/i, "company website"))
      return
    }
    const productSiteErr = validateProductWebsite(productWebsite)
    if (productSiteErr) {
      setError(productSiteErr)
      return
    }
    const normalizedCompanySite = normalizeProductWebsite(companyWebsite)
    const normalizedProductSite = normalizeProductWebsite(productWebsite)
    setSaving(true)
    const nextStep = stepForSlug("connectors") ?? 2
    try {
      let ws = workspace
      if (workspace) {
        const updated = await updateWorkspace(workspace.id, {
          display_name: companyName.trim(),
          website: normalizedCompanySite || null,
          onboarding_step: nextStep,
        })
        // The product name still falls back to the company name: `products.name`
        // rejects an empty string, and the product step behind this one is where
        // someone names it properly.
        const product = await upsertPrimaryProduct(workspace.id, {
          name: productName.trim() || workspace.product?.name || companyName.trim(),
          website: normalizedProductSite || workspace.product?.website || null,
        })
        ws = { ...updated, product }
        setWorkspace(ws)
      } else {
        ws = await createWorkspace({
          companyName,
          website: normalizedCompanySite,
          productName: productName.trim() || companyName,
          productWebsite: normalizedProductSite,
          accountType: "company",
          userId: auth.user.id,
          onboardingStep: nextStep,
        })
        setWorkspace(ws)
      }
      // NAME THE WORKSPACE FOR THEM. The step that used to ask was removed —
      // see DEFAULT_WORKSPACE_NAME for why — so the company's default workspace
      // would otherwise keep the "Default" sentinel and read as unnamed
      // everywhere it is shown.
      //
      // Only while it is still unnamed, so someone who has since renamed it and
      // walks back through this step does not have their name replaced. And
      // BEST-EFFORT: this is a label on a row that already exists, and failing
      // the whole company step over it would be the wrong trade.
      if (ws && ws.team_name == null) {
        try {
          await saveWorkspaceOwnedFields(DEFAULT_WORKSPACE_NAME, {
            team_scope: DEFAULT_WORKSPACE_SCOPE,
          })
          ws = { ...ws, team_name: DEFAULT_WORKSPACE_NAME, team_scope: DEFAULT_WORKSPACE_SCOPE }
          setWorkspace(ws)
        } catch {
          /* named later by the same check on the next pass — never blocks */
        }
      }
      clearDraft(DRAFT_KEY)
      // Kick off the website analysis in the BACKGROUND and move on. The job
      // runs server-side; the provider outlives this navigation.
      //
      // THE COMPANY SITE FIRST, the product's as the fallback. The sweep
      // researches the ORGANIZATION — industry, positioning, competitors — so
      // the company URL is the better subject. The fallback is what keeps the
      // prefill working for someone who fills in only the product, and for every
      // company whose site is recorded on the product row from before the split.
      const analysisSite =
        ws?.website
        || ws?.product?.website
        || normalizedCompanySite
        || normalizedProductSite
      if (ws && analysisSite) startWebsiteAnalysis(analysisSite, ws.id)
      // The PAYMENT GATE sits between company creation and the rest of the
      // flow. `onboarding_step` above is still the NEXT step on purpose: the
      // gate is unnumbered, so the persisted marker names the step they will
      // resume ONCE they have paid, and postLoginPath keeps routing them back
      // here until they have. Someone who abandons at Checkout therefore comes
      // back to the gate rather than to a half-built workspace, and their
      // company row — the reason to put payment this early — already exists.
      //
      // The gate forwards straight through for a company that already has a
      // live subscription, so an invited teammate never sees it. With payments
      // hidden it forwards for everyone (see lib/billingAccess).
      router.push(ONBOARDING_PLAN_PATH)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save your company.")
      setSaving(false)
    }
  }

  if (loading) return <div className="onb-shell">Loading…</div>

  return (
    <OnboardingChrome
      step={stepForSlug("company") ?? 1}
      saveLabel="Saved · auto-saves"
      title={
        <>
          Tell us about your <em>company and product.</em>
        </>
      }
      subtitle="Just the basics — everything else is in Settings, whenever you want it."
      footerMeta="Company & Product"
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

          <div className="field" data-field="companyWebsite">
            <div className="field-l">
              Company website <span className="opt">optional</span>
            </div>
            <input
              className="inp"
              type="url"
              value={companyWebsite}
              onChange={(e) => setCompanyWebsite(e.target.value)}
              placeholder="https://yourcompany.com"
              autoComplete="url"
            />
            <p className="onb-field-hint">
              We&apos;ll read this in the background to draft your business
              context, and fill it into the prompt on the next step.
            </p>
          </div>

          <div className="field" data-field="productName">
            <div className="field-l">
              Product name <span className="opt">optional</span>
            </div>
            <input
              className="inp"
              value={productName}
              onChange={(e) => setProductName(e.target.value)}
              maxLength={100}
              placeholder="The product you're onboarding (you can add more later)"
            />
          </div>

          <div className="field" data-field="productWebsite">
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
          </div>
        </div>
      </div>
    </OnboardingChrome>
  )
}
