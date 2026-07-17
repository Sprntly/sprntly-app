"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "../../../lib/auth"
import { useFieldValidation } from "../../onboarding/InterviewLayout"
import { OnboardingChrome } from "../../onboarding/OnboardingChrome"
import { useOnboarding } from "../../../context/OnboardingContext"
import {
  advanceOnboardingStep,
  updateWorkspace,
} from "../../../lib/onboarding/store"
import { ONBOARDING_STEP_COUNT } from "../../../lib/onboarding/types"
import { saveDraft, loadDraft, clearDraft } from "../../../lib/onboarding/useFormDraft"

const DRAFT_KEY = "team-step"

/**
 * Onboarding step 05 — "Your team" (v6 screenshot spec 2026-07-17).
 *
 * Team name* and scope of work* only. The prioritization framework moved to
 * the metrics step, teammate invites to the invite step (08), and the
 * weekly-brief day to Settings → Comms & Brief.
 *
 * The team name is a COMPANY field (companies.team_name) — deliberately not
 * the workspaces row, which stays "Default" until renamed in Settings →
 * Workspaces (July 17 decision).
 */
export function TeamStep() {
  const auth = useAuth()
  const { workspace, setWorkspace, loading } = useOnboarding()
  const router = useRouter()

  const draft = loadDraft(DRAFT_KEY)
  const [teamName, setTeamName] = useState((draft?.teamName as string) ?? "")
  const [teamScope, setTeamScope] = useState((draft?.teamScope as string) ?? "")

  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Seed from the saved workspace (draft takes priority).
  useEffect(() => {
    if (!workspace) return
    if (draft) return
    setTeamName(workspace.team_name ?? "")
    setTeamScope(workspace.team_scope ?? "")
  }, [workspace]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const onHide = () => {
      if (document.hidden) saveDraft(DRAFT_KEY, { teamName, teamScope })
    }
    document.addEventListener("visibilitychange", onHide)
    return () => document.removeEventListener("visibilitychange", onHide)
  }, [teamName, teamScope])

  // Redirect when there's no workspace to anchor the step.
  useEffect(() => {
    if (!loading && !workspace) router.replace("/onboarding/company")
  }, [loading, workspace, router])

  const { errors, validate, clearError, containerRef } = useFieldValidation(() => [
    {
      key: "teamName",
      valid: teamName.trim().length > 0,
      message: "Name your team.",
    },
    {
      key: "teamScope",
      valid: teamScope.trim().length > 0,
      message: "Describe the area this team owns.",
    },
  ])

  async function persist(nextStep: number): Promise<boolean> {
    if (!workspace || auth.kind !== "authed") return false
    setError(null)
    if (!validate().ok) return false
    setSaving(true)
    try {
      const updated = await updateWorkspace(workspace.id, {
        team_name: teamName.trim() || null,
        team_scope: teamScope.trim() || null,
        onboarding_step: nextStep,
      })
      setWorkspace({ ...updated, product: workspace.product })
      clearDraft(DRAFT_KEY)
      return true
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save your team setup.")
      setSaving(false)
      return false
    }
  }

  async function save() {
    if (await persist(6)) router.push("/onboarding/strategy")
  }

  async function skipToEnd() {
    if (!workspace) return
    if (await persist(ONBOARDING_STEP_COUNT)) router.push("/onboarding/review")
  }

  if (loading || !workspace) return <div className="onb-shell">Loading…</div>

  return (
    <OnboardingChrome
      step={5}
      saveLabel="Saved · auto-saves"
      title={
        <>
          Your <em>team.</em>
        </>
      }
      subtitle="Name the team and describe the area you own."
      footerMeta="Team"
      onBack={() => router.push("/onboarding/connectors")}
      onContinue={() => void save()}
      onSkipToEnd={() => void skipToEnd()}
      continueLabel="Next"
      continueDisabled={saving}
      loading={saving}
    >
      <div ref={containerRef}>
        {error && <div className="onb-form-error">{error}</div>}

        <div className="form-grid">
          <div className="field full" data-field="teamName">
            <div className="field-l">
              Team name <span className="req">*</span>
            </div>
            <input
              className={`inp ${errors.teamName ? "has-error" : ""}`}
              value={teamName}
              onChange={(e) => {
                setTeamName(e.target.value)
                clearError("teamName")
              }}
              maxLength={100}
              placeholder="e.g. Nutrition & Sleep"
            />
            {errors.teamName && <p className="onb-field-error">{errors.teamName}</p>}
          </div>

          <div className="field full" data-field="teamScope">
            <div className="field-l">
              Scope of work <span className="req">*</span>
            </div>
            <textarea
              className={`inp ${errors.teamScope ? "has-error" : ""}`}
              rows={5}
              value={teamScope}
              onChange={(e) => {
                setTeamScope(e.target.value)
                clearError("teamScope")
              }}
              maxLength={1000}
              placeholder="What this team owns end to end — the surfaces, flows, and outcomes you're responsible for"
            />
            {errors.teamScope && <p className="onb-field-error">{errors.teamScope}</p>}
          </div>
        </div>
      </div>
    </OnboardingChrome>
  )
}
