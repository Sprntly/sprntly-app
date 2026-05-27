"use client"

import { useCallback, useEffect, useState } from "react"
import { AuthApiError } from "@supabase/supabase-js"
import { useAuth } from "../../../../lib/auth"
import { validatePassword } from "../../../../lib/auth-validation"
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
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [profileSaved, setProfileSaved] = useState(false)
  const [profileError, setProfileError] = useState<string | null>(null)

  const [firstName, setFirstName] = useState("")
  const [lastName, setLastName] = useState("")
  const [role, setRole] = useState("")
  const [roleOther, setRoleOther] = useState("")

  const [newPassword, setNewPassword] = useState("")
  const [confirmPassword, setConfirmPassword] = useState("")
  const [passwordSaving, setPasswordSaving] = useState(false)
  const [passwordMessage, setPasswordMessage] = useState<string | null>(null)
  const [passwordError, setPasswordError] = useState<string | null>(null)

  const email = auth.kind === "authed" ? auth.user.email ?? "" : ""

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
        const { data, error } = await supabase
          .from("profiles")
          .insert({
            id: auth.user.id,
            email: auth.user.email,
            first_name: first,
            last_name: last,
            full_name: [first, last].filter(Boolean).join(" ") || null,
          })
          .select(
            "id, email, first_name, last_name, role, onboarding_step, onboarding_completed_at, skipped_fields",
          )
          .single()
        if (!error && data) {
          p = {
            id: data.id,
            email: data.email,
            first_name: data.first_name,
            last_name: data.last_name,
            role: data.role,
            onboarding_step: data.onboarding_step ?? 0,
            onboarding_completed_at: data.onboarding_completed_at,
            skipped_fields: Array.isArray(data.skipped_fields) ? data.skipped_fields : [],
          }
        }
      }
      if (p) {
        setFirstName(p.first_name ?? "")
        setLastName(p.last_name ?? "")
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
      })
      setProfileSaved(true)
    } catch (e) {
      setProfileError(e instanceof Error ? e.message : "Could not save profile")
    } finally {
      setSaving(false)
    }
  }

  async function onChangePassword(e: React.FormEvent) {
    e.preventDefault()
    setPasswordError(null)
    setPasswordMessage(null)
    const pwErr = validatePassword(newPassword)
    if (pwErr) {
      setPasswordError(pwErr)
      return
    }
    if (newPassword !== confirmPassword) {
      setPasswordError("Passwords do not match.")
      return
    }
    setPasswordSaving(true)
    try {
      const supabase = getSupabase()
      const { error } = await supabase.auth.updateUser({ password: newPassword })
      if (error) throw error
      setPasswordMessage("Password updated successfully.")
      setNewPassword("")
      setConfirmPassword("")
    } catch (e) {
      if (e instanceof AuthApiError) {
        setPasswordError(e.message)
      } else {
        setPasswordError("Could not update password.")
      }
    } finally {
      setPasswordSaving(false)
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
          {profileError && <SettingsMessage kind="error">{profileError}</SettingsMessage>}
          {profileSaved && (
            <SettingsMessage kind="success">Profile saved.</SettingsMessage>
          )}
          <button type="submit" className="btn btn-primary" disabled={saving}>
            {saving ? "Saving…" : "Save profile"}
          </button>
        </form>
      </SettingsSection>

      <SettingsSection title="Password" sub="Change the password you use to sign in.">
        <form onSubmit={onChangePassword}>
          <div className="field">
            <label className="field-label">New password</label>
            <input
              type="password"
              className="input"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              autoComplete="new-password"
            />
          </div>
          <div className="field">
            <label className="field-label">Confirm new password</label>
            <input
              type="password"
              className="input"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              autoComplete="new-password"
            />
          </div>
          {passwordError && <SettingsMessage kind="error">{passwordError}</SettingsMessage>}
          {passwordMessage && (
            <SettingsMessage kind="success">{passwordMessage}</SettingsMessage>
          )}
          <button
            type="submit"
            className="btn btn-primary"
            disabled={passwordSaving || !newPassword}
          >
            {passwordSaving ? "Updating…" : "Update password"}
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
