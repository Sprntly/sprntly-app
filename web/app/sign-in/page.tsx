"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { AuthApiError } from "@supabase/supabase-js"
import { useAuth } from "../lib/auth"
import {
  authLockoutRemainingMs,
  clearSignInAttempts,
  recordFailedSignIn,
  validateWorkEmail,
} from "../lib/auth-validation"
import { publicPath } from "../lib/public-path"
import { AuthShell } from "../components/auth/AuthShell"
import { SignInView } from "../components/auth/SignInView"

export default function SignInPage() {
  const auth = useAuth()
  const router = useRouter()
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [showPassword, setShowPassword] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [forgotMode, setForgotMode] = useState(false)
  const [forgotSent, setForgotSent] = useState(false)
  const [lockoutMs, setLockoutMs] = useState(0)

  useEffect(() => {
    if (auth.kind === "authed") {
      void auth.postLoginPath().then((path) => router.replace(path))
    }
  }, [auth, router])

  useEffect(() => {
    setLockoutMs(authLockoutRemainingMs())
    const id = setInterval(() => setLockoutMs(authLockoutRemainingMs()), 1000)
    return () => clearInterval(id)
  }, [])

  async function onSignIn(e: React.FormEvent) {
    e.preventDefault()
    if (lockoutMs > 0) return
    setError(null)
    const emailErr = validateWorkEmail(email)
    if (emailErr) {
      setError(emailErr)
      return
    }
    setSubmitting(true)
    try {
      await auth.signInWithPassword(email, password)
      clearSignInAttempts()
      router.replace(await auth.postLoginPath())
    } catch (e) {
      recordFailedSignIn()
      setLockoutMs(authLockoutRemainingMs())
      if (e instanceof AuthApiError && e.message === "Invalid login credentials") {
        setError("Email or password incorrect.")
      } else {
        setError("Couldn't sign in. Try again in a moment.")
      }
    } finally {
      setSubmitting(false)
    }
  }

  async function onForgot(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    const emailErr = validateWorkEmail(email)
    if (emailErr) {
      setError(emailErr)
      return
    }
    setSubmitting(true)
    try {
      await auth.resetPassword(email)
      setForgotSent(true)
    } catch {
      setForgotSent(true)
    } finally {
      setSubmitting(false)
    }
  }

  async function onGoogle() {
    setError(null)
    try {
      await auth.signInWithGoogle()
    } catch {
      setError("Couldn't start Google sign-in. Try again.")
    }
  }

  if (auth.kind === "loading" || auth.kind === "authed") {
    return (
      <AuthShell tag="Sign in">
        <div className="auth-sub">Loading…</div>
      </AuthShell>
    )
  }

  if (auth.kind === "unconfigured") {
    return (
      <AuthShell tag="Sign in">
        <div className="auth-h">Sign-in <em>not configured.</em></div>
        <div className="auth-sub">Set Supabase env vars in web/.env.local</div>
      </AuthShell>
    )
  }

  if (forgotSent) {
    return (
      <AuthShell tag="Reset password">
        <div className="auth-h">Check your <em>email.</em></div>
        <div className="auth-sub">
          If an account exists for <strong>{email}</strong>, you&apos;ll receive a reset link
          shortly.
        </div>
        <button
          type="button"
          className="btn btn-brand btn-block"
          onClick={() => {
            setForgotMode(false)
            setForgotSent(false)
          }}
        >
          Back to sign in
        </button>
      </AuthShell>
    )
  }

  return (
    <SignInView
      email={email}
      password={password}
      showPassword={showPassword}
      submitting={submitting}
      error={error}
      forgotMode={forgotMode}
      lockoutMs={lockoutMs}
      termsHref={publicPath("/terms")}
      privacyHref={publicPath("/privacy")}
      onEmailChange={setEmail}
      onPasswordChange={setPassword}
      onToggleShowPassword={() => setShowPassword((v) => !v)}
      onSubmit={forgotMode ? onForgot : onSignIn}
      onGoogle={onGoogle}
      onEnterForgot={() => setForgotMode(true)}
      onExitForgot={() => setForgotMode(false)}
    />
  )
}
