"use client"

import { Suspense, useEffect, useState } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import Link from "next/link"
import { useAuth } from "../lib/auth"

function VerifyEmailContent() {
  const auth = useAuth()
  const router = useRouter()
  const searchParams = useSearchParams()
  const emailParam = searchParams.get("email") ?? ""
  const [resendCooldown, setResendCooldown] = useState(0)
  const [message, setMessage] = useState<string | null>(null)

  const email =
    emailParam ||
    (auth.kind === "authed" ? auth.user.email ?? "" : "")

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
    <div className="ob-shell">
      <div className="auth-card">
        <div className="ob-brand-mark">spr<span>ntly</span></div>
        <div className="ob-eyebrow">Almost there</div>
        <h1 className="ob-title">Verify your email</h1>
        <p className="ob-desc">
          We sent a verification link to <strong>{email || "your email"}</strong>.
          Click the link to start the Business Context Interview.
        </p>
        <p className="hint">Check your spam folder if you don&apos;t see it within a few minutes.</p>
        {message && <div className="msg">{message}</div>}
        <button
          type="button"
          className="btn btn-primary btn-block btn-lg"
          onClick={onResend}
          disabled={resendCooldown > 0 || !email}
        >
          {resendCooldown > 0 ? `Resend in ${resendCooldown}s` : "Resend email"}
        </button>
        <p className="auth-switch">
          Wrong address? <Link href="/sign-up">Create a new account</Link>
          {" · "}
          <Link href="/sign-in">Sign in</Link>
        </p>
      </div>
      <style jsx>{`
        .auth-card { width: 100%; max-width: 480px; }
        .ob-brand-mark { font-family: var(--font-display); font-weight: 600; font-size: 22px; text-align: center; margin-bottom: 48px; }
        .ob-brand-mark :global(span) { color: var(--accent); }
        .hint { font-size: 13px; color: var(--muted); margin: 0 0 20px; }
        .msg { font-size: 13px; color: var(--accent); margin-bottom: 12px; }
        .auth-switch { text-align: center; font-size: 13px; color: var(--ink-3); margin-top: 18px; }
        .auth-switch :global(a) { color: var(--ink); font-weight: 600; }
      `}</style>
    </div>
  )
}

export default function VerifyEmailPage() {
  return (
    <Suspense fallback={<div className="ob-shell">Loading…</div>}>
      <VerifyEmailContent />
    </Suspense>
  )
}
