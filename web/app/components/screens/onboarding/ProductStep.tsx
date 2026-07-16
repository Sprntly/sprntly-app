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
  advanceOnboardingStep,
  markSkippedFields,
  upsertPrimaryProduct,
} from "../../../lib/onboarding/store"
import {
  MONETIZATION_OPTIONS,
  SURFACE_OPTIONS,
} from "../../../lib/onboarding/types"
import { saveDraft, loadDraft, clearDraft } from "../../../lib/onboarding/useFormDraft"
import { Check, Plus } from "../../auth/icons"

const DRAFT_KEY = "product-step"

/**
 * Onboarding step 02 — "Your product" (registration spec 2026-07, Product
 * section). Product URL* and surfaces* are mandatory for COMPANY accounts;
 * user personas + monetization live behind an optional disclosure. Product
 * position / state / competitors are settings-only (blue in the spec).
 *
 * Persists via the extended upsertPrimaryProduct and advances to metrics.
 */
export function ProductStep() {
  const auth = useAuth()
  const { workspace, profile, setWorkspace, loading } = useOnboarding()
  const router = useRouter()

  const draft = loadDraft(DRAFT_KEY)
  const [productName, setProductName] = useState((draft?.productName as string) ?? "")
  const [productUrl, setProductUrl] = useState((draft?.productUrl as string) ?? "")
  const [surfaces, setSurfaces] = useState<string[]>(
    (draft?.surfaces as string[]) ?? [],
  )
  const [personas, setPersonas] = useState<string[]>(
    (draft?.personas as string[]) ?? [],
  )
  const [personaInput, setPersonaInput] = useState("")
  const [monetization, setMonetization] = useState<string[]>(
    (draft?.monetization as string[]) ?? [],
  )

  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const isCompany = (profile?.account_type ?? "company") === "company"

  // Seed from the saved workspace/product (draft takes priority).
  useEffect(() => {
    if (!workspace) return
    if (draft) return
    setProductName(workspace.product?.name ?? workspace.display_name)
    setProductUrl(workspace.product?.website ?? "")
    setSurfaces(workspace.product?.surfaces ?? [])
    setPersonas(workspace.product?.personas ?? [])
    setMonetization(workspace.product?.monetization ?? [])
  }, [workspace]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const onHide = () => {
      if (document.hidden)
        saveDraft(DRAFT_KEY, { productName, productUrl, surfaces, personas, monetization })
    }
    document.addEventListener("visibilitychange", onHide)
    return () => document.removeEventListener("visibilitychange", onHide)
  }, [productName, productUrl, surfaces, personas, monetization])

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
    requiredFor(isCompany, {
      key: "productUrl",
      valid: productUrl.trim().length > 0,
      message: "Enter your product URL.",
    }),
    requiredFor(isCompany, {
      key: "surfaces",
      valid: surfaces.length > 0,
      message: "Pick at least one surface.",
    }),
  ])

  function toggleSurface(value: string) {
    clearError("surfaces")
    setSurfaces((prev) =>
      prev.includes(value) ? prev.filter((s) => s !== value) : [...prev, value],
    )
  }

  function toggleMonetization(value: string) {
    setMonetization((prev) =>
      prev.includes(value) ? prev.filter((s) => s !== value) : [...prev, value],
    )
  }

  function addPersona() {
    const p = personaInput.trim()
    if (!p) return
    setPersonas((prev) =>
      prev.some((x) => x.toLowerCase() === p.toLowerCase()) ? prev : [...prev, p],
    )
    setPersonaInput("")
  }

  async function save() {
    if (!workspace || auth.kind !== "authed") return
    setError(null)
    if (!validate().ok) return
    const urlErr = validateProductWebsite(productUrl)
    if (urlErr) {
      setError(urlErr)
      return
    }
    setSaving(true)
    try {
      const skipped: string[] = []
      if (!isCompany) {
        if (!productUrl.trim()) skipped.push("product_url")
        if (!surfaces.length) skipped.push("product_surfaces")
      }
      const product = await upsertPrimaryProduct(workspace.id, {
        name: productName.trim() || workspace.display_name,
        website: normalizeProductWebsite(productUrl) || null,
        surfaces,
        personas,
        monetization,
      })
      const updated = await advanceOnboardingStep(workspace.id, 3)
      setWorkspace({ ...updated, product })
      if (skipped.length) await markSkippedFields(auth.user.id, skipped)
      clearDraft(DRAFT_KEY)
      router.push("/onboarding/metrics")
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save your product.")
      setSaving(false)
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
      subtitle="Where it lives and who it's for. Positioning, competitors, and stage can all be added later in Settings."
      footerMeta={
        isCompany
          ? "URL and surfaces are required — personas and monetization are optional."
          : "Everything here is optional — add what you like."
      }
      onBack={() => router.push("/onboarding/company")}
      onContinue={() => void save()}
      continueDisabled={saving}
      loading={saving}
    >
      <div ref={containerRef}>
        {error && <div className="onb-form-error">{error}</div>}

        <div className="form-grid">
          <div className="field full" data-field="productName">
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

          <div className="field full" data-field="productUrl">
            <div className="field-l">
              Product URL{" "}
              {isCompany ? (
                <span className="req">*</span>
              ) : (
                <span className="opt">optional</span>
              )}
            </div>
            <input
              className={`inp ${errors.productUrl ? "has-error" : ""}`}
              type="url"
              value={productUrl}
              onChange={(e) => {
                setProductUrl(e.target.value)
                clearError("productUrl")
              }}
              placeholder="https://yourproduct.com"
              autoComplete="url"
            />
            {errors.productUrl && (
              <p className="onb-field-error">{errors.productUrl}</p>
            )}
          </div>
        </div>

        <div className="onb-section" style={{ marginTop: 18 }} data-field="surfaces">
          <div className="onb-section-h">
            Surfaces{" "}
            {isCompany ? (
              <span className="req">*</span>
            ) : (
              <span className="opt">optional</span>
            )}{" "}
            <span className="opt">— where does it run?</span>
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

        <OptionalDisclosure label="Add personas & monetization">
          <div className="onb-section">
            <div className="onb-section-h">
              User personas <span className="opt">— who uses it?</span>
            </div>
            <div className="metric-chips">
              {personas.map((p) => (
                <button
                  type="button"
                  key={p}
                  className="metric sel"
                  aria-pressed
                  onClick={() => setPersonas((prev) => prev.filter((x) => x !== p))}
                  title="Remove"
                >
                  <span className="mt-ic" aria-hidden>
                    <Check style={{ width: 11, height: 11 }} />
                  </span>
                  {p}
                </button>
              ))}
            </div>
            <div className="metric-other-row" style={{ marginTop: 10 }}>
              <input
                className="inp"
                value={personaInput}
                onChange={(e) => setPersonaInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault()
                    addPersona()
                  }
                }}
                placeholder="e.g. Growth PM, Support lead…"
                maxLength={60}
                aria-label="Add a user persona"
              />
              <button
                type="button"
                className="btn btn-secondary"
                onClick={addPersona}
                disabled={!personaInput.trim()}
              >
                <Plus style={{ width: 13, height: 13 }} aria-hidden /> Add
              </button>
            </div>
          </div>

          <div className="onb-section" style={{ marginTop: 16 }}>
            <div className="onb-section-h">
              Monetization <span className="opt">— how does it earn?</span>
            </div>
            <div className="metric-chips">
              {MONETIZATION_OPTIONS.map((opt) => {
                const isSel = monetization.includes(opt.value)
                return (
                  <button
                    type="button"
                    key={opt.value}
                    className={`metric ${isSel ? "sel" : ""}`}
                    aria-pressed={isSel}
                    onClick={() => toggleMonetization(opt.value)}
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
        </OptionalDisclosure>
      </div>
    </OnboardingChrome>
  )
}
