"use client"

import { useCallback, useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { AuthApiError } from "@supabase/supabase-js"
import { getSupabase, isSupabaseConfigured } from "../lib/supabase/client"
import { validatePassword } from "../lib/auth-validation"
import {
  ResetPasswordView,
  type ResetPasswordMode,
} from "../components/auth/ResetPasswordView"

/**
 * Reset-password landing page.
 *
 * The flow:
 *   1. User clicks the recovery link in the email
 *   2. /auth/callback exchanges the code for a session (signs them in
 *      with a transient recovery session) and detects type=recovery
 *      → routes here
 *   3. User picks a new password → supabase.auth.updateUser({ password })
 *   4. Success → router.replace("/")
 *
 * If the page is opened without an active session (link expired, direct
 * navigation, etc.) we render the "no-session" state with a back-to-
 * sign-in link instead of failing silently.
 */
export default function ResetPasswordPage() {
  const router = useRouter()
  const [mode, setMode] = useState<ResetPasswordMode>("form")
  const [newPassword, setNewPassword] = useState("")
  const [confirmPassword, setConfirmPassword] = useState("")
  const [showPassword, setShowPassword] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [checkedSession, setCheckedSession] = useState(false)

  // Verify we actually have a session to act on. Recovery links produce
  // a session via /auth/callback's exchangeCodeForSession; without one,
  // updateUser will just return an auth error and the user will be
  // confused. Render the no-session state instead.
  useEffect(() => {
    if (!isSupabaseConfigured()) {
      setMode("no-session")
      setCheckedSession(true)
      return
    }
    const supabase = getSupabase()
    void supabase.auth.getSession().then(({ data: { session } }) => {
      if (!session) setMode("no-session")
      setCheckedSession(true)
    })
  }, [])

  const onSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault()
      setError(null)
      const pwErr = validatePassword(newPassword)
      if (pwErr) {
        setError(pwErr)
        return
      }
      if (newPassword !== confirmPassword) {
        setError("Passwords do not match.")
        return
      }
      setSubmitting(true)
      try {
        const supabase = getSupabase()
        const { error: updateErr } = await supabase.auth.updateUser({
          password: newPassword,
        })
        if (updateErr) throw updateErr
        setMode("done")
        // Soft auto-bounce to home after a beat so confirmation is visible.
        setTimeout(() => router.replace("/"), 1500)
      } catch (e) {
        if (e instanceof AuthApiError) {
          setError(e.message)
        } else {
          setError("Could not update password. Try again.")
        }
      } finally {
        setSubmitting(false)
      }
    },
    [newPassword, confirmPassword, router],
  )

  if (!checkedSession) {
    return <div className="auth-shell">Loading…</div>
  }

  return (
    <ResetPasswordView
      mode={mode}
      newPassword={newPassword}
      confirmPassword={confirmPassword}
      showPassword={showPassword}
      submitting={submitting}
      error={error}
      onNewPasswordChange={setNewPassword}
      onConfirmPasswordChange={setConfirmPassword}
      onToggleShowPassword={() => setShowPassword((s) => !s)}
      onSubmit={onSubmit}
    />
  )
}
