"use client"

import { useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "../../../lib/auth"
import { OnboardingChrome } from "../../onboarding/OnboardingChrome"
import { useOnboarding } from "../../../context/OnboardingContext"
import { saveBusinessContextSummary } from "../../../lib/onboarding/store"
import { prefetchBusinessContextDraft } from "../../../lib/onboarding/draftPrefetch"
import { saveDraft, loadDraft, clearDraft } from "../../../lib/onboarding/useFormDraft"

const DRAFT_KEY = "review-step"

/**
 * Widths of the shimmer lines standing in for the drafted prose, as ragged
 * paragraph-ish runs so the placeholder reads as text rather than a bar chart.
 * Inline so the shared `.assistant-skel-line` nth-child widths (tuned for a
 * 3-line chat skeleton) don't apply here.
 */
const DRAFT_SKELETON_WIDTHS = [
  "96%", "88%", "93%", "61%",
  "91%", "97%", "84%", "72%",
  "94%", "89%", "46%",
]

/**
 * Onboarding step 08 — "Here's what we learned" (2026-07-21 screenshot spec).
 *
 * Shows the AI-drafted business-context prose (from everything shared plus
 * the website analysis and connected data), fully editable, with a "This
 * looks accurate" checkbox. Accepting stores it on
 * companies.business_context_summary (+ accepted stamp) and continues to the
 * personalize step.
 *
 * This step used to own the define-metrics gate. Personalize was inserted
 * between it and the sub-flow, so the branch (and the metric-definition
 * prefetch that warms it) moved there — see PersonalizeStep.
 *
 * Draft resolution order: an in-progress local draft → the previously saved
 * summary → a fresh backend draft (with a graceful manual-writing fallback
 * when generation fails).
 */
export function ReviewStep() {
  const auth = useAuth()
  const { workspace, setWorkspace, loading } = useOnboarding()
  const router = useRouter()

  const draft = loadDraft(DRAFT_KEY)
  const [summary, setSummary] = useState((draft?.summary as string) ?? "")
  const [accurate, setAccurate] = useState(false)
  const [drafting, setDrafting] = useState(false)
  const [draftFailed, setDraftFailed] = useState(false)
  const requested = useRef(false)

  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const onHide = () => {
      if (document.hidden) saveDraft(DRAFT_KEY, { summary })
    }
    document.addEventListener("visibilitychange", onHide)
    return () => document.removeEventListener("visibilitychange", onHide)
  }, [summary])

  // Redirect when there's no workspace to anchor the step.
  useEffect(() => {
    if (!loading && !workspace) router.replace("/onboarding/company")
  }, [loading, workspace, router])

  // Resolve the draft: local draft → saved summary → the backend draft. The
  // invite step already kicked the memoized prefetch in the background, so
  // this usually resolves INSTANTLY (we join the in-flight/settled promise);
  // when the user got here without passing invite, this call starts it.
  useEffect(() => {
    if (!workspace || requested.current) return
    requested.current = true
    if (draft?.summary) return
    if (workspace.business_context_summary) {
      setSummary(workspace.business_context_summary)
      return
    }
    setDrafting(true)
    prefetchBusinessContextDraft(workspace.id)
      .then((d) => setSummary((prev) => prev || d))
      .catch(() => setDraftFailed(true))
      .finally(() => setDrafting(false))
  }, [workspace]) // eslint-disable-line react-hooks/exhaustive-deps

  async function accept() {
    if (!workspace || auth.kind !== "authed") return
    setError(null)
    if (!summary.trim()) {
      setError("Add a few sentences of business context before continuing.")
      return
    }
    setSaving(true)
    try {
      const updated = await saveBusinessContextSummary(
        workspace.id,
        summary,
        accurate,
      )
      setWorkspace({ ...updated, product: workspace.product })
      clearDraft(DRAFT_KEY)
      router.push("/onboarding/personalize")
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save your business context.")
      setSaving(false)
    }
  }

  if (loading || !workspace) return <div className="onb-shell">Loading…</div>

  return (
    <OnboardingChrome
      step={8}
      saveLabel="Saved · auto-saves"
      title={
        <>
          Here&apos;s what we <em>learned.</em>
        </>
      }
      subtitle="Based on everything you shared — plus research across your website, reviews and connected data — here's the business context every agent will reason through. Read it, edit anything, and accept."
      footerMeta="Review business context"
      onBack={() => router.push("/onboarding/invite")}
      onContinue={() => void accept()}
      continueLabel="Next · personalize"
      continueDisabled={saving || drafting || !summary.trim()}
      loading={saving}
      wideCard
    >
      {error && <div className="onb-form-error">{error}</div>}

      <div className="metric-note" style={{ marginBottom: 12 }}>
        <span>
          ✦ Drafted by Sprntly from your inputs
          {workspace.product?.website ? `, ${workspace.product.website}` : ""} and
          connected sources. Fully editable.
        </span>
      </div>

      {drafting ? (
        // Keep the page shaped like the editor it's about to become: a
        // textarea-sized shimmer plus the checkbox row, so the card doesn't
        // read as empty (with a dead Continue) while the draft generates.
        <>
          <div className="onb-draft-skel" aria-hidden>
            {DRAFT_SKELETON_WIDTHS.map((width, i) => (
              <span key={i} className="assistant-skel-line" style={{ width }} />
            ))}
          </div>
          <div
            style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 12 }}
            aria-hidden
          >
            <span
              className="assistant-skel-line"
              style={{ width: 13, height: 13, borderRadius: 3 }}
            />
            <span className="assistant-skel-line" style={{ width: 128 }} />
          </div>
          <p className="onb-field-hint" role="status">
            Generating your business context from everything you shared — this
            usually takes a few seconds.
          </p>
        </>
      ) : (
        <>
          {draftFailed && !summary.trim() && (
            <p className="onb-field-hint" role="status">
              We couldn&apos;t draft this automatically just now — describe your
              business in your own words, or come back to Settings → Business
              Context later.
            </p>
          )}
          <textarea
            className="inp"
            rows={14}
            value={summary}
            onChange={(e) => setSummary(e.target.value)}
            maxLength={8000}
            placeholder="What the business is, how it earns, who it serves, and what the team is focused on right now"
            aria-label="Business context"
          />
          <label
            style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 12 }}
          >
            <input
              type="checkbox"
              checked={accurate}
              onChange={(e) => setAccurate(e.target.checked)}
            />
            This looks accurate
          </label>
        </>
      )}
    </OnboardingChrome>
  )
}
