/**
 * Sprntly staff admin panel (/admin) — org invites + per-company entitlements.
 *
 * Auth is a DEDICATED credential login, fully separate from the normal app
 * session: no staff token in sessionStorage ⇒ a standalone minimal ID +
 * Password form (never a redirect to the normal login). A successful
 * POST /v1/staff/login stores a short-lived staff JWT (sprntly_staff_token)
 * and every staff API call sends it as the Bearer. Any 401/404 from the
 * staff APIs — expired token, disabled surface, no credential — clears the
 * token and drops back to the login form; Sign out does the same.
 *
 * Two sections:
 *   1. Organizations — every company with its entitlements (modules,
 *      seat limit, prototype feature, platform-key vs BYOK) and an inline
 *      editor.
 *   2. Invitations — invite an organization by email with its deal's
 *      entitlements pre-configured; pending invites can be resent/revoked.
 *
 * Self-contained styling (scoped `sadm-` classes in an inline <style>) — this
 * page deliberately does not depend on the app shell or sidebar.
 */
"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import {
  ApiError,
  staffApi,
  staffAuth,
  type OrgInvite,
  type OrgInviteIn,
  type StaffCompany,
  type StaffEntitlementsPatch,
} from "../../../lib/api"

// The module toggles stored in companies.feature_flags — mirrors the
// FeatureFlags keys in lib/onboarding/types.ts.
export const MODULES: { key: string; label: string }[] = [
  { key: "weekly_brief", label: "Weekly Brief" },
  { key: "on_demand_analysis", label: "On-demand Analysis" },
  { key: "auto_prd_generation", label: "Auto PRD Generation" },
  { key: "engineer_agent", label: "Engineer Agent" },
  { key: "research_agent", label: "Research Agent" },
  { key: "on_call_agent", label: "On-call Agent" },
  { key: "claude_code_handoff", label: "Claude Code Handoff" },
]

export function keyModeLabel(c: {
  use_platform_key: boolean
  llm_key_configured: boolean
}): string {
  if (c.use_platform_key) return "Platform key"
  return c.llm_key_configured ? "Own key (set)" : "Own key (not set yet)"
}

function enabledModules(flags: Record<string, boolean>): string {
  const on = MODULES.filter((m) => flags[m.key]).map((m) => m.label)
  if (!on.length) return "No modules enabled"
  return on.join(", ")
}

function formatDate(iso: string | null): string {
  if (!iso) return "—"
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return "—"
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  })
}

// ── Entitlement form (shared by the company editor and the invite form) ──

type EntitlementFormState = {
  seatLimit: string // text input; "" = unlimited
  prototypeEnabled: boolean
  usePlatformKey: boolean
  featureFlags: Record<string, boolean>
}

function EntitlementFields({
  state,
  onChange,
  idPrefix,
}: {
  state: EntitlementFormState
  onChange: (next: EntitlementFormState) => void
  idPrefix: string
}) {
  return (
    <div className="sadm-fields">
      <label className="sadm-field">
        <span className="sadm-field-label">Seat limit</span>
        <input
          id={`${idPrefix}-seats`}
          type="number"
          min={1}
          placeholder="Unlimited"
          value={state.seatLimit}
          onChange={(e) => onChange({ ...state, seatLimit: e.target.value })}
        />
        <span className="sadm-field-hint">
          Members + pending invites. Empty = unlimited.
        </span>
      </label>

      <label className="sadm-check">
        <input
          type="checkbox"
          checked={state.prototypeEnabled}
          onChange={(e) =>
            onChange({ ...state, prototypeEnabled: e.target.checked })
          }
        />
        <span>Prototype feature (design agent)</span>
      </label>

      <label className="sadm-check">
        <input
          type="checkbox"
          checked={state.usePlatformKey}
          onChange={(e) =>
            onChange({ ...state, usePlatformKey: e.target.checked })
          }
        />
        <span>
          Use Sprntly&apos;s default Claude key
          <span className="sadm-field-hint">
            {" "}
            — unchecked, they must bring their own key in Settings
          </span>
        </span>
      </label>

      <fieldset className="sadm-modules">
        <legend>Modules</legend>
        {MODULES.map((m) => (
          <label key={m.key} className="sadm-check">
            <input
              type="checkbox"
              checked={!!state.featureFlags[m.key]}
              onChange={(e) =>
                onChange({
                  ...state,
                  featureFlags: {
                    ...state.featureFlags,
                    [m.key]: e.target.checked,
                  },
                })
              }
            />
            <span>{m.label}</span>
          </label>
        ))}
      </fieldset>
    </div>
  )
}

