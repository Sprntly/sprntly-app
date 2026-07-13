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

export function ProfileSettings() {
  const auth = useAuth()
  const { workspace, refresh: refreshWorkspace } = useWorkspace()
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [profileSaved, setProfileSaved] = useState(false)
  const [profileError, setProfileError] = useState<string | null>(null)

  const [firstName, setFirstName] = useState("")
  const [lastName, setLastName] = useState("")
  const [role, setRole] = useState("")
  const [roleOther, setRoleOther] = useState("")
  const [timezone, setTimezone] = useState("")
  // The last loaded/saved values — "Discard" restores these, and any deviation
  // from them is what arms the Save/Discard actions in the top bar.
  const [snapshot, setSnapshot] = useState<ProfileFields | null>(null)

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
            "id, email, first_name, last_name, role, timezone, onboarding_step, onboarding_completed_at, skipped_fields",
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
            onboarding_step: data.onboarding_step ?? 0,
            onboarding_completed_at: data.onboarding_completed_at,
            skipped_fields: Array.isArray(data.skipped_fields) ? data.skipped_fields : [],
          }
        }
      }
      if (p) {
        const loaded: ProfileFields = {
          firstName: p.first_name ?? "",
          lastName: p.last_name ?? "",
          // Seed from the saved zone; if none stored yet, prefill the browser's
          // so the field is never blank and a Save persists a sensible default.
          timezone: p.timezone ?? detectBrowserTimezone() ?? "",
          role: "",
          roleOther: "",
        }
        const r = p.role ?? ""
        if (r && !ROLE_OPTIONS.includes(r as (typeof ROLE_OPTIONS)[number])) {
          loaded.role = "Other"
          loaded.roleOther = r
        } else {
          loaded.role = r
        }
        setFirstName(loaded.firstName)
        setLastName(loaded.lastName)
        setTimezone(loaded.timezone)
        setRole(loaded.role)
        setRoleOther(loaded.roleOther)
        setSnapshot(loaded)
      }
    } catch (e) {
      setProfileError(e instanceof Error ? e.message : "Could not load profile")
    } finally {
      setLoading(false)
    }
  }, [auth])

  useEffect(() => {
    void load()
  }, [load])

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
