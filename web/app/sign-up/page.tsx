"use client"

import { Suspense, useEffect, useState } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import Link from "next/link"
import { AuthApiError } from "@supabase/supabase-js"
import { useAuth } from "../lib/auth"
import { PasswordStrengthBar } from "../components/auth/PasswordStrengthBar"
import {
  validatePassword,
  validateWorkEmail,
} from "../lib/auth-validation"
import { publicPath } from "../lib/public-path"

export default function SignUpPage() {
  return (
    <Suspense fallback={<div className="ob-shell">Loading…</div>}>
      <SignUpForm />
    </Suspense>
  )
}

function SignUpForm() {
  const auth = useAuth()
  const router = useRouter()
  const searchParams = useSearchParams()
  const prefillEmail = searchParams.get("email") ?? ""

  const [email, setEmail] = useState(prefillEmail)
  const [password, setPassword] = useState("")
  const [firstName, setFirstName] = useState("")
  const [lastName, setLastName] = useState("")
  const [showPassword, setShowPassword] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (auth.kind === "authed") {
      void auth.postLoginPath().then((path) => router.replace(path))
    }
  }, [auth, router])

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    const emailErr = validateWorkEmail(email)
    if (emailErr) { setError(emailErr); return }
    const pwErr = validatePassword(password)
    if (pwErr) { setError(pwErr); return }
    if (!firstName.trim() || !lastName.trim()) {
      setError("First and last name are required.")
      return
    }
    setSubmitting(true)
    try {
      const result = await auth.signUpWithPassword({
        email,
        password,
        firstName,
        lastName,
      })
      if (result === "confirm_email") {
        router.replace(`/verify-email?email=${encodeURIComponent(email)}`)
      } else {
        router.replace(await auth.postLoginPath())
      }
    } catch (e) {
      if (e instanceof AuthApiError && e.message.toLowerCase().includes("registered")) {
        setError("An account with this email already exists. Try signing in.")
      } else {
        setError("Couldn't create your account. Try again in a moment.")
      }
    } finally {
      setSubmitting(false)
    }
  }

  if (auth.kind === "loading" || auth.kind === "authed") {
    return <div className="ob-shell">Loading…</div>
  }

  return (
    <div className="ob-shell">
      <div className="auth-card">
        <div className="ob-brand-mark">spr<span>ntly</span></div>
        <div className="ob-eyebrow">Get started</div>
        <h1 className="ob-title">Create your account</h1>
        <p className="ob-desc">We&apos;ll use this to personalize your onboarding interview.</p>

        <form onSubmit={onSubmit}>
          <div className="field-row">
            <div className="field">
              <label className="field-label">First name</label>
              <input className="input" value={firstName} onChange={(e) => setFirstName(e.target.value)} placeholder="Sarah" maxLength={50} required />
            </div>
            <div className="field">
              <label className="field-label">Last name</label>
              <input className="input" value={lastName} onChange={(e) => setLastName(e.target.value)} placeholder="Chen" maxLength={50} required />
            </div>
          </div>
          <div className="field">
            <label className="field-label">Work email</label>
            <input type="email" className="input" value={email} onChange={(e) => setEmail(e.target.value)} required />
          </div>
          <div className="field">
            <label className="field-label">Password</label>
            <div className="pw-row">
              <input
                type={showPassword ? "text" : "password"}
                className="input"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="new-password"
                required
              />
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => setShowPassword((v) => !v)}>
                {showPassword ? "Hide" : "Show"}
              </button>
            </div>
            <PasswordStrengthBar password={password} />
          </div>
          {error && <div className="auth-error">{error}</div>}
          <button type="submit" className="btn btn-primary btn-block btn-lg" disabled={submitting}>
            {submitting ? "Creating account…" : "Create account"}
          </button>
        </form>

        <p className="auth-switch">
          Already have an account? <Link href="/sign-in">Sign in</Link>
        </p>
        <p className="auth-legal">
          <Link href={publicPath("/terms")}>Terms</Link> · <Link href={publicPath("/privacy")}>Privacy</Link>
        </p>
      </div>

      <style jsx>{`
        .auth-card { width: 100%; max-width: 480px; }
        .ob-brand-mark { font-family: var(--font-display); font-weight: 400; font-size: 22px; text-align: center; margin-bottom: 48px; }
        .ob-brand-mark :global(span) { color: var(--accent); }
        .field-row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
        .pw-row { display: flex; gap: 8px; align-items: center; }
        .pw-row :global(.input) { flex: 1; }
        .auth-error { color: #c0392b; font-size: 13px; padding: 8px 12px; background: rgba(192,57,43,0.08); border-radius: 8px; margin-bottom: 12px; }
        .auth-switch { text-align: center; font-size: 13px; color: var(--ink-3); margin-top: 18px; }
        .auth-switch :global(a) { color: var(--ink); font-weight: 600; }
        .auth-legal { text-align: center; font-size: 11.5px; color: var(--muted); margin-top: 16px; }
        .auth-legal :global(a) { color: var(--ink-3); }
      `}</style>
    </div>
  )
}
