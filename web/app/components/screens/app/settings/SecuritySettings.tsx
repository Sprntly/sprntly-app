"use client"

import { useCallback, useState } from "react"
import { AuthApiError } from "@supabase/supabase-js"
import { validatePassword } from "../../../../lib/auth-validation"
import { getSupabase } from "../../../../lib/supabase/client"
import {
  SettingsSection,
  SettingsMessage,
} from "./SettingsLayout"

/**
 * Security pane.
 *
 * Hosts the proactive Change-password form. MFA, active sessions, SSO,
 * and audit log are placeholders for follow-on slices.
 *
 * The View is pure (props in, JSX out) — unit-tested via
 * renderToStaticMarkup. The default-exported SecuritySettings wraps
 * the View with Supabase auth wiring.
 */
export type SecuritySettingsViewProps = {
  newPassword: string
  confirmPassword: string
  saving: boolean
  error: string | null
  message: string | null
  onNewPasswordChange: (v: string) => void
  onConfirmPasswordChange: (v: string) => void
  onSubmit: (e: React.FormEvent) => void
}

export function SecuritySettingsView({
  newPassword,
  confirmPassword,
  saving,
  error,
  message,
  onNewPasswordChange,
  onConfirmPasswordChange,
  onSubmit,
}: SecuritySettingsViewProps) {
  const canSubmit = newPassword.length > 0 && !saving
  return (
    <>
      <SettingsSection
        title="Change password"
        sub="Set a new password for your Sprntly sign-in."
      >
        <form onSubmit={onSubmit}>
          <div className="field">
            <label className="field-label">New password</label>
            <input
              type="password"
              className="input"
              value={newPassword}
              onChange={(e) => onNewPasswordChange(e.target.value)}
              autoComplete="new-password"
              placeholder="At least 8 characters"
            />
          </div>
          <div className="field">
            <label className="field-label">Confirm new password</label>
            <input
              type="password"
              className="input"
              value={confirmPassword}
              onChange={(e) => onConfirmPasswordChange(e.target.value)}
              autoComplete="new-password"
              placeholder="Repeat the new password"
            />
          </div>
          {error && <SettingsMessage kind="error">{error}</SettingsMessage>}
          {message && <SettingsMessage kind="success">{message}</SettingsMessage>}
          <button
            type="submit"
            className="btn btn-primary"
            disabled={!canSubmit}
          >
            {saving ? "Updating…" : "Update password"}
          </button>
        </form>
      </SettingsSection>

      <SettingsSection
        title="More security controls"
        sub="MFA, active sessions, and SSO are coming in a follow-on slice."
      >
        <p className="settings-placeholder">
          Multi-factor authentication, active-session management, and SSO
          configuration aren&apos;t available yet in this build.
        </p>
      </SettingsSection>
    </>
  )
}

export function SecuritySettings() {
  const [newPassword, setNewPassword] = useState("")
  const [confirmPassword, setConfirmPassword] = useState("")
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)

  const onSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault()
      setError(null)
      setMessage(null)
      const pwErr = validatePassword(newPassword)
      if (pwErr) {
        setError(pwErr)
        return
      }
      if (newPassword !== confirmPassword) {
        setError("Passwords do not match.")
        return
      }
      setSaving(true)
      try {
        const supabase = getSupabase()
        const { error: updateErr } = await supabase.auth.updateUser({
          password: newPassword,
        })
        if (updateErr) throw updateErr
        setMessage("Password updated successfully.")
        setNewPassword("")
        setConfirmPassword("")
      } catch (e) {
        if (e instanceof AuthApiError) {
          setError(e.message)
        } else {
          setError("Could not update password.")
        }
      } finally {
        setSaving(false)
      }
    },
    [newPassword, confirmPassword],
  )

  return (
    <SecuritySettingsView
      newPassword={newPassword}
      confirmPassword={confirmPassword}
      saving={saving}
      error={error}
      message={message}
      onNewPasswordChange={setNewPassword}
      onConfirmPasswordChange={setConfirmPassword}
      onSubmit={onSubmit}
    />
  )
}
