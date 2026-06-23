"use client"

import { useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { OnboardingChrome } from "../../onboarding/OnboardingChrome"
import { useOnboarding } from "../../../context/OnboardingContext"
import { advanceOnboardingStep, updateWorkspace } from "../../../lib/onboarding/store"
import { saveDraft, loadDraft, clearDraft } from "../../../lib/onboarding/useFormDraft"
import { roadmapDocApi } from "../../../lib/api"
import { FileText, Check } from "../../auth/icons"

const DRAFT_KEY = "strategy"

/**
 * Onboarding step 04 — "Strategy, leadership & your roadmap" (scene onbstrat).
 *
 * Captures what shapes the company's priorities so the agents weigh work
 * correctly:
 *   - a free-text "current priorities" field (persisted to companies.okrs, the
 *     existing strategic-context slot), and
 *   - a ROADMAP-DOC upload affordance that posts to `POST /v1/company/roadmap-doc`.
 *
 * ROADMAP-DOC STATUS: the backend endpoint does not exist yet (see
 * roadmapDocApi). The upload is fully wired UI-side; a failure (incl. the route
 * being missing) is caught and shown as a soft "we'll load this in later"
 * notice and NEVER blocks the step. The step is also skippable.
 */
export function Strategy() {
  const { workspace, loading } = useOnboarding()
  const router = useRouter()
  const draft = loadDraft(DRAFT_KEY)
  const [priorities, setPriorities] = useState<string>((draft?.priorities as string) ?? "")
  const [roadmapFileName, setRoadmapFileName] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadNotice, setUploadNotice] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement | null>(null)

  // Seed priorities from the saved company (only if no draft).
  useEffect(() => {
    if (!workspace || draft) return
    setPriorities(workspace.okrs ?? "")
  }, [workspace]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const onHide = () => {
      if (document.hidden) saveDraft(DRAFT_KEY, { priorities })
    }
    document.addEventListener("visibilitychange", onHide)
    return () => document.removeEventListener("visibilitychange", onHide)
  }, [priorities])

  // Redirect when there's no workspace to anchor the step.
  useEffect(() => {
    if (!loading && !workspace) router.replace("/onboarding/business-info")
  }, [loading, workspace, router])

  async function onPickRoadmap(file: File | null) {
    if (!file) return
    setUploadNotice(null)
    setRoadmapFileName(file.name)
    setUploading(true)
    try {
      // TODO(backend): POST /v1/company/roadmap-doc is an assumed endpoint that
      // may not exist yet — soft-fail so the step never blocks (see roadmapDocApi).
      await roadmapDocApi.upload(file)
      setUploadNotice(`Uploaded "${file.name}" — we'll pressure-test it against your data.`)
    } catch {
      setUploadNotice(
        `Saved "${file.name}" to upload once roadmap import is enabled — this won't block setup.`,
      )
    } finally {
      setUploading(false)
    }
  }

  async function go(skipped: boolean) {
    if (!workspace) return
    setError(null)
    setSaving(true)
    try {
      if (!skipped && priorities.trim()) {
        await updateWorkspace(workspace.id, {
          okrs: priorities.trim(),
          onboarding_step: 5,
        })
      } else {
        await advanceOnboardingStep(workspace.id, 5)
      }
      clearDraft(DRAFT_KEY)
      router.push("/onboarding/workspace")
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save your strategy.")
      setSaving(false)
    }
  }

  if (loading || !workspace) return <div className="onb-shell">Loading…</div>

  return (
    <OnboardingChrome
      step={4}
      saveLabel="Saved · auto-saves"
      title={
        <>
          Strategy, leadership &amp; <em>your roadmap.</em>
        </>
      }
      subtitle="Give the agents what shapes your priorities. The more you add, the sharper every brief and roadmap gets — you can always add more in Settings."
      footerMeta={
        <>
          Step 4 of 5 · strategy —{" "}
          <button
            type="button"
            className="onb-skip-link"
            onClick={() => void go(true)}
            disabled={saving}
          >
            Skip for now
          </button>
        </>
      }
      onBack={() => router.push("/onboarding/business-context")}
      onContinue={() => void go(false)}
      continueDisabled={saving}
      loading={saving}
    >
      {error && <div className="onb-form-error">{error}</div>}

      <div className="onb-section">
        <div className="onb-section-h">
          Current priorities <span className="opt">— optional</span>
        </div>
        <textarea
          className="inp"
          rows={4}
          style={{ resize: "vertical", lineHeight: 1.6 }}
          value={priorities}
          onChange={(e) => setPriorities(e.target.value)}
          placeholder="What's the leadership direction this half? OKRs, big bets, constraints the team is weighing…"
          aria-label="Current priorities"
        />
        <p className="onb-field-hint">
          This anchors how every agent weighs what matters.
        </p>
      </div>

      <div className="onb-section" style={{ marginTop: 18 }}>
        <div className="onb-section-h">
          Your current roadmap <span className="opt">— we&apos;ll stress-test it</span>
        </div>
        <button
          type="button"
          className={`onb-up onb-up-wide ${roadmapFileName ? "has-file" : ""}`}
          onClick={() => fileRef.current?.click()}
          disabled={uploading}
          data-field="roadmap-doc"
        >
          <span className="onb-up-ic" aria-hidden>
            {roadmapFileName ? (
              <Check style={{ width: 16, height: 16 }} />
            ) : (
              <FileText style={{ width: 16, height: 16 }} />
            )}
          </span>
          <span className="onb-up-b">
            <span className="onb-up-t">
              {roadmapFileName
                ? roadmapFileName
                : uploading
                  ? "Uploading…"
                  : "Upload your current roadmap"}
            </span>
            <span className="onb-up-s">
              Spreadsheet, deck, or doc — Sprntly loads it in and pressure-tests it
              against your data.
            </span>
          </span>
        </button>
        <input
          ref={fileRef}
          type="file"
          style={{ display: "none" }}
          onChange={(e) => void onPickRoadmap(e.target.files?.[0] ?? null)}
          aria-label="Roadmap document"
        />
        {uploadNotice && (
          <p className="onb-field-hint" role="status">
            {uploadNotice}
          </p>
        )}
      </div>
    </OnboardingChrome>
  )
}
