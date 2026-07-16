"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "../../../lib/auth"
import { useFieldValidation } from "../../onboarding/InterviewLayout"
import { OnboardingChrome } from "../../onboarding/OnboardingChrome"
import { OptionalDisclosure } from "../../onboarding/OptionalDisclosure"
import { useOnboarding } from "../../../context/OnboardingContext"
import { requiredFor } from "../../../lib/onboarding/validation"
import {
  markSkippedFields,
  saveNotificationBriefDay,
  updateWorkspace,
} from "../../../lib/onboarding/store"
import { PRIORITIZATION_FRAMEWORKS } from "../../../lib/onboarding/types"
import { teamApi, type InviteRole } from "../../../lib/teamApi"
import { saveDraft, loadDraft, clearDraft } from "../../../lib/onboarding/useFormDraft"
import { Plus } from "../../auth/icons"

const DRAFT_KEY = "team-step"

const WEEKDAYS = [
  "Sunday",
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
] as const

type InviteRow = { email: string; role: InviteRole }

/**
 * Onboarding step 06 — "Your team" (registration spec 2026-07, Team section).
 *
 * Team scope* (the exact product area, e.g. "notifications") and
 * prioritization framework* are mandatory for COMPANY accounts. Teammate
 * invites (email + role, via the existing POST /v1/team/invites — best-effort,
 * a failed invite never blocks the step) and the weekly-brief day live behind
 * an optional disclosure. Sizing methodology is settings-only per spec.
 */
