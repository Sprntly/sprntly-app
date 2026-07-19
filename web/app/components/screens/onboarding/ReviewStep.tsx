"use client"

import { useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "../../../lib/auth"
import { OnboardingChrome } from "../../onboarding/OnboardingChrome"
import { useOnboarding } from "../../../context/OnboardingContext"
import { saveBusinessContextSummary } from "../../../lib/onboarding/store"
import {
  prefetchBusinessContextDraft,
  prefetchMetricDefinitions,
} from "../../../lib/onboarding/draftPrefetch"
import { saveDraft, loadDraft, clearDraft } from "../../../lib/onboarding/useFormDraft"

const DRAFT_KEY = "review-step"

/**
 * Onboarding step 09 — "Here's what we learned" (v6 screenshot spec
 * 2026-07-17). The closing NUMBERED step.
 *
 * Shows the AI-drafted business-context prose (from everything shared plus
 * the website analysis and connected data), fully editable, with a "This
 * looks accurate" checkbox. Accepting stores it on
 * companies.business_context_summary (+ accepted stamp) and hands off to the
 * define-metrics sub-flow, which completes onboarding.
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

  // While the user reads/edits the prose, draft the metric definitions in the
  // BACKGROUND so the define-metrics screens open pre-filled instead of
  // spinning. Same memoized request DefineMetrics joins; fire-and-forget.
  useEffect(() => {
    if (!workspace) return
    if (workspace.metric_definitions.length) return
    const names = workspace.kpi_tree.metrics
      .map((m) => m.name.trim())
      .filter(Boolean)
    if (!names.length) return
    prefetchMetricDefinitions(workspace.id, names).catch(() => {})
  }, [workspace])

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
      router.push("/onboarding/define-metrics")
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save your business context.")
      setSaving(false)
    }
  }

  if (loading || !workspace) return <div className="onb-shell">Loading…</div>

  return (
    <OnboardingChrome
      step={10}
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
      continueLabel="Next · define metrics"
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
        <p className="onb-field-hint" role="status">
          Drafting your business context from everything you shared…
        </p>
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