function formStateToPatch(state: EntitlementFormState): StaffEntitlementsPatch {
  const seats = state.seatLimit.trim()
  return {
    seat_limit: seats === "" ? null : Math.max(1, parseInt(seats, 10) || 1),
    prototype_enabled: state.prototypeEnabled,
    use_platform_key: state.usePlatformKey,
    feature_flags: state.featureFlags,
  }
}

// ── Company row + inline editor ──

function CompanyRow({
  company,
  onSaved,
}: {
  company: StaffCompany
  onSaved: (updated: StaffCompany) => void
}) {
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [form, setForm] = useState<EntitlementFormState>(() => ({
    seatLimit: company.seat_limit == null ? "" : String(company.seat_limit),
    prototypeEnabled: company.prototype_enabled,
    usePlatformKey: company.use_platform_key,
    featureFlags: { ...company.feature_flags },
  }))

  const startEdit = () => {
    setForm({
      seatLimit: company.seat_limit == null ? "" : String(company.seat_limit),
      prototypeEnabled: company.prototype_enabled,
      usePlatformKey: company.use_platform_key,
      featureFlags: { ...company.feature_flags },
    })
    setError(null)
    setEditing(true)
  }

  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      const updated = await staffApi.updateCompany(
        company.id,
        formStateToPatch(form),
      )
      onSaved({ ...company, ...updated })
      setEditing(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed")
    } finally {
      setSaving(false)
    }
  }

  return (
    <li className="sadm-org">
      <div className="sadm-org-head">
        <div>
          <div className="sadm-org-name">{company.display_name}</div>
          <div className="sadm-org-meta">
            {company.member_count}
            {company.seat_limit != null ? ` / ${company.seat_limit}` : ""} member
            {company.member_count === 1 ? "" : "s"}
            {company.pending_invite_count > 0 &&
              ` · ${company.pending_invite_count} pending`}
            {" · joined "}
            {formatDate(company.created_at)}
          </div>
          <div className="sadm-org-meta">{enabledModules(company.feature_flags)}</div>
        </div>
        <div className="sadm-org-right">
          <span
            className={`sadm-chip ${company.prototype_enabled ? "on" : "off"}`}
          >
            {company.prototype_enabled ? "Prototype on" : "Prototype off"}
          </span>
          <span className="sadm-chip neutral">{keyModeLabel(company)}</span>
          {!editing && (
            <button type="button" className="sadm-btn" onClick={startEdit}>
              Edit
            </button>
          )}
        </div>
      </div>

      {editing && (
        <div className="sadm-editor">
          <EntitlementFields
            state={form}
            onChange={setForm}
            idPrefix={`org-${company.id}`}
          />
          {error && <p className="sadm-error">{error}</p>}
          <div className="sadm-actions">
            <button
              type="button"
              className="sadm-btn primary"
              disabled={saving}
              onClick={save}
            >
              {saving ? "Saving…" : "Save changes"}
            </button>
            <button
              type="button"
              className="sadm-btn"
              disabled={saving}
              onClick={() => setEditing(false)}
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </li>
  )
}

// ── Invite form + list ──

const EMPTY_INVITE_FORM: EntitlementFormState = {
  seatLimit: "",
  prototypeEnabled: false,
  usePlatformKey: false,
  featureFlags: {
    weekly_brief: true,
    on_demand_analysis: true,
    auto_prd_generation: true,
  },
}

function InviteSection({
  invites,
  onCreated,
  onChanged,
}: {
  invites: OrgInvite[]
  onCreated: (invite: OrgInvite) => void
  onChanged: () => void
}) {
  const [open, setOpen] = useState(false)
  const [email, setEmail] = useState("")
  const [companyName, setCompanyName] = useState("")
  const [form, setForm] = useState<EntitlementFormState>(EMPTY_INVITE_FORM)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const pending = invites.filter((i) => i.status === "pending")
  const settled = invites.filter((i) => i.status !== "pending")

  const submit = async () => {
    setSubmitting(true)
    setError(null)
    setNotice(null)
    try {
      const body: OrgInviteIn = {
        email: email.trim(),
        company_name: companyName.trim(),
        ...formStateToPatch(form),
      }
      const created = await staffApi.createInvite(body)
      onCreated(created)
      setNotice(
        created.email_sent === false
          ? `Invite saved, but the email to ${created.email} could not be sent — use Resend.`
          : `Invite sent to ${created.email}.`,
      )
      setEmail("")
      setCompanyName("")
      setForm(EMPTY_INVITE_FORM)
      setOpen(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create the invite")
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <section className="sadm-section">
      <div className="sadm-section-head">
        <h2>Invitations</h2>
        <button
          type="button"
          className="sadm-btn primary"
          onClick={() => setOpen((v) => !v)}
        >
          {open ? "Close" : "+ Invite organization"}
        </button>
      </div>

      {notice && <p className="sadm-notice">{notice}</p>}

      {open && (
        <div className="sadm-editor">
          <div className="sadm-fields">
            <label className="sadm-field">
              <span className="sadm-field-label">Admin email</span>
              <input
                type="email"
                placeholder="admin@customer.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
              <span className="sadm-field-hint">
                The invite email goes to this person; they become the
                organization&apos;s owner.
              </span>
            </label>
            <label className="sadm-field">
              <span className="sadm-field-label">Organization name</span>
              <input
                type="text"
                placeholder="Acme Corp"
                value={companyName}
                onChange={(e) => setCompanyName(e.target.value)}
              />
            </label>
          </div>
          <EntitlementFields state={form} onChange={setForm} idPrefix="invite" />
          {error && <p className="sadm-error">{error}</p>}
          <div className="sadm-actions">
            <button
              type="button"
              className="sadm-btn primary"
              disabled={submitting || !email.trim() || !companyName.trim()}
              onClick={submit}
            >
              {submitting ? "Sending…" : "Send invite"}
            </button>
          </div>
        </div>
      )}

      <ul className="sadm-list">
        {pending.map((invite) => (
          <li key={invite.id} className="sadm-org">
            <div className="sadm-org-head">
              <div>
                <div className="sadm-org-name">{invite.company_name}</div>
                <div className="sadm-org-meta">
                  {invite.email} · invited {formatDate(invite.created_at)}
                </div>
                <div className="sadm-org-meta">
                  {invite.seat_limit != null
                    ? `${invite.seat_limit} seats`
                    : "Unlimited seats"}
                  {" · "}
                  {invite.prototype_enabled ? "prototype on" : "prototype off"}
                  {" · "}
                  {invite.use_platform_key ? "platform key" : "own key"}
                </div>
              </div>
              <div className="sadm-org-right">
                <span className="sadm-chip neutral">Pending</span>
                <button
                  type="button"
                  className="sadm-btn"
                  onClick={async () => {
                    try {
                      await staffApi.resendInvite(invite.id)
                      setNotice(`Invite re-sent to ${invite.email}.`)
                    } catch (e) {
                      setError(
                        e instanceof Error ? e.message : "Resend failed",
                      )
                    }
                  }}
                >
                  Resend
                </button>
                <button
                  type="button"
                  className="sadm-btn danger"
                  onClick={async () => {
                    try {
                      await staffApi.revokeInvite(invite.id)
                      onChanged()
                    } catch (e) {
                      setError(
                        e instanceof Error ? e.message : "Revoke failed",
                      )
                    }
                  }}
                >
                  Revoke
                </button>
              </div>
            </div>
          </li>
        ))}
        {!pending.length && (
          <li className="sadm-empty">No pending invitations.</li>
        )}
      </ul>

      {settled.length > 0 && (
        <details className="sadm-history">
          <summary>History ({settled.length})</summary>
          <ul className="sadm-list">
            {settled.map((invite) => (
              <li key={invite.id} className="sadm-org">
                <div className="sadm-org-head">
                  <div>
                    <div className="sadm-org-name">{invite.company_name}</div>
                    <div className="sadm-org-meta">{invite.email}</div>
                  </div>
                  <span
                    className={`sadm-chip ${
                      invite.status === "accepted" ? "on" : "off"
                    }`}
                  >
                    {invite.status}
                  </span>
                </div>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  )
}

// ── Standalone login form (dedicated credential — not the app login) ──

function StaffLoginForm({ onSuccess }: { onSuccess: () => void }) {
  const [id, setId] = useState("")
  const [password, setPassword] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async () => {
    setSubmitting(true)
    setError(null)
    try {
      await staffAuth.login(id, password)
      onSuccess()
    } catch (e) {
      // 401 = bad credentials (the backend's message is deliberately
      // generic); 404 = the surface is disabled — same stealth posture.
      if (e instanceof ApiError && e.status === 401) {
        setError("Invalid credentials.")
      } else if (e instanceof ApiError && e.status === 404) {
        setError("Not found.")
      } else {
        setError("Sign-in failed — try again.")
      }
      setSubmitting(false)
    }
  }

  return (
    <form
      className="sadm-login"
      onSubmit={(e) => {
        e.preventDefault()
        void submit()
      }}
    >
      <h1>Sprntly Admin</h1>
      <label className="sadm-field">
        <span className="sadm-field-label">ID</span>
        <input
          type="text"
          autoComplete="username"
          value={id}
          onChange={(e) => setId(e.target.value)}
        />
      </label>
      <label className="sadm-field">
        <span className="sadm-field-label">Password</span>
        <input
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </label>
      {error && <p className="sadm-error">{error}</p>}
      <div className="sadm-actions">
        <button
          type="submit"
          className="sadm-btn primary"
          disabled={submitting || !id.trim() || !password}
        >
          {submitting ? "Signing in…" : "Sign in"}
        </button>
      </div>
    </form>
  )
}

// ── Screen ──

type LoadState = "checking" | "login" | "loading" | "ready" | "error"

export function StaffAdminScreen() {
  const [state, setState] = useState<LoadState>("checking")
  const [companies, setCompanies] = useState<StaffCompany[]>([])
  const [invites, setInvites] = useState<OrgInvite[]>([])

  const load = useCallback(async () => {
    setState("loading")
    try {
      const [c, i] = await Promise.all([
        staffApi.listCompanies(),
        staffApi.listInvites(),
      ])
      setCompanies(c.companies)
      setInvites(i.invites)
      setState("ready")
    } catch (e) {
      // 401/404 = the staff token is missing/expired/rejected (or the surface
      // is disabled): clear it and drop back to the standalone login form.
      // Everything else is a real error.
      if (e instanceof ApiError && [401, 403, 404].includes(e.status)) {
        staffAuth.logout()
        setState("login")
      } else {
        setState("error")
      }
    }
  }, [])

  useEffect(() => {
    // sessionStorage is browser-only — decide login-vs-load after mount so
    // the statically exported page hydrates cleanly.
    if (staffAuth.hasToken()) {
      void load()
    } else {
      setState("login")
    }
  }, [load])

  const signOut = () => {
    staffAuth.logout()
    setCompanies([])
    setInvites([])
    setState("login")
  }

  const totals = useMemo(
    () => ({
      orgs: companies.length,
      pending: invites.filter((i) => i.status === "pending").length,
    }),
    [companies, invites],
  )

  if (state === "checking" || state === "loading") {
    return <div className="sadm-page"><ScopedStyle /><p className="sadm-empty">Loading…</p></div>
  }
  if (state === "login") {
    return (
      <div className="sadm-page">
        <ScopedStyle />
        <StaffLoginForm onSuccess={() => void load()} />
      </div>
    )
  }
  if (state === "error") {
    return (
      <div className="sadm-page">
        <ScopedStyle />
        <p className="sadm-error">
          Could not load the admin panel.{" "}
          <button type="button" className="sadm-btn" onClick={() => void load()}>
            Retry
          </button>
        </p>
      </div>
    )
  }

  return (
    <div className="sadm-page">
      <ScopedStyle />
      <header className="sadm-header">
        <div className="sadm-header-row">
          <h1>Sprntly Admin</h1>
          <button type="button" className="sadm-btn" onClick={signOut}>
            Sign out
          </button>
        </div>
        <p className="sadm-sub">
          {totals.orgs} organization{totals.orgs === 1 ? "" : "s"} ·{" "}
          {totals.pending} pending invite{totals.pending === 1 ? "" : "s"}
        </p>
      </header>

      <InviteSection
        invites={invites}
        onCreated={(invite) => setInvites((prev) => [invite, ...prev])}
        onChanged={() => void load()}
      />

      <section className="sadm-section">
        <div className="sadm-section-head">
          <h2>Organizations</h2>
        </div>
        <ul className="sadm-list">
          {companies.map((c) => (
            <CompanyRow
              key={c.id}
              company={c}
              onSaved={(updated) =>
                setCompanies((prev) =>
                  prev.map((x) => (x.id === updated.id ? updated : x)),
                )
              }
            />
          ))}
          {!companies.length && (
            <li className="sadm-empty">No organizations yet.</li>
          )}
        </ul>
      </section>
    </div>
  )
}

// Scoped styles — this page stands alone (no app shell).
function ScopedStyle() {
  return (
    <style>{`
    .sadm-page { max-width: 880px; margin: 0 auto; padding: 40px 24px 80px;
      font-family: 'Geist', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      color: #111; }
    .sadm-header h1 { font-size: 22px; font-weight: 600; margin: 0; }
    .sadm-header-row { display: flex; align-items: center;
      justify-content: space-between; gap: 16px; }
    .sadm-sub { color: #666; font-size: 13px; margin: 4px 0 0; }
    .sadm-login { max-width: 320px; margin: 96px auto 0; display: flex;
      flex-direction: column; gap: 14px; }
    .sadm-login h1 { font-size: 22px; font-weight: 600; margin: 0 0 6px; }
    .sadm-section { margin-top: 32px; }
    .sadm-section-head { display: flex; align-items: center;
      justify-content: space-between; margin-bottom: 12px; }
    .sadm-section-head h2 { font-size: 15px; font-weight: 600; margin: 0; }
    .sadm-list { list-style: none; margin: 0; padding: 0; display: flex;
      flex-direction: column; gap: 8px; }
    .sadm-org { border: 1px solid #e5e5e5; border-radius: 10px; padding: 14px 16px; }
    .sadm-org-head { display: flex; justify-content: space-between; gap: 16px;
      align-items: flex-start; }
    .sadm-org-name { font-size: 14px; font-weight: 600; }
    .sadm-org-meta { font-size: 12px; color: #666; margin-top: 2px; }
    .sadm-org-right { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
    .sadm-chip { font-size: 11px; padding: 3px 8px; border-radius: 999px;
      white-space: nowrap; }
    .sadm-chip.on { background: #e7f6ec; color: #137a3d; }
    .sadm-chip.off { background: #f3f4f6; color: #888; }
    .sadm-chip.neutral { background: #eef2ff; color: #4353c4; }
    .sadm-btn { font-size: 12px; padding: 5px 12px; border-radius: 7px;
      border: 1px solid #d8d8d8; background: #fff; cursor: pointer; }
    .sadm-btn:hover { background: #f7f7f7; }
    .sadm-btn.primary { background: #111; color: #fff; border-color: #111; }
    .sadm-btn.primary:hover { background: #333; }
    .sadm-btn.danger { color: #b42318; border-color: #f0c8c4; }
    .sadm-btn:disabled { opacity: 0.5; cursor: default; }
    .sadm-editor { border-top: 1px solid #eee; margin-top: 12px; padding-top: 14px; }
    .sadm-fields { display: flex; flex-direction: column; gap: 12px; }
    .sadm-field { display: flex; flex-direction: column; gap: 4px; max-width: 380px; }
    .sadm-field input { font-size: 13px; padding: 7px 10px; border-radius: 7px;
      border: 1px solid #d8d8d8; }
    .sadm-field-label { font-size: 12px; font-weight: 600; }
    .sadm-field-hint { font-size: 11px; color: #888; font-weight: 400; }
    .sadm-check { display: flex; gap: 8px; align-items: baseline; font-size: 13px; }
    .sadm-modules { border: 1px solid #eee; border-radius: 8px;
      padding: 10px 14px 12px; display: flex; flex-direction: column; gap: 6px; }
    .sadm-modules legend { font-size: 12px; font-weight: 600; padding: 0 4px; }
    .sadm-actions { display: flex; gap: 8px; margin-top: 14px; }
    .sadm-error { color: #b42318; font-size: 13px; margin: 10px 0 0; }
    .sadm-notice { color: #137a3d; font-size: 13px; margin: 0 0 10px; }
    .sadm-empty { color: #888; font-size: 13px; padding: 14px 2px; }
    .sadm-history { margin-top: 14px; }
    .sadm-history summary { font-size: 12px; color: #666; cursor: pointer;
      margin-bottom: 8px; }
  `}</style>
  )
}
