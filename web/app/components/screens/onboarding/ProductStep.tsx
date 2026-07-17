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
import {
  advanceOnboardingStep,
  updateWorkspace,
  upsertPrimaryProduct,
} from "../../../lib/onboarding/store"
import {
  MONETIZATION_OPTIONS,
  ONBOARDING_STEP_COUNT,
  SURFACE_OPTIONS,
} from "../../../lib/onboarding/types"
import { saveDraft, loadDraft, clearDraft } from "../../../lib/onboarding/useFormDraft"
import { Check } from "../../auth/icons"

const DRAFT_KEY = "product-step"

/** Parse the comma-separated competitors field into a clean, deduped list. */
export function parseCompetitors(raw: string): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const part of raw.split(",")) {
    const name = part.trim()
    if (!name) continue
    const key = name.toLowerCase()
    if (seen.has(key)) continue
    seen.add(key)
    out.push(name)
  }
  return out
}

/**
 * Onboarding step 02 — "Your product" (v6 screenshot spec 2026-07-17).
 *
 * Product name* and surfaces* are mandatory; website, monetization (single
 * dropdown), and the "tell us about your users" prose are optional, with
 * competitors (comma-separated → companies.competitors) behind a disclosure.
 * Product position lives in Settings only.
 */
export function ProductStep() {
  const auth = useAuth()
  const { workspace, setWorkspace, loading } = useOnboarding()
  const router = useRouter()

  const draft = loadDraft(DRAFT_KEY)
  const [productName, setProductName] = useState((draft?.productName as string) ?? "")
  const [productUrl, setProductUrl] = useState((draft?.productUrl as string) ?? "")
  const [surfaces, setSurfaces] = useState<string[]>(
    (draft?.surfaces as string[]) ?? [],
  )
  const [monetization, setMonetization] = useState((draft?.monetization as string) ?? "")
  const [usersDescription, setUsersDescription] = useState(
    (draft?.usersDescription as string) ?? "",
  )
  const [competitors, setCompetitors] = useState((draft?.competitors as string) ?? "")

  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Seed from the saved workspace/product (draft takes priority).
  useEffect(() => {
    if (!workspace) return
    if (draft) return
    setProductName(workspace.product?.name ?? workspace.display_name)
    setProductUrl(workspace.product?.website ?? "")
    setSurfaces(workspace.product?.surfaces ?? [])
    setMonetization(workspace.product?.monetization?.[0] ?? "")
    setUsersDescription(workspace.product?.users_description ?? "")
    setCompetitors((workspace.competitors ?? []).join(", "))
  }, [workspace]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const onHide = () => {
      if (document.hidden)
        saveDraft(DRAFT_KEY, {
          productName,
          productUrl,
          surfaces,
          monetization,
          usersDescription,
          competitors,
        })
    }
    document.addEventListener("visibilitychange", onHide)
    return () => document.removeEventListener("visibilitychange", onHide)
  }, [productName, productUrl, surfaces, monetization, usersDescription, competitors])

  // Redirect when there's no workspace to anchor the step.
  useEffect(() => {
    if (!loading && !workspace) router.replace("/onboarding/company")
  }, [loading, workspace, router])

  const { errors, validate, clearError, containerRef } = useFieldValidation(() => [
    {
      key: "productName",
      valid: productName.trim().length > 0,
      message: "Enter your product name.",
    },
    {
      key: "surfaces",
      valid: surfaces.length > 0,
      message: "Pick at least one surface.",
    },
  ])

  function toggleSurface(value: string) {
    clearError("surfaces")
    setSurfaces((prev) =>
      prev.includes(value) ? prev.filter((s) => s !== value) : [...prev, value],
    )
  }

  async function persist(): Promise<boolean> {
    if (!workspace || auth.kind !== "authed") return false
    setError(null)
    if (!validate().ok) return false
    const urlErr = validateProductWebsite(productUrl)
    if (urlErr) {
      setError(urlErr)
      return false
    }
    setSaving(true)
    try {
      const product = await upsertPrimaryProduct(workspace.id, {
        name: productName.trim() || workspace.display_name,
        website: normalizeProductWebsite(productUrl) || null,
        surfaces,
        monetization: monetization ? [monetization] : [],
        usersDescription: usersDescription.trim() || null,
      })
      const updated = await updateWorkspace(workspace.id, {
        competitors: parseCompetitors(competitors),
        onboarding_step: 3,
      })
      setWorkspace({ ...updated, product })
      clearDraft(DRAFT_KEY)
      return true
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save your product.")
      setSaving(false)
      return false
    }
  }

  async function save() {
    if (await persist()) router.push("/onboarding/metrics")
  }

  async function skipToEnd() {
    if (!workspace) return
    if (await persist()) {
      await advanceOnboardingStep(workspace.id, ONBOARDING_STEP_COUNT)
      router.push("/onboarding/review")
    }
  }

  if (loading || !workspace) return <div className="onb-shell">Loading…</div>

  return (
    <OnboardingChrome
      step={2}
      saveLabel="Saved · auto-saves"
      title={
        <>
          Your <em>product.</em>
        </>
      }
      subtitle="Name, where it lives, and how it makes money. Product position and competitors live in Settings."
      footerMeta="Product"
      onBack={() => router.push("/onboarding/company")}
      onContinue={() => void save()}
      onSkipToEnd={() => void skipToEnd()}
      continueLabel="Next"
      continueDisabled={saving}
      loading={saving}
    >
      <div ref={containerRef}>
        {error && <div className="onb-form-error">{error}</div>}

        <div className="form-grid">
          <div className="field" data-field="productName">
            <div className="field-l">
              Product name <span className="req">*</span>
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
            {errors.productName && (
              <p className="onb-field-error">{errors.productName}</p>
            )}
          </div>

          <div className="field" data-field="productUrl">
            <div className="field-l">
              Website <span className="opt">optional</span>
            </div>
            <input
              className="inp"
              type="url"
              value={productUrl}
              onChange={(e) => setProductUrl(e.target.value)}
              placeholder="https://yourproduct.com"
              autoComplete="url"
            />
          </div>
        </div>

        <div className="onb-section" style={{ marginTop: 18 }} data-field="surfaces">
          <div className="onb-section-h">
            Surfaces <span className="req">*</span>{" "}
            <span className="opt">— select all that apply</span>
          </div>
          {errors.surfaces && <p className="onb-field-error">{errors.surfaces}</p>}
          <div className="metric-chips">
            {SURFACE_OPTIONS.map((opt) => {
              const isSel = surfaces.includes(opt.value)
              return (
                <button
                  type="button"
                  key={opt.value}
                  className={`metric ${isSel ? "sel" : ""}`}
                  aria-pressed={isSel}
                  onClick={() => toggleSurface(opt.value)}
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

        <div className="form-grid" style={{ marginTop: 18 }}>
          <div className="field full" data-field="monetization">
            <div className="field-l">
              Monetization <span className="opt">optional</span>
            </div>
            <select
              className="inp"
              value={monetization}
              onChange={(e) => setMonetization(e.target.value)}
              aria-label="Monetization"
            >
              <option value="">How does it earn?</option>
              {MONETIZATION_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </div>

          <div className="field full" data-field="usersDescription">
            <div className="field-l">
              Tell us about your users{" "}
              <span className="opt">— who are your users or customers?</span>
            </div>
            <textarea
              className="inp"
              rows={3}
              value={usersDescription}
              onChange={(e) => setUsersDescription(e.target.value)}
              maxLength={1000}
              placeholder="Your main user or customer types, in your own words"
            />
          </div>
        </div>

        <OptionalDisclosure label="Add competitors — who you're up against (optional)">
          <div className="field full" data-field="competitors">
            <div className="field-l">
              Competitors <span className="opt">— comma-separated</span>
            </div>
            <textarea
              className="inp"
              rows={2}
              value={competitors}
              onChange={(e) => setCompetitors(e.target.value)}
              maxLength={500}
              placeholder="e.g. Apple Health, Fitbit, Oura, Garmin"
            />
          </div>
        </OptionalDisclosure>
      </div>
    </OnboardingChrome>
  )
}
