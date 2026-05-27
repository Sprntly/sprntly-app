"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import Link from "next/link"
import { AuthApiError } from "@supabase/supabase-js"
import { useAuth } from "../lib/auth"
import {
  authLockoutRemainingMs,
  clearSignInAttempts,
  recordFailedSignIn,
  validateWorkEmail,
} from "../lib/auth-validation"
import { publicPath } from "../lib/public-path"

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

  if (auth.kind === "loading" || auth.kind === "authed") {
    return <AuthShell>Loading…</AuthShell>
  }

  if (auth.kind === "unconfigured") {
    return (
      <AuthShell>
        <h1 className="ob-title">Sign-in not configured</h1>
        <p className="ob-desc">Set Supabase env vars in web/.env.local</p>
      </AuthShell>
    )
  }

  if (forgotSent) {
    return (
      <AuthShell>
        <h1 className="ob-title">Check your email</h1>
        <p className="ob-desc">
          If an account exists for <strong>{email}</strong>, you&apos;ll receive a reset
          link shortly.
        </p>
        <button type="button" className="btn btn-primary btn-block btn-lg" onClick={() => { setForgotMode(false); setForgotSent(false) }}>
          Back to sign in
        </button>
      </AuthShell>
    )
  }

  return (
    <AuthShell>
      <div className="ob-eyebrow">Welcome back</div>
      <h1 className="ob-title">Sign in</h1>
      <p className="ob-desc">Use your work email and password to open your workspace.</p>

      <form onSubmit={forgotMode ? onForgot : onSignIn}>
        <div className="field">
          <label className="field-label" htmlFor="email">Work email</label>
          <input
            id="email"
            type="email"
            className="input"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="email"
            required
          />
        </div>
        {!forgotMode && (
          <div className="field">
            <label className="field-label" htmlFor="password">Password</label>
            <div className="pw-row">
              <input
                id="password"
                type={showPassword ? "text" : "password"}
                className="input"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                required
              />
              <button type="button" className="btn btn-ghost btn-sm pw-toggle" onClick={() => setShowPassword((v) => !v)}>
                {showPassword ? "Hide" : "Show"}
              </button>
            </div>
          </div>
        )}
        {lockoutMs > 0 && (
          <div className="auth-error">
            Too many attempts. Try again in {Math.ceil(lockoutMs / 60000)} min.
          </div>
        )}
        {error && <div className="auth-error">{error}</div>}
        <button
          type="submit"
          className="btn btn-primary btn-block btn-lg"
          disabled={submitting || lockoutMs > 0}
        >
          {submitting ? "…" : forgotMode ? "Send reset link" : "Sign in"}
        </button>
      </form>

      {!forgotMode ? (
        <p className="auth-switch">
          <button type="button" className="link-btn" onClick={() => setForgotMode(true)}>
            Forgot password?
          </button>
          {" · "}
          <Link href="/sign-up">Create account</Link>
        </p>
      ) : (
        <p className="auth-switch">
          <button type="button" className="link-btn" onClick={() => setForgotMode(false)}>
            Back to sign in
          </button>
        </p>
      )}

      <p className="auth-legal">
        <Link href={publicPath("/terms")}>Terms</Link> · <Link href={publicPath("/privacy")}>Privacy</Link>
      </p>

      <style jsx>{`
        .pw-row { display: flex; gap: 8px; align-items: center; }
        .pw-row :global(.input) { flex: 1; }
        .pw-toggle { flex-shrink: 0; }
        .auth-error { color: #c0392b; font-size: 13px; padding: 8px 12px; background: rgba(192,57,43,0.08); border-radius: 8px; margin-bottom: 12px; }
        .auth-switch { text-align: center; font-size: 13px; color: var(--ink-3); margin-top: 18px; }
        .auth-switch :global(a), .link-btn { color: var(--ink); font-weight: 600; background: none; border: none; cursor: pointer; font: inherit; text-decoration: underline; padding: 0; }
        .auth-legal { text-align: center; font-size: 11.5px; color: var(--muted); margin-top: 16px; }
        .auth-legal :global(a) { color: var(--ink-3); }
      `}</style>
    </AuthShell>
  )
}

function AuthShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="ob-shell">
      <div className="auth-card">
        <div className="ob-brand-mark">spr<span>ntly</span></div>
        {children}
      </div>
      <style jsx>{`
        .auth-card { width: 100%; max-width: 480px; }
        .ob-brand-mark { font-family: var(--font-display); font-weight: 600; font-size: 22px; letter-spacing: -0.02em; margin-bottom: 48px; text-align: center; color: var(--ink); }
        .ob-brand-mark :global(span) { color: var(--accent); }
      `}</style>
    </div>
  )
}
