"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import { useWorkspace } from "../../../../context/WorkspaceContext"
import { detectBrowserTimezone, useAuth } from "../../../../lib/auth"
import { fetchUserProfile, updateUserProfile } from "../../../../lib/onboarding/store"
import { ROLE_OPTIONS } from "../../../../lib/onboarding/types"
import { getSupabase } from "../../../../lib/supabase/client"
import { SettingsMessage, SettingsPaneBar } from "./SettingsLayout"

const PROFILE_FORM_ID = "pset-profile-form"

/** "America/Los_Angeles" → "America/Los Angeles (UTC−7)". Offset lookup is
 *  cached per zone — the full IANA list is ~400 entries and this runs once. */
const tzOffsetCache = new Map<string, string | null>()
function tzLabel(tz: string): string {
  let off = tzOffsetCache.get(tz)
  if (off === undefined) {
    try {
      const parts = new Intl.DateTimeFormat("en-US", {
        timeZone: tz,
        timeZoneName: "shortOffset",
      }).formatToParts(new Date())
      const name = parts.find((p) => p.type === "timeZoneName")?.value ?? null
      off = name ? name.replace("GMT", "UTC") : null
    } catch {
      off = null
    }
    tzOffsetCache.set(tz, off)
  }
  const pretty = tz.replace(/_/g, " ")
  return off ? `${pretty} (${off})` : pretty
}

function roleDisplay(role: string, roleOther: string): string {
  if (role === "PM") return "Product Manager"
  if (role === "Other") return roleOther.trim() || "Other"
  return role
}

type ProfileFields = {
  firstName: string
  lastName: string
  role: string
  roleOther: string
  timezone: string
}

/** Editable field values from a profile row (role split into the select value
 *  + the free-text "Other" input; timezone falls back to the browser's). */
function fieldsFromProfile(p: {
  first_name: string | null
  last_name: string | null
  role: string | null
  timezone: string | null
}): ProfileFields {
  const fields: ProfileFields = {
    firstName: p.first_name ?? "",
    lastName: p.last_name ?? "",
    // Seed from the saved zone; if none stored yet, prefill the browser's so
    // the field is never blank and a Save persists a sensible default.
    timezone: p.timezone ?? detectBrowserTimezone() ?? "",
    role: "",
    roleOther: "",
  }
  const r = p.role ?? ""
  if (r && !ROLE_OPTIONS.includes(r as (typeof ROLE_OPTIONS)[number])) {
    fields.role = "Other"
    fields.roleOther = r
  } else {
    fields.role = r
  }
  return fields
}

