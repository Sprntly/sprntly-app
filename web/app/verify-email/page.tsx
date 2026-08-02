"use client"

import { Suspense, useEffect, useState } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { AuthApiError } from "@supabase/supabase-js"
import { useAuth } from "../lib/auth"
import { AuthShell } from "../components/auth/AuthShell"
import { VerifyEmailView } from "../components/auth/VerifyEmailView"

/**
 * Signup confirmation via a typed 6-digit code.
 *
 * The confirmation email carries {{ .Token }} (Supabase mints 6 digits per
 * supabase/config.toml otp_length/otp_expiry) instead of a link, so the
 * session is minted right here by verifyOtp — no /auth/confirm hop, and
 * nothing for a mail scanner to prefetch and burn.
 */
function VerifyEmailContent() {
  const auth = useAuth()
  const router = useRouter()
  const searchParams = useSearchParams()
  const emailParam = searchParams.get("email") ?? ""
  const [code, setCode] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [resendCooldown, setResendCooldown] = useState(0)
  const [message, setMessage] = useState<string | null>(null)

  const email = emailParam || (auth.kind === "authed" ? auth.user.email ?? "" : "")

  useEffect(() => {
    if (auth.kind === "authed" && auth.isEmailVerified()) {
      void auth.postLoginPath().then((path) => router.replace(path))
    }
  }, [auth, router])

  useEffect(() => {
    if (resendCooldown <= 0) return
    const id = setInterval(() => setResendCooldown((s) => Math.max(0, s - 1)), 1000)
    return () => clearInterval(id)
  }, [resendCooldown])

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (submitting || code.length < 6) return
    setError(null)
    setMessage(null)
    if (!email) {
      setError("We don't know which address to verify. Sign in to start again.")
      return
    }
    setSubmitting(true)
    try {
      await auth.verifyEmailOtp(email, code)
      // verifyOtp persists the session; postLoginPath reads the fresh user.
      router.replace(await auth.postLoginPath())
      // Stay submitting — navigation unmounts this page.
    } catch (err) {
      setCode("")
      setError(
        err instanceof AuthApiError && /expired/i.test(err.message)
          ? "That code has expired. Request a new one below."
          : "That code isn't right. Check the email and try again.",
      )
      setSubmitting(false)
    }
  }

  async function onResend() {
    if (!email || resendCooldown > 0) return
    setError(null)
    setMessage(null)
    try {
      await auth.resendVerificationEmail(email)
      setCode("")
      setMessage("New code sent.")
      setResendCooldown(60)
    } catch {
      setError("Couldn't resend right now. Try again shortly.")
    }
  }

  return (
    <VerifyEmailView
      email={email}
      code={code}
      message={message}
      error={error}
      submitting={submitting}
      resendCooldown={resendCooldown}
      canResend={!!email && resendCooldown <= 0 && !submitting}
      onCodeChange={setCode}
      onSubmit={onSubmit}
      onResend={onResend}
    />
  )
}

export default function VerifyEmailPage() {
  return (
    <Suspense
      fallback={
        <AuthShell tag="Verify email" cardClassName="auth-card-center">
          <div className="auth-sub">Loading…</div>
        </AuthShell>
      }
    >
      <VerifyEmailContent />
    </Suspense>
  )
}
