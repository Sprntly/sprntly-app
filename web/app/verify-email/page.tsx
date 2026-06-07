"use client"

import { Suspense, useEffect, useState } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { useAuth } from "../lib/auth"
import { AuthShell } from "../components/auth/AuthShell"
import { VerifyEmailView } from "../components/auth/VerifyEmailView"

function VerifyEmailContent() {
  const auth = useAuth()
  const router = useRouter()
  const searchParams = useSearchParams()
  const emailParam = searchParams.get("email") ?? ""
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

  async function onContinue() {
    setMessage(null)
    await auth.refresh()
    if (auth.kind === "authed" && auth.isEmailVerified()) {
      router.replace(await auth.postLoginPath())
    } else {
      setMessage(
        "We haven't seen your verification yet. Click the link in your email, then try again.",
      )
    }
  }

  async function onResend() {
    if (!email || resendCooldown > 0) return
    setMessage(null)
    try {
      await auth.resendVerificationEmail(email)
      setMessage("Verification email sent.")
      setResendCooldown(60)
    } catch {
      setMessage("Couldn't resend right now. Try again shortly.")
    }
  }

  return (
    <VerifyEmailView
      email={email}
      message={message}
      resendCooldown={resendCooldown}
      canResend={!!email && resendCooldown <= 0}
      onContinue={onContinue}
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
