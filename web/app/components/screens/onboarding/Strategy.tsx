"use client"

import { useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "../../../lib/auth"
import { OnboardingChrome } from "../../onboarding/OnboardingChrome"
import { useOnboarding } from "../../../context/OnboardingContext"
import { useContent } from "../../../context/ContentContext"
import { completeOnboarding, updateWorkspace } from "../../../lib/onboarding/store"
import { saveDraft, loadDraft, clearDraft } from "../../../lib/onboarding/useFormDraft"
import { briefToContentPatch } from "../../../lib/brief-adapter"
import {
  ensureDatasetForWorkspace,
  fetchBriefWhenReady,
  seedWorkspaceContextFiles,
  startBriefGeneration,
} from "../../../lib/workspace-brief"
import { roadmapDocApi } from "../../../lib/api"
import { FileText, Check } from "../../auth/icons"

const DRAFT_KEY = "strategy"

/**
 * Onboarding step 05 — "Strategy, leadership & your roadmap" (scene onbstrat).
 *
 * The FINAL step: it COMPLETES onboarding and enters the app. (The workspace
 * step moved EARLY in the redesign; completion + first-brief kickoff relocated
 * here from it.)
 *
 * Content (unchanged for now — the 4 upload cards are a separate follow-up PR):
 *   - a free-text "current priorities" field (persisted to companies.okrs, the
 *     existing strategic-context slot), and
 *   - a ROADMAP-DOC upload that posts to `POST /v1/company/roadmap-doc`, which
 *     stores the doc + its extracted text against the company. The stored
 *     roadmap feeds the weekly brief as a high-weight priorities signal and
 *     renders read-only as the `roadmapdoc` artifact view.
 *
 * On "Finish setup" we:
 *   1. persist the (optional) priorities,
 *   2. kick the first weekly brief generation (fire-and-forget; it lands on the
 *      Brief page when ready),
 *   3. completeOnboarding → set the active company → router.replace("/brief").
 * Brief-generation is best-effort: a failure there must NOT trap the user on the
 * last step. completeOnboarding is the only hard requirement.
 *
 * The roadmap-doc upload shows the design's "uploaded" confirmation state on
 * success; a failure is caught as a non-blocking notice — the upload is optional
 * and the whole step is skippable.
 */
export function Strategy() {
  const auth = useAuth()
  const { workspace, loading } = useOnboarding()
  const { setContent } = useContent()
  const router = useRouter()
  const draft = loadDraft(DRAFT_KEY)
  const [priorities, setPriorities] = useState<string>((draft?.priorities as string) ?? "")
  const [roadmapFileName, setRoadmapFileName] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [uploaded, setUploaded] = useState(false)
  const [uploadNotice, setUploadNotice] = useState<string | null>(null)
  const [finishing, setFinishing] = useState(false)
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
    setUploaded(false)
    setRoadmapFileName(file.name)
    setUploading(true)
    try {
      await roadmapDocApi.upload(file)
      setUploaded(true)
      setUploadNotice(`Your roadmap · uploaded just now — we'll pressure-test it against your data.`)
    } catch {
      // The upload is optional and the step is skippable; a transient failure
      // surfaces a non-blocking notice rather than halting onboarding.
      setUploaded(false)
      setUploadNotice(
        `Couldn't upload "${file.name}" just now — you can re-try here or add it later in Settings. This won't block setup.`,
      )
    } finally {
      setUploading(false)
    }
  }

  // The closing step: persist priorities, kick the first brief, COMPLETE
  // onboarding, and enter the app. `skipped` only skips persisting priorities —
  // completion always runs.
  async function finish(skipped: boolean) {
    if (!workspace || auth.kind !== "authed") return
    setError(null)
    setFinishing(true)
    try {
      // 1) Persist the (optional) priorities when not skipped.
      if (!skipped && priorities.trim()) {
        await updateWorkspace(workspace.id, { okrs: priorities.trim() })
      }
      clearDraft(DRAFT_KEY)

      // 2) Kick the first brief (fire-and-forget). It lands on the Brief page.
      void (async () => {
        try {
          await ensureDatasetForWorkspace(workspace)
          await seedWorkspaceContextFiles(workspace)
          const existing = await fetchBriefWhenReady(workspace.slug)
          if (existing) setContent(briefToContentPatch(existing))
          else await startBriefGeneration(workspace.slug)
        } catch {
          /* generation runs server-side; the Brief page reflects status */
        }
      })()

      // 3) Complete onboarding and enter the app.
      await completeOnboarding(workspace.id, auth.user.id)
      if (typeof window !== "undefined") {
        window.localStorage.setItem("sprntly_active_company", workspace.slug)
      }
      router.replace("/brief")
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't finish setting up your workspace.")
      setFinishing(false)
    }
  }

  if (loading || !workspace) return <div className="onb-shell">Loading…</div>

  return (
    <OnboardingChrome
      step={5}
      saveLabel="Saved · auto-saves"
      title={
        <>
          Strategy, leadership &amp; <em>your roadmap.</em>
        </>
      }
      subtitle="Give the agents what shapes your priorities. The more you add, the sharper every brief and roadmap gets — you can always add more in Settings."
      footerMeta={
        <>
          Step 5 of 5 · strategy —{" "}
          <button
            type="button"
            className="onb-skip-link"
            onClick={() => void finish(true)}
            disabled={finishing}
          >
            Skip
          </button>{" "}
          · your first Brief starts generating when you finish
        </>
      }
      onBack={() => router.push("/onboarding/business-context")}
      onContinue={() => void finish(false)}
      continueLabel="Finish setup"
      continueDisabled={finishing}
      loading={finishing}
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
          className={`onb-up onb-up-wide ${uploaded ? "has-file" : ""}`}
          onClick={() => fileRef.current?.click()}
          disabled={uploading}
          data-field="roadmap-doc"
          data-uploaded={uploaded ? "true" : undefined}
        >
          <span className="onb-up-ic" aria-hidden>
            {uploaded ? (
              <Check style={{ width: 16, height: 16 }} />
            ) : (
              <FileText style={{ width: 16, height: 16 }} />
            )}
          </span>
          <span className="onb-up-b">
            <span className="onb-up-t">
              {uploading
                ? "Uploading…"
                : roadmapFileName
                  ? roadmapFileName
                  : "Upload your current roadmap"}
            </span>
            <span className="onb-up-s">
              {uploaded
                ? "Loaded in — we'll pressure-test it against your data."
                : "Spreadsheet, deck, or doc — Sprntly loads it in and pressure-tests it against your data."}
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