export function ProfileSettings() {
  const auth = useAuth()
  const {
    workspace,
    profile: ctxProfile,
    loading: workspaceLoading,
    refresh: refreshWorkspace,
  } = useWorkspace()

  // Hydrate INSTANTLY from WorkspaceContext's profile — the app already
  // fetched it once at sign-in (it's what the sidebar name renders from), so
  // opening this pane needs NO network fetch and never flashes
  // "Loading profile…" on a warm session. The fetch-or-insert path (`load`)
  // only runs for brand-new accounts with no profile row yet.
  const seeded = ctxProfile ? fieldsFromProfile(ctxProfile) : null
  // The last loaded/saved values — "Discard" restores these, and any deviation
  // from them is what arms the Save/Discard actions in the top bar. Also the
  // "already hydrated" latch: background context refreshes never re-seed (and
  // so can never clobber in-progress edits).
  const [snapshot, setSnapshot] = useState<ProfileFields | null>(seeded)
  const [loading, setLoading] = useState(seeded == null)
  const [saving, setSaving] = useState(false)
  const [profileSaved, setProfileSaved] = useState(false)
  const [profileError, setProfileError] = useState<string | null>(null)

  const [firstName, setFirstName] = useState(seeded?.firstName ?? "")
  const [lastName, setLastName] = useState(seeded?.lastName ?? "")
  const [role, setRole] = useState(seeded?.role ?? "")
  const [roleOther, setRoleOther] = useState(seeded?.roleOther ?? "")
  const [timezone, setTimezone] = useState(seeded?.timezone ?? "")

  const applyFields = useCallback((loaded: ProfileFields) => {
    setFirstName(loaded.firstName)
    setLastName(loaded.lastName)
    setRole(loaded.role)
    setRoleOther(loaded.roleOther)
    setTimezone(loaded.timezone)
    setSnapshot(loaded)
  }, [])

  const email = auth.kind === "authed" ? auth.user.email ?? "" : ""
  const joinedAt = auth.kind === "authed" ? auth.user.created_at : null

  const dirty =
    snapshot != null &&
    (firstName !== snapshot.firstName ||
      lastName !== snapshot.lastName ||
      role !== snapshot.role ||
      roleOther !== snapshot.roleOther ||
      timezone !== snapshot.timezone)

  // Full IANA zone list from the browser (modern engines). The current saved
  // value is always included even if the engine omits it, so we never silently
  // drop a stored zone.
  const tzOptions = useMemo(() => {
    let zones: string[] = []
    try {
      zones =
        (Intl as { supportedValuesOf?: (k: string) => string[] }).supportedValuesOf?.(
          "timeZone",
        ) ?? []
    } catch {
      zones = []
    }
    if (timezone && !zones.includes(timezone)) zones = [timezone, ...zones]
    return zones
  }, [timezone])

  const load = useCallback(async () => {
    if (auth.kind !== "authed") return
    setLoading(true)
    setProfileError(null)
    try {
      let p = await fetchUserProfile(auth.user.id)
      if (!p) {
        const supabase = getSupabase()
        const meta = auth.user.user_metadata ?? {}
        const first = String(meta.first_name ?? "").trim()
        const last = String(meta.last_name ?? "").trim()
        const metaTz = String(meta.timezone ?? "").trim() || null
        const { data, error } = await supabase
          .from("profiles")
          .insert({
            id: auth.user.id,
            email: auth.user.email,
            first_name: first,
            last_name: last,
            full_name: [first, last].filter(Boolean).join(" ") || null,
            timezone: metaTz,
          })
          .select(
            "id, email, first_name, last_name, role, timezone, account_type, onboarding_step, onboarding_completed_at, skipped_fields",
          )
          .single()
        if (!error && data) {
          p = {
            id: data.id,
            email: data.email,
            first_name: data.first_name,
            last_name: data.last_name,
            role: data.role,
            timezone: data.timezone ?? null,
            account_type:
              data.account_type === "company" || data.account_type === "personal"
                ? data.account_type
                : null,
            onboarding_step: data.onboarding_step ?? 0,
            onboarding_completed_at: data.onboarding_completed_at,
            skipped_fields: Array.isArray(data.skipped_fields) ? data.skipped_fields : [],
          }
        }
      }
      if (p) {
        applyFields(fieldsFromProfile(p))
        // Sync the context so the sidebar name + later visits pick the row up
        // without re-running this path.
        void refreshWorkspace()
      }
    } catch (e) {
      setProfileError(e instanceof Error ? e.message : "Could not load profile")
    } finally {
      setLoading(false)
    }
  }, [auth, applyFields, refreshWorkspace])

  // Late hydration — only when the mount-time seed found nothing: either the
  // context's initial fetch is still in flight (seed when it lands) or the
  // account truly has no profile row yet (fetch-or-insert via load()). The
  // `snapshot != null` latch keeps this from ever re-seeding over edits.
  useEffect(() => {
    if (auth.kind !== "authed" || snapshot != null) return
    if (ctxProfile) {
      applyFields(fieldsFromProfile(ctxProfile))
      setLoading(false)
      return
    }
    if (workspaceLoading) return
    void load()
  }, [auth.kind, snapshot, ctxProfile, workspaceLoading, applyFields, load])

  function onDiscard() {
    if (!snapshot) return
    setFirstName(snapshot.firstName)
    setLastName(snapshot.lastName)
    setRole(snapshot.role)
    setRoleOther(snapshot.roleOther)
    setTimezone(snapshot.timezone)
    setProfileError(null)
  }

  async function onSaveProfile(e: React.FormEvent) {
    e.preventDefault()
    if (auth.kind !== "authed") return
    if (!firstName.trim() || !lastName.trim()) {
      setProfileError("First and last name are required.")
      return
    }
    setSaving(true)
    setProfileError(null)
    setProfileSaved(false)
    try {
      const resolvedRole =
        role === "Other" ? roleOther.trim() || null : role.trim() || null
      await updateUserProfile(auth.user.id, {
        first_name: firstName,
        last_name: lastName,
        role: resolvedRole,
        timezone: timezone.trim() || null,
      })
      setSnapshot({ firstName, lastName, role, roleOther, timezone })
      setProfileSaved(true)
      await refreshWorkspace()
    } catch (e) {
      setProfileError(e instanceof Error ? e.message : "Could not save profile")
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <div className="pset">
        <div className="pset-body">
          <p className="settings-loading">Loading profile…</p>
        </div>
      </div>
    )
  }

  const fullName = [firstName.trim(), lastName.trim()].filter(Boolean).join(" ")
  const initials =
    ((firstName.trim()[0] ?? "") + (lastName.trim()[0] ?? "")).toUpperCase() ||
    (email[0] ?? "?").toUpperCase()
  const joinedLabel = (() => {
    if (!joinedAt) return null
    const d = new Date(joinedAt)
    if (Number.isNaN(d.getTime())) return null
    return `joined ${d.toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" })}`
  })()
  const identityMeta = [
    role ? roleDisplay(role, roleOther) : null,
    workspace?.display_name ?? null,
    joinedLabel,
  ]
    .filter(Boolean)
    .join(" · ")

  return (
    <div className="pset">
      {/* Sticky action bar — save/discard live here, not at the card's foot. */}
      <SettingsPaneBar
        title="Profile"
        meta={[fullName, email].filter(Boolean).join(" · ") || null}
        saved={profileSaved}
        dirty={dirty}
        saving={saving}
        onDiscard={onDiscard}
        formId={PROFILE_FORM_ID}
      />

      <div className="pset-body">
      <h2 className="pset-title">Profile</h2>
      <p className="pset-sub">
        How Sprntly addresses you and tunes Briefs to your role. Visible only
        inside your workspace.
      </p>

      <form id={PROFILE_FORM_ID} className="pset-card" onSubmit={onSaveProfile}>
        <div className="pset-identity">
          <div className="pset-avatar" aria-hidden>{initials}</div>
          <div className="pset-identity-text">
            <div className="pset-name">{fullName || "Your name"}</div>
            {identityMeta && <div className="pset-identity-meta">{identityMeta}</div>}
          </div>
          <button
            type="button"
            className="pset-avatar-btn"
            disabled
            title="Avatar upload is coming soon"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z" />
              <circle cx="12" cy="13" r="4" />
            </svg>
            Change avatar
          </button>
        </div>

        <div className="pset-grid">
          <div className="pset-field">
            <label className="pset-label" htmlFor="pset-first-name">First name</label>
            <input
              id="pset-first-name"
              className="input"
              value={firstName}
              onChange={(e) => setFirstName(e.target.value)}
              maxLength={50}
              required
            />
          </div>
          <div className="pset-field">
            <label className="pset-label" htmlFor="pset-last-name">Last name</label>
            <input
              id="pset-last-name"
              className="input"
              value={lastName}
              onChange={(e) => setLastName(e.target.value)}
              maxLength={50}
              required
            />
          </div>
          <div className="pset-field">
            <label className="pset-label" htmlFor="pset-email">Work email</label>
            <input
              id="pset-email"
              className="input"
              value={email || "—"}
              readOnly
              title="Contact support to change your login email."
            />
          </div>
          <div className="pset-field">
            <label className="pset-label" htmlFor="pset-account-type">Account type</label>
            <input
              id="pset-account-type"
              className="input"
              value={
                ctxProfile?.account_type === "personal"
                  ? "Personal"
                  : ctxProfile?.account_type === "company"
                    ? "Company"
                    : "—"
              }
              readOnly
              title="Chosen at sign-up. Contact support to change it."
            />
          </div>
          <div className="pset-field">
            <label className="pset-label" htmlFor="pset-timezone">Timezone</label>
            {tzOptions.length > 0 ? (
              <select
                id="pset-timezone"
                className="input"
                value={timezone}
                onChange={(e) => setTimezone(e.target.value)}
              >
                <option value="">Select a timezone</option>
                {tzOptions.map((tz) => (
                  <option key={tz} value={tz}>
                    {tzLabel(tz)}
                  </option>
                ))}
              </select>
            ) : (
              <input
                id="pset-timezone"
                className="input"
                value={timezone}
                onChange={(e) => setTimezone(e.target.value)}
                placeholder="e.g. America/New_York"
                maxLength={64}
              />
            )}
          </div>
          <div className="pset-field pset-field--full">
            <label className="pset-label" htmlFor="pset-role">Your role</label>
            <select
              id="pset-role"
              className="input"
              value={role}
              onChange={(e) => setRole(e.target.value)}
            >
              <option value="">Select a role</option>
              {ROLE_OPTIONS.map((r) => (
                <option key={r} value={r}>
                  {r === "PM" ? "Product Manager" : r}
                </option>
              ))}
            </select>
            {role === "Other" && (
              <input
                className="input"
                style={{ marginTop: 8 }}
                value={roleOther}
                onChange={(e) => setRoleOther(e.target.value)}
                placeholder="Your role"
                maxLength={50}
              />
            )}
          </div>
        </div>

        {profileError && <SettingsMessage kind="error">{profileError}</SettingsMessage>}
      </form>
      </div>
    </div>
  )
}
