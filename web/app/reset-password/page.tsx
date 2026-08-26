"use client"

import { Suspense, useCallback, useEffect, useState } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { AuthApiError } from "@supabase/supabase-js"
import {
  getSupabase,
  isSupabaseConfigured,
  postLoginPath,
} from "../lib/supabase/client"
import { useAuth } from "../lib/auth"
import { validatePassword } from "../lib/auth-validation"
import {
  ResetPasswordView,
  type ResetPasswordMode,
} from "../components/auth/ResetPasswordView"

/**
 * Reset-password landing page.
 *
 * The flow:
 *   1. /sign-in "forgot password" sends the recovery email and routes here
 *      with ?email=…
 *   2. The email carries a 6-digit code (supabase/templates/recovery.html),
 *      entered here → verifyOtp({type:"recovery"}) mints the recovery session
 *   3. User picks a new password → supabase.auth.updateUser({ password })
 *   4. Success → straight into the app, at `postLoginPath()`

 *
 * STEP 4 ASKS FOR NOTHING. The recovery OTP already minted a session, and
 * updateUser returns one — the person IS signed in by the time the new
 * password is saved, so a screen telling them so and offering a button is an
 * interstitial in front of a door that is already open. It used to sit there
 * for a second and a half before bouncing to "/". Now it renders only for as
 * long as the redirect takes, and keeps its link purely as a way out if the
 * redirect never lands.
 *
 * `postLoginPath()` rather than "/", for the same reason /set-password uses
 * it: someone resetting a password may still be mid-onboarding or carrying an
 * unaccepted invite, and "/" is only correct for the finished case.
 *
 * Arriving with a session already in hand skips straight to step 3 — that's
 * the /auth/confirm?type=recovery path, still live for recovery links sent
 * before the switch to codes. Landing with neither a session nor an ?email to
 * verify against renders the no-session state rather than failing silently.
 */
function ResetPasswordContent() {
  const auth = useAuth()
  const router = useRouter()
  const searchParams = useSearchParams()
  const emailParam = searchParams.get("email") ?? ""

  const [mode, setMode] = useState<ResetPasswordMode>("form")
  const [code, setCode] = useState("")
  const [message, setMessage] = useState<string | null>(null)
  const [resendCooldown, setResendCooldown] = useState(0)
  const [newPassword, setNewPassword] = useState("")
  const [confirmPassword, setConfirmPassword] = useState("")
  const [showPassword, setShowPassword] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [checkedSession, setCheckedSession] = useState(false)

  // Decide the entry mode once: an existing session goes straight to the
  // password form, otherwise we need the emailed code (and an address to
  // verify it against).
  useEffect(() => {
    if (!isSupabaseConfigured()) {
      setMode("no-session")
      setCheckedSession(true)
      return
    }
    const supabase = getSupabase()
    void supabase.auth.getSession().then(({ data: { session } }) => {
      if (!session) setMode(emailParam ? "code" : "no-session")
      setCheckedSession(true)
    })
  }, [emailParam])

  useEffect(() => {
    if (resendCooldown <= 0) return
    const id = setInterval(() => setResendCooldown((s) => Math.max(0, s - 1)), 1000)
    return () => clearInterval(id)
  }, [resendCooldown])

  const onCodeSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault()
      if (submitting || code.length < 6) return
      setError(null)
      setMessage(null)
      setSubmitting(true)
      try {
        await auth.verifyPasswordResetOtp(emailParam, code)
        setMode("form")
      } catch (err) {
        setCode("")
        setError(
          err instanceof AuthApiError && /expired/i.test(err.message)
            ? "That code has expired. Request a new one below."
            : "That code isn't right. Check the email and try again.",
        )
      } finally {
        setSubmitting(false)
      }
    },
    [auth, code, emailParam, submitting],
  )

  const onResend = useCallback(async () => {
    if (!emailParam || resendCooldown > 0) return
    setError(null)
    setMessage(null)
    try {
      await auth.resetPassword(emailParam)
    } catch {
      // Swallow: never reveal whether the address is registered.
    }
    setCode("")
    setMessage("New code sent.")
    setResendCooldown(60)
  }, [auth, emailParam, resendCooldown])

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
        // Shown while the redirect resolves — `postLoginPath` reads the user
        // and their workspace, so it is not instant.
        setMode("done")
        router.replace(await postLoginPath())
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
      email={emailParam}
      code={code}
      message={message}
      resendCooldown={resendCooldown}
      canResend={!!emailParam && resendCooldown <= 0 && !submitting}
      onCodeChange={setCode}
      onCodeSubmit={onCodeSubmit}
      onResend={onResend}
    />
  )
}

export default function ResetPasswordPage() {
  return (
    <Suspense fallback={<div className="auth-shell">Loading…</div>}>
      <ResetPasswordContent />
    </Suspense>
  )
}
