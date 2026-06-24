"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import { useWorkspace } from "../../../../context/WorkspaceContext"
import { detectBrowserTimezone, useAuth } from "../../../../lib/auth"
import { fetchUserProfile, updateUserProfile } from "../../../../lib/onboarding/store"
import { ROLE_OPTIONS } from "../../../../lib/onboarding/types"
import { getSupabase } from "../../../../lib/supabase/client"
import {
  SettingsRow,
  SettingsSection,
  SettingsMessage,
} from "./SettingsLayout"

export function ProfileSettings() {
  const auth = useAuth()
  const { refresh: refreshWorkspace } = useWorkspace()
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [profileSaved, setProfileSaved] = useState(false)
  const [profileError, setProfileError] = useState<string | null>(null)

  const [firstName, setFirstName] = useState("")
  const [lastName, setLastName] = useState("")
  const [role, setRole] = useState("")
  const [roleOther, setRoleOther] = useState("")
  const [timezone, setTimezone] = useState("")

  const email = auth.kind === "authed" ? auth.user.email ?? "" : ""

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
        setFirstName(p.first_name ?? "")
        setLastName(p.last_name ?? "")
        // Seed from the saved zone; if none stored yet, prefill the browser's so
        // the field is never blank and a Save persists a sensible default.
        setTimezone(p.timezone ?? detectBrowserTimezone() ?? "")
        const r = p.role ?? ""
        if (r && !ROLE_OPTIONS.includes(r as (typeof ROLE_OPTIONS)[number])) {
          setRole("Other")
          setRoleOther(r)
        } else {
          setRole(r)
          setRoleOther("")
        }
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
      setProfileSaved(true)
      await refreshWorkspace()
    } catch (e) {
      setProfileError(e instanceof Error ? e.message : "Could not save profile")
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return <p className="settings-loading">Loading profile…</p>
  }

  return (
    <>
      <SettingsSection
        title="Profile"
        sub="Your name and role appear across Sprntly and in team views."
      >
        <form onSubmit={onSaveProfile}>
          <SettingsRow label="Work email" sub="Contact support to change your login email.">
            <span className="settings-readonly">{email || "—"}</span>
          </SettingsRow>
          <div className="settings-field-row">
            <div className="field">
              <label className="field-label">First name</label>
              <input
                className="input"
                value={firstName}
                onChange={(e) => setFirstName(e.target.value)}
                maxLength={50}
                required
              />
            </div>
            <div className="field">
              <label className="field-label">Last name</label>
              <input
                className="input"
                value={lastName}
                onChange={(e) => setLastName(e.target.value)}
                maxLength={50}
                required
              />
            </div>
          </div>
          <div className="field" style={{ marginBottom: 14 }}>
            <label className="field-label">Your role</label>
            <select
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
          <div className="field" style={{ marginBottom: 14 }}>
            <label className="field-label">Timezone</label>
            {tzOptions.length > 0 ? (
              <select
                className="input"
                value={timezone}
                onChange={(e) => setTimezone(e.target.value)}
              >
                <option value="">Select a timezone</option>
                {tzOptions.map((tz) => (
                  <option key={tz} value={tz}>
                    {tz.replace(/_/g, " ")}
                  </option>
                ))}
              </select>
            ) : (
              <input
                className="input"
                value={timezone}
                onChange={(e) => setTimezone(e.target.value)}
                placeholder="e.g. America/New_York"
                maxLength={64}
              />
            )}
            <p className="field-hint">
              Your weekly brief is delivered Monday 6:00 AM in this timezone.
            </p>
          </div>
          {profileError && <SettingsMessage kind="error">{profileError}</SettingsMessage>}
          {profileSaved && (
            <SettingsMessage kind="success">Profile saved.</SettingsMessage>
          )}
          <button type="submit" className="btn btn-primary" disabled={saving}>
            {saving ? "Saving…" : "Save profile"}
          </button>
        </form>
      </SettingsSection>

      <SettingsSection
        title="Notifications"
        sub="Email digest and in-app alerts — full controls coming soon."
      >
        <p className="settings-placeholder">
          Configure notification preferences in a future update. Brief delivery
          settings remain under workspace notifications.
        </p>
      </SettingsSection>
    </>
  )
}
