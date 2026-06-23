"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "../../../lib/auth"
import { OnboardingChrome } from "../../onboarding/OnboardingChrome"
import { useOnboarding } from "../../../context/OnboardingContext"
import {
  completeOnboarding,
  sendWorkspaceInvites,
  updateWorkspace,
} from "../../../lib/onboarding/store"
import { useContent } from "../../../context/ContentContext"
import { briefToContentPatch } from "../../../lib/brief-adapter"
import {
  ensureDatasetForWorkspace,
  fetchBriefWhenReady,
  seedWorkspaceContextFiles,
  startBriefGeneration,
} from "../../../lib/workspace-brief"
import { Plus } from "../../auth/icons"

const ROLE_OPTIONS = ["Member", "Admin"] as const

type InviteRow = { email: string; role: (typeof ROLE_OPTIONS)[number] }

function isValidEmail(v: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v.trim())
}

/**
 * Onboarding step 05 — "Create your workspace" (design scene onbws).
 *
 * The final numbered step: confirm the workspace name and (optionally) invite
 * colleagues, then COMPLETE onboarding and enter the app. On finish we:
 *   1. persist the workspace name (display_name),
 *   2. send any valid invites (sendWorkspaceInvites — best-effort),
 *   3. kick the first weekly brief generation (fire-and-forget; it lands on the
 *      Brief page when ready), and
 *   4. completeOnboarding → set the active company → router.replace("/brief").
 *
 * Invites and brief-generation are best-effort: a failure there must NOT trap
 * the user on this last step, so they're caught and the user still enters the
 * app. completeOnboarding is the only hard requirement.
 */
export function Workspace() {
  const auth = useAuth()
  const { workspace, setWorkspace, loading } = useOnboarding()
  const { setContent } = useContent()
  const router = useRouter()

  const [name, setName] = useState("")
  const [invites, setInvites] = useState<InviteRow[]>([{ email: "", role: "Member" }])
  const [finishing, setFinishing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!workspace) return
    setName(workspace.display_name ?? "")
  }, [workspace])

  // Redirect when there's no workspace to anchor the step.
  useEffect(() => {
    if (!loading && !workspace) router.replace("/onboarding/business-info")
  }, [loading, workspace, router])

  function setInvite(i: number, patch: Partial<InviteRow>) {
    setInvites((prev) => prev.map((row, idx) => (idx === i ? { ...row, ...patch } : row)))
  }
  function addInviteRow() {
    setInvites((prev) => [...prev, { email: "", role: "Member" }])
  }
  function removeInviteRow(i: number) {
    setInvites((prev) => (prev.length === 1 ? prev : prev.filter((_, idx) => idx !== i)))
  }

  async function finish() {
    if (!workspace || auth.kind !== "authed") return
    setError(null)
    setFinishing(true)
    try {
      // 1) Persist the (optional) workspace name when it changed.
      const trimmed = name.trim()
      let ws = workspace
      if (trimmed && trimmed !== workspace.display_name) {
        ws = await updateWorkspace(workspace.id, { display_name: trimmed })
        setWorkspace(ws)
      }

      // 2) Send valid invites (best-effort — never blocks finishing).
      const valid = invites
        .map((r) => ({ email: r.email.trim(), role: r.role }))
        .filter((r) => isValidEmail(r.email))
      if (valid.length) {
        try {
          await sendWorkspaceInvites(workspace.id, valid, auth.user.id)
        } catch {
          /* best-effort — invites can be re-sent from Team settings */
        }
      }

      // 3) Kick the first brief (fire-and-forget). It lands on the Brief page.
      void (async () => {
        try {
          await ensureDatasetForWorkspace(ws)
          await seedWorkspaceContextFiles(ws)
          const existing = await fetchBriefWhenReady(ws.slug)
          if (existing) setContent(briefToContentPatch(existing))
          else await startBriefGeneration(ws.slug)
        } catch {
          /* generation runs server-side; the Brief page reflects status */
        }
      })()

      // 4) Complete onboarding and enter the app.
      await completeOnboarding(workspace.id, auth.user.id)
      if (typeof window !== "undefined") {
        window.localStorage.setItem("sprntly_active_company", workspace.slug)
      }
      router.replace("/brief")
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't create your workspace.")
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
          Create your <em>workspace.</em>
        </>
      }
      subtitle="Your workspace is the shared home where you invite colleagues, run briefs, and collaborate. Invite your team now or later from Settings."
      footerMeta="Step 5 of 5 · workspace — your first Brief starts generating when you finish"
      onBack={() => router.push("/onboarding/strategy")}
      onContinue={() => void finish()}
      continueLabel="Create workspace & enter"
      continueDisabled={finishing}
      loading={finishing}
    >
      {error && <div className="onb-form-error">{error}</div>}

      <div className="form-grid">
        <div className="field full" data-field="workspaceName">
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
          <p className="onb-field-hint">You can change this later in Settings.</p>
        </div>
      </div>

      <div className="onb-section" style={{ marginTop: 22 }}>
        <div className="onb-section-h">
          Invite your team <span className="opt">— optional</span>
        </div>
        {invites.map((row, i) => (
          <div className="invite-row" key={i} data-field={`invite-${i}`}>
            <input
              className="inp"
              type="email"
              value={row.email}
              onChange={(e) => setInvite(i, { email: e.target.value })}
              placeholder="colleague@company.com"
              aria-label={`Invite email ${i + 1}`}
            />
            <select
              className="inp"
              value={row.role}
              onChange={(e) =>
                setInvite(i, { role: e.target.value as InviteRow["role"] })
              }
              aria-label={`Invite role ${i + 1}`}
            >
              {ROLE_OPTIONS.map((r) => (
                <option key={r}>{r}</option>
              ))}
            </select>
            {invites.length > 1 && (
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => removeInviteRow(i)}
                aria-label={`Remove invite ${i + 1}`}
              >
                Remove
              </button>
            )}
          </div>
        ))}
        <button
          type="button"
          className="btn btn-secondary"
          onClick={addInviteRow}
          style={{ marginTop: 10 }}
        >
          <Plus style={{ width: 13, height: 13 }} aria-hidden /> Add another
        </button>
      </div>
    </OnboardingChrome>
  )
}
