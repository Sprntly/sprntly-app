"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { AuthShell } from "../../auth/AuthShell"
import { ArrowRight } from "../../auth/icons"
import { useOnboarding } from "../../../context/OnboardingContext"
import { advanceOnboardingStep, updateWorkspace } from "../../../lib/onboarding/store"

/**
 * Onboarding step 02 — "Create your workspace" (design scene onbws).
 *
 * A SLIM, EARLY, name-only step. It uses the design's minimal auth-card layout
 * (not the numbered onb-shell chrome): a header, a sub, a single optional
 * "Workspace name" field + hint, and a single "Continue →".
 *
 * It does exactly two things:
 *   1. persist the (optional) workspace name (display_name) when it changed, and
 *   2. advance to the connectors step (index 3) and route there.
 *
 * The team-invite UI, the first-brief kickoff, and onboarding completion that
 * used to live here have MOVED:
 *   - invites → Settings → Team (TeamSettings), and
 *   - completion + first brief → the final Strategy step (onbstrat).
 */
export function Workspace() {
  const { workspace, setWorkspace, loading } = useOnboarding()
  const router = useRouter()

  const [name, setName] = useState("")
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!workspace) return
    setName(workspace.display_name ?? "")
  }, [workspace])

  // Redirect when there's no workspace to anchor the step.
  useEffect(() => {
    if (!loading && !workspace) router.replace("/onboarding/business-info")
  }, [loading, workspace, router])

  async function continueToConnectors() {
    if (!workspace) return
    setError(null)
    setSaving(true)
    try {
      // Persist the (optional) workspace name when it changed, advancing the
      // resume marker to connectors (index 3 in ONBOARDING_STEP_SLUGS) in the
      // same write. When it's unchanged we only advance the step.
      const trimmed = name.trim()
      if (trimmed && trimmed !== workspace.display_name) {
        const ws = await updateWorkspace(workspace.id, {
          display_name: trimmed,
          onboarding_step: 3,
        })
        setWorkspace(ws)
      } else {
        const ws = await advanceOnboardingStep(workspace.id, 3)
        setWorkspace(ws)
      }
      router.push("/onboarding/connectors")
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save your workspace.")
      setSaving(false)
    }
  }

  if (loading || !workspace) return <div className="onb-shell">Loading…</div>

  return (
    <AuthShell tag="Create workspace" cardClassName="auth-card-wide" showMeta={false}>
      <div className="auth-h">
        Create your <em>workspace.</em>
      </div>
      <div className="auth-sub">
        Your workspace is the shared home where you invite colleagues, run
        briefs, and collaborate.
      </div>

      {error && <div className="onb-form-error">{error}</div>}

      <div className="field" data-field="workspaceName">
        <div className="field-l">
          Workspace name <span className="opt">optional</span>
        </div>
        <input
          className="inp"
          value={name}
          onChange={(e) => setName(e.target.value)}
          maxLength={100}
          placeholder="Usually your company or team name"
          aria-label="Workspace name"
        />
        <div className="field-hint">
          Usually your company or team name. You can change this later.
        </div>
      </div>

      <button
        type="button"
        className="btn btn-brand btn-block"
        style={{ marginTop: 10 }}
        onClick={() => void continueToConnectors()}
        disabled={saving}
      >
        {saving ? "Saving…" : "Continue"}
        {!saving && <ArrowRight style={{ width: 14, height: 14 }} aria-hidden />}
      </button>

      <div className="auth-foot">
        <a
          role="button"
          tabIndex={0}
          onClick={() => router.push("/onboarding/business-info")}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault()
              router.push("/onboarding/business-info")
            }
          }}
        >
          Back
        </a>
      </div>
    </AuthShell>
  )
}
