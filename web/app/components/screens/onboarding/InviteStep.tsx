"use client"

import { useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "../../../lib/auth"
import { OnboardingChrome } from "../../onboarding/OnboardingChrome"
import { useOnboarding } from "../../../context/OnboardingContext"
import { advanceOnboardingStep } from "../../../lib/onboarding/store"
import { prefetchBusinessContextDraft } from "../../../lib/onboarding/draftPrefetch"
import { JOB_ROLE_OPTIONS, stepForSlug } from "../../../lib/onboarding/types"
import {
  parseInvitesCsv,
  parsePastedEmails,
  teamApi,
  type InviteRole,
  type InviteRow,
} from "../../../lib/teamApi"
import { saveDraft, loadDraft, clearDraft } from "../../../lib/onboarding/useFormDraft"
import { Plus } from "../../auth/icons"

const DRAFT_KEY = "invite-step"

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

function emptyRow(): InviteRow {
  return { email: "", jobRole: JOB_ROLE_OPTIONS[0], permission: "member" }
}

/**
 * Onboarding step 03 — "Invite your team." Nothing here is required: Next on
 * an empty form sends nothing and moves on, which is why the Skip that used to
 * sit beside it came off (2026-09-08).
 *
 * REINSTATED 2026-09-07. Cut from the flow on 2026-09-03 along with six other
 * steps that were all questions ABOUT the product — OKRs, success metrics, a
 * prioritization framework — asked of someone who hadn't seen it yet. Invite
 * is different: it isn't a question about the product, it's how a company
 * grows into having more than one user of it, and the earlier that happens
 * the sooner there is more than one person's usage to learn from. It goes
 * back exactly where it sat before, right after connecting tools and before
 * reading the AI-drafted business context — see lib/onboarding/types.ts.
 *
 * Rows of email + JOB role (Data Science, Engineer…) + permission
 * (member/admin/viewer), an "Add teammate" row-appender, and a CSV import
 * (email, job role, permission per line) and a bulk paste field, both behind
 * the "Add multiple people at once" disclosure. Invites send on Continue
 * through the existing POST /v1/team/invites — best-effort, a failed invite
 * never blocks the step (re-invite from Settings → Team). The bulk parsers
 * (parsePastedEmails, parseInvitesCsv) live in lib/teamApi.ts, shared with the
 * Settings → Team & roles pane that kept this same capability while this step
 * was gone — this step is an ADDITIONAL front door onto it, not a replacement.
 */
export function InviteStep() {
  const auth = useAuth()
  const { workspace, setWorkspace, loading } = useOnboarding()
  const router = useRouter()

  const draft = loadDraft(DRAFT_KEY)
  const [rows, setRows] = useState<InviteRow[]>(
    (draft?.rows as InviteRow[]) ?? [emptyRow()],
  )
  const csvRef = useRef<HTMLInputElement | null>(null)

  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  // Bulk paste field (behind the "Add multiple people at once" disclosure).
  const [bulk, setBulk] = useState("")
  const [bulkNotice, setBulkNotice] = useState<string | null>(null)
  // The "Add multiple people at once" toggle sits inline with "Add teammate"
  // (its body drops below the row), so this step owns the open state locally
  // rather than delegating it to <OptionalDisclosure>.
  const [bulkOpen, setBulkOpen] = useState(false)

  /**
   * Turn the pasted blob into rows, appending only addresses not already
   * listed. Replaces a blank trailing row rather than leaving a hole above the
   * new entries.
   */
  function addPastedEmails() {
    const parsed = parsePastedEmails(bulk, rows)
    if (parsed.length === 0) {
      setBulkNotice("No new valid email addresses in that paste.")
      return
    }
    setRows((prev) => {
      const kept = prev.filter((r) => r.email.trim())
      return [...kept, ...parsed]
    })
    setBulk("")
    setBulkNotice(
      parsed.length === 1 ? "1 teammate added." : `${parsed.length} teammates added.`,
    )
  }

  useEffect(() => {
    const onHide = () => {
      if (document.hidden) saveDraft(DRAFT_KEY, { rows })
    }
    document.addEventListener("visibilitychange", onHide)
    return () => document.removeEventListener("visibilitychange", onHide)
  }, [rows])

  // Redirect when there's no workspace to anchor the step.
  useEffect(() => {
    if (!loading && !workspace) router.replace("/onboarding/company")
  }, [loading, workspace, router])

  // Kick the review-step business-context draft in the BACKGROUND now — every
  // input it reads (company, product, connected sources) is saved by earlier
  // steps, and invites don't affect it. By the time the user reaches the
  // review screen the prose is usually already generated (that screen joins
  // this same memoized request — see draftPrefetch.ts). Fire-and-forget; a
  // failure here just means the review screen retries on mount.
  const workspaceId = workspace?.id ?? null
  const hasSavedSummary = Boolean(workspace?.business_context_summary)
  useEffect(() => {
    if (!workspaceId || hasSavedSummary) return
    prefetchBusinessContextDraft(workspaceId).catch(() => {})
  }, [workspaceId, hasSavedSummary])

  function patchRow(i: number, patch: Partial<InviteRow>) {
    setRows((prev) => prev.map((r, j) => (j === i ? { ...r, ...patch } : r)))
  }

  function removeRow(i: number) {
    setRows((prev) => {
      const next = prev.filter((_, j) => j !== i)
      return next.length ? next : [emptyRow()]
    })
  }

  async function onPickCsv(file: File | null) {
    if (!file) return
    setNotice(null)
    try {
      const text = await file.text()
      const imported = parseInvitesCsv(text)
      if (!imported.length) {
        setNotice("No teammate rows found in that CSV — expected email, role, permission per line.")
        return
      }
      setRows((prev) => {
        const kept = prev.filter((r) => r.email.trim())
        const have = new Set(kept.map((r) => r.email.trim().toLowerCase()))
        const fresh = imported.filter((r) => !have.has(r.email))
        return [...kept, ...fresh]
      })
      setNotice(`Imported ${imported.length} teammate${imported.length === 1 ? "" : "s"} from ${file.name}.`)
    } catch {
      setNotice(`Couldn't read "${file.name}" — is it a plain CSV?`)
    } finally {
      if (csvRef.current) csvRef.current.value = ""
    }
  }

  /** Send whatever valid rows exist (best-effort), then advance. */
  async function go(nextStep: number, nextRoute: string) {
    if (!workspace || auth.kind !== "authed") return
    setError(null)
    setNotice(null)
    const valid = rows.filter((r) => EMAIL_RE.test(r.email.trim().toLowerCase()))
    setSaving(true)
    try {
      const failures: { email: string; reason: string | null }[] = []
      for (const row of valid) {
        try {
          await teamApi.invite(
            row.email.trim().toLowerCase(),
            row.permission,
            [],
            row.jobRole,
          )
        } catch (err) {
          failures.push({
            email: row.email,
            reason: err instanceof Error && err.message ? err.message : null,
          })
        }
      }
      if (failures.length) {
        // A single refusal carries the backend's reason (e.g. "already
        // belongs to another company") — show it; multiple failures get the
        // compact list form.
        const [first] = failures
        setNotice(
          failures.length === 1 && first.reason
            ? `Couldn't invite ${first.email}: ${first.reason}`
            : `Couldn't invite ${failures.map((f) => f.email).join(", ")} — you can re-invite them in Settings → Team.`,
        )
      }
      const updated = await advanceOnboardingStep(workspace.id, nextStep)
      setWorkspace({ ...updated, product: workspace.product })
      clearDraft(DRAFT_KEY)
      router.push(nextRoute)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't send your invites.")
      setSaving(false)
    }
  }

  /* THE SKIP BUTTON IS GONE, and with it the separate `skip()` path it
     called. It sat beside Next and did the same thing: `go()` filters to rows
     carrying a valid email, so pressing Next on an empty form sends nothing
     and advances — which is what skipping is. Two buttons for one outcome
     asks the reader to work out a difference that was never there. Leaving
     the step empty is still supported and still costs one click; it is just
     the same click as everyone else's. */

  if (loading || !workspace) return <div className="onb-shell">Loading…</div>

  return (
    <OnboardingChrome
      step={stepForSlug("invite") ?? 3}
      saveLabel="Saved · auto-saves"
      title={
        <>
          Invite your <em>team.</em>
        </>
      }
      subtitle="Add teammates by email, role and permission. Leave it empty if you'd rather do this later — you can invite anyone from Settings → Team."
      onBack={() => router.push("/onboarding/connectors")}
      onContinue={() => void go(stepForSlug("review") ?? 4, "/onboarding/review")}
      continueLabel="Next"
      continueDisabled={saving}
      loading={saving}
      wideCard
    >
      {error && <div className="onb-form-error">{error}</div>}
      {notice && (
        <p className="onb-field-hint" role="status">
          {notice}
        </p>
      )}

      <div className="onb-invite-rows">
        <div className="onb-invite-head form-grid" aria-hidden>
          <div className="field-l">Email</div>
          <div className="field-l">Role</div>
          <div className="field-l">Permission</div>
        </div>
        {rows.map((row, i) => (
          <div className="metric-other-row" style={{ marginBottom: 8 }} key={i}>
            <input
              className="inp"
              type="email"
              value={row.email}
              onChange={(e) => patchRow(i, { email: e.target.value })}
              placeholder="teammate@company.com"
              aria-label={`Teammate ${i + 1} email`}
            />
            <select
              className="inp"
              style={{ maxWidth: 160 }}
              value={row.jobRole}
              onChange={(e) => patchRow(i, { jobRole: e.target.value })}
              aria-label={`Teammate ${i + 1} role`}
            >
              {JOB_ROLE_OPTIONS.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
            <select
              className="inp"
              style={{ maxWidth: 120 }}
              value={row.permission}
              onChange={(e) =>
                patchRow(i, { permission: e.target.value as InviteRole })
              }
              aria-label={`Teammate ${i + 1} permission`}
            >
              <option value="member">Member</option>
              <option value="admin">Admin</option>
              <option value="viewer">Viewer</option>
            </select>
            <button
              type="button"
              className="btn btn-ghost"
              onClick={() => removeRow(i)}
              aria-label={`Remove teammate ${i + 1}`}
            >
              ×
            </button>
          </div>
        ))}
      </div>

      <div style={{ marginTop: 10, display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        <button
          type="button"
          className="btn btn-secondary"
          onClick={() => setRows((prev) => [...prev, emptyRow()])}
        >
          <Plus style={{ width: 13, height: 13 }} aria-hidden /> Add teammate
        </button>
        <button
          type="button"
          className="onb-disclosure-toggle"
          style={{ width: "auto" }}
          aria-expanded={bulkOpen}
          onClick={() => setBulkOpen((v) => !v)}
        >
          <Plus
            style={{
              width: 12,
              height: 12,
              transform: bulkOpen ? "rotate(45deg)" : undefined,
              transition: "transform 0.15s",
            }}
            aria-hidden
          />
          <span className="t">Add multiple people at once</span>
          <span className="s">optional — you can finish this later in Settings</span>
        </button>
      </div>

      {bulkOpen && (
        <div className="onb-disclosure-body">
          <div className="field full" data-field="bulkEmails">
            <div className="field-l">
              Paste multiple emails <span className="opt">— separate with commas</span>
            </div>
            <div style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
              <input
                className="inp"
                value={bulk}
                onChange={(e) => setBulk(e.target.value)}
                placeholder="alex@company.com, sam@company.com, jordan@company.com"
                aria-label="Paste multiple emails"
              />
              <button
                type="button"
                className="btn btn-secondary"
                onClick={addPastedEmails}
                disabled={!bulk.trim()}
              >
                <Plus style={{ width: 13, height: 13 }} aria-hidden /> Add
              </button>
            </div>
            {bulkNotice && (
              <p className="onb-field-hint" role="status">
                {bulkNotice}
              </p>
            )}
          </div>

          <button
            type="button"
            className="btn btn-ghost"
            onClick={() => csvRef.current?.click()}
          >
            Import CSV
          </button>
          <input
            ref={csvRef}
            type="file"
            accept=".csv,text/csv"
            style={{ display: "none" }}
            onChange={(e) => void onPickCsv(e.target.files?.[0] ?? null)}
            aria-label="Import teammates CSV"
          />
        </div>
      )}
      <p className="onb-field-hint">
        Invites send when you continue. Manage the team any time in Settings → Team.
      </p>
    </OnboardingChrome>
  )
}