export function TeamStep() {
  const auth = useAuth()
  const { workspace, profile, setWorkspace, loading } = useOnboarding()
  const router = useRouter()

  const draft = loadDraft(DRAFT_KEY)
  const [teamScope, setTeamScope] = useState((draft?.teamScope as string) ?? "")
  const [framework, setFramework] = useState((draft?.framework as string) ?? "")
  const [invites, setInvites] = useState<InviteRow[]>(
    (draft?.invites as InviteRow[]) ?? [],
  )
  const [inviteEmail, setInviteEmail] = useState("")
  const [inviteRole, setInviteRole] = useState<InviteRole>("member")
  const [briefDay, setBriefDay] = useState<number | null>(
    typeof draft?.briefDay === "number" ? (draft.briefDay as number) : null,
  )

  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [inviteNotice, setInviteNotice] = useState<string | null>(null)

  const isCompany = (profile?.account_type ?? "company") === "company"

  // Seed from the saved workspace (draft takes priority).
  useEffect(() => {
    if (!workspace) return
    if (draft) return
    setTeamScope(workspace.team_scope ?? "")
    setFramework(workspace.prioritization_framework ?? "")
    const wd = workspace.notification_settings?.brief_weekday
    if (typeof wd === "number") setBriefDay(wd)
  }, [workspace]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const onHide = () => {
      if (document.hidden)
        saveDraft(DRAFT_KEY, { teamScope, framework, invites, briefDay })
    }
    document.addEventListener("visibilitychange", onHide)
    return () => document.removeEventListener("visibilitychange", onHide)
  }, [teamScope, framework, invites, briefDay])

  // Redirect when there's no workspace to anchor the step.
  useEffect(() => {
    if (!loading && !workspace) router.replace("/onboarding/company")
  }, [loading, workspace, router])

  const { errors, validate, clearError, containerRef } = useFieldValidation(() => [
    requiredFor(isCompany, {
      key: "teamScope",
      valid: teamScope.trim().length > 0,
      message: "Name the product area this team owns.",
    }),
    requiredFor(isCompany, {
      key: "framework",
      valid: framework.trim().length > 0,
      message: "Pick your prioritization framework.",
    }),
  ])

  function addInvite() {
    const email = inviteEmail.trim().toLowerCase()
    if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return
    setInvites((prev) =>
      prev.some((i) => i.email === email)
        ? prev
        : [...prev, { email, role: inviteRole }],
    )
    setInviteEmail("")
  }

  async function save() {
    if (!workspace || auth.kind !== "authed") return
    setError(null)
    setInviteNotice(null)
    if (!validate().ok) return
    setSaving(true)
    try {
      const skipped: string[] = []
      if (!isCompany) {
        if (!teamScope.trim()) skipped.push("team_scope")
        if (!framework.trim()) skipped.push("prioritization_framework")
      }
      let updated = await updateWorkspace(workspace.id, {
        team_scope: teamScope.trim() || null,
        prioritization_framework: framework || null,
        onboarding_step: 7,
      })
      if (briefDay !== null) {
        updated = await saveNotificationBriefDay(workspace.id, briefDay)
      }
      setWorkspace({ ...updated, product: workspace.product })

      // Send the queued invites through the existing team API. Best-effort:
      // a failed invite surfaces a notice but never blocks onboarding (they
      // can re-invite from Settings → Team).
      if (invites.length) {
        const failures: string[] = []
        for (const inv of invites) {
          try {
            await teamApi.invite(inv.email, inv.role)
          } catch {
            failures.push(inv.email)
          }
        }
        if (failures.length) {
          setInviteNotice(
            `Couldn't invite ${failures.join(", ")} — you can re-invite them in Settings → Team.`,
          )
        }
      }

      if (skipped.length) await markSkippedFields(auth.user.id, skipped)
      clearDraft(DRAFT_KEY)
      router.push("/onboarding/strategy")
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save your team setup.")
      setSaving(false)
    }
  }

  if (loading || !workspace) return <div className="onb-shell">Loading…</div>

  return (
    <OnboardingChrome
      step={6}
      saveLabel="Saved · auto-saves"
      title={
        <>
          Your <em>team.</em>
        </>
      }
      subtitle="What this team owns and how you prioritize. Invites and the brief schedule are optional — Settings has the full controls."
      footerMeta={
        isCompany
          ? "Scope and framework are required — invites are optional."
          : "Everything here is optional — add what you like."
      }
      onBack={() => router.push("/onboarding/connectors")}
      onContinue={() => void save()}
      continueDisabled={saving}
      loading={saving}
    >
      <div ref={containerRef}>
        {error && <div className="onb-form-error">{error}</div>}
        {inviteNotice && (
          <p className="onb-field-hint" role="status">
            {inviteNotice}
          </p>
        )}

        <div className="form-grid">
          <div className="field full" data-field="teamScope">
            <div className="field-l">
              Scope — the exact product area{" "}
              {isCompany ? (
                <span className="req">*</span>
              ) : (
                <span className="opt">optional</span>
              )}
            </div>
            <input
              className={`inp ${errors.teamScope ? "has-error" : ""}`}
              value={teamScope}
              onChange={(e) => {
                setTeamScope(e.target.value)
                clearError("teamScope")
              }}
              maxLength={100}
              placeholder="e.g. notifications"
            />
            {errors.teamScope && <p className="onb-field-error">{errors.teamScope}</p>}
          </div>

          <div className="field full" data-field="framework">
            <div className="field-l">
              Prioritization framework{" "}
              {isCompany ? (
                <span className="req">*</span>
              ) : (
                <span className="opt">optional</span>
              )}
            </div>
            <select
              className={`inp ${errors.framework ? "has-error" : ""}`}
              value={framework}
              onChange={(e) => {
                setFramework(e.target.value)
                clearError("framework")
              }}
              aria-label="Prioritization framework"
            >
              <option value="">Select a framework</option>
              {PRIORITIZATION_FRAMEWORKS.map((f) => (
                <option key={f.value} value={f.value}>
                  {f.label}
                </option>
              ))}
            </select>
            {errors.framework && <p className="onb-field-error">{errors.framework}</p>}
          </div>
        </div>

        <OptionalDisclosure label="Invite teammates & pick your brief day">
          <div className="onb-section">
            <div className="onb-section-h">
              Invite teammates <span className="opt">— email + role</span>
            </div>
            {invites.length > 0 && (
              <div className="metric-chips" style={{ marginBottom: 8 }}>
                {invites.map((inv) => (
                  <button
                    type="button"
                    key={inv.email}
                    className="metric sel"
                    onClick={() =>
                      setInvites((prev) => prev.filter((i) => i.email !== inv.email))
                    }
                    title="Remove"
                  >
                    {inv.email} · {inv.role}
                  </button>
                ))}
              </div>
            )}
            <div className="metric-other-row">
              <input
                className="inp"
                type="email"
                value={inviteEmail}
                onChange={(e) => setInviteEmail(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault()
                    addInvite()
                  }
                }}
                placeholder="teammate@company.com"
                aria-label="Teammate email"
              />
              <select
                className="inp"
                style={{ maxWidth: 120 }}
                value={inviteRole}
                onChange={(e) => setInviteRole(e.target.value as InviteRole)}
                aria-label="Teammate role"
              >
                <option value="member">Member</option>
                <option value="admin">Admin</option>
                <option value="viewer">Viewer</option>
              </select>
              <button
                type="button"
                className="btn btn-secondary"
                onClick={addInvite}
                disabled={!inviteEmail.trim()}
              >
                <Plus style={{ width: 13, height: 13 }} aria-hidden /> Add
              </button>
            </div>
            <p className="onb-field-hint">
              Invites send when you continue. Manage the team any time in
              Settings → Team.
            </p>
          </div>

          <div className="onb-section" style={{ marginTop: 16 }}>
            <div className="onb-section-h">
              Weekly brief day{" "}
              <span className="opt">— when should your brief arrive?</span>
            </div>
            <select
              className="inp"
              value={briefDay === null ? "" : String(briefDay)}
              onChange={(e) =>
                setBriefDay(e.target.value === "" ? null : Number(e.target.value))
              }
              aria-label="Weekly brief day"
            >
              <option value="">Default (Monday)</option>
              {WEEKDAYS.map((d, i) => (
                <option key={d} value={i}>
                  {d}
                </option>
              ))}
            </select>
          </div>
        </OptionalDisclosure>
      </div>
    </OnboardingChrome>
  )
}
