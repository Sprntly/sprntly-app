"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { AuthApiError } from "@supabase/supabase-js"
import { useAuth } from "../lib/auth"
import { publicPath } from "../lib/public-path"

type Mode = "sign-in" | "sign-up"

export default function SignInPage() {
  const auth = useAuth()
  const router = useRouter()
  const [mode, setMode] = useState<Mode>("sign-in")
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirmEmailSent, setConfirmEmailSent] = useState(false)

  useEffect(() => {
    if (auth.kind === "authed") {
      void auth.postLoginPath().then((path) => router.replace(path))
    }
  }, [auth, router])

  async function redirectAfterAuth() {
    router.replace(await auth.postLoginPath())
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      if (mode === "sign-in") {
        await auth.signInWithPassword(email, password)
        await redirectAfterAuth()
      } else {
        const result = await auth.signUpWithPassword(email, password)
        if (result === "confirm_email") {
          setConfirmEmailSent(true)
        } else {
          await redirectAfterAuth()
        }
      }
    } catch (e) {
      setError(friendlyAuthError(e, mode))
    } finally {
      setSubmitting(false)
    }
  }

  if (auth.kind === "loading" || auth.kind === "authed") {
    return <SignInLoading />
  }

  if (auth.kind === "unconfigured") {
    return (
      <SignInShell>
        <div className="signin-card">
          <div className="ob-brand-mark">
            spr<span>ntly</span>
          </div>
          <h1 className="ob-title">Sign-in not configured</h1>
          <p className="ob-desc">
            Set <code>NEXT_PUBLIC_SUPABASE_URL</code> and{" "}
            <code>NEXT_PUBLIC_SUPABASE_ANON_KEY</code> in <code>web/.env.local</code>.
          </p>
        </div>
      </SignInShell>
    )
  }

  if (confirmEmailSent) {
    return (
      <SignInShell>
        <div className="signin-card">
          <div className="ob-brand-mark">
            spr<span>ntly</span>
          </div>
          <div className="ob-eyebrow">Almost there</div>
          <h1 className="ob-title">Confirm your email</h1>
          <p className="ob-desc">
            We sent a confirmation link to <strong>{email}</strong>. Click it to
            activate your account, then sign in.
          </p>
          <button
            type="button"
            className="btn btn-primary btn-block btn-lg"
            onClick={() => {
              setConfirmEmailSent(false)
              setMode("sign-in")
            }}
          >
            Back to sign in
          </button>
        </div>
      </SignInShell>
    )
  }

  return (
    <SignInShell>
      <div className="signin-card">
        <div className="ob-brand-mark">
          spr<span>ntly</span>
        </div>
        <div className="ob-eyebrow">{mode === "sign-in" ? "Welcome back" : "Get started"}</div>
        <h1 className="ob-title">
          {mode === "sign-in" ? "Sign in" : "Create your account"}
        </h1>
        <p className="ob-desc">
          {mode === "sign-in"
            ? "Enter your email and password to open your workspace."
            : "One account, one workspace. You can invite teammates after setup."}
        </p>

        <form onSubmit={onSubmit}>
          <div className="field">
            <label className="field-label" htmlFor="email">
              Email
            </label>
            <input
              id="email"
              type="email"
              className="input"
              placeholder="you@company.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
              required
            />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="password">
              Password
            </label>
            <input
              id="password"
              type="password"
              className="input"
              placeholder={mode === "sign-up" ? "At least 8 characters" : "Your password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={mode === "sign-in" ? "current-password" : "new-password"}
              minLength={mode === "sign-up" ? 8 : undefined}
              required
            />
          </div>
          {error && <div className="signin-error">{error}</div>}
          <button
            type="submit"
            className="btn btn-primary btn-block btn-lg"
            disabled={submitting || !email.trim() || !password}
          >
            {submitting
              ? mode === "sign-in"
                ? "Signing in…"
                : "Creating account…"
              : mode === "sign-in"
              ? "Sign in"
              : "Create account"}
          </button>
        </form>

        <p className="signin-switch">
          {mode === "sign-in" ? (
            <>
              Don&apos;t have an account?{" "}
              <button type="button" onClick={() => { setMode("sign-up"); setError(null) }}>
                Create one
              </button>
            </>
          ) : (
            <>
              Already have an account?{" "}
              <button type="button" onClick={() => { setMode("sign-in"); setError(null) }}>
                Sign in
              </button>
            </>
          )}
        </p>

        <p className="signin-legal">
          By continuing you agree to our{" "}
          <a href={publicPath("/terms")}>Terms of Use</a> and{" "}
          <a href={publicPath("/privacy")}>Privacy Policy</a>.
        </p>
      </div>

      <style jsx>{`
        .signin-card {
          width: 100%;
          max-width: 480px;
        }
        .signin-error {
          color: #c0392b;
          font-size: 13px;
          padding: 8px 12px;
          background: rgba(192, 57, 43, 0.08);
          border-radius: 8px;
          margin-bottom: 12px;
        }
        .signin-switch {
          text-align: center;
          font-size: 13px;
          color: var(--ink-3);
          margin-top: 18px;
        }
        .signin-switch button {
          background: none;
          border: none;
          padding: 0;
          font: inherit;
          color: var(--ink);
          font-weight: 600;
          cursor: pointer;
          text-decoration: underline;
        }
        .signin-legal {
          text-align: center;
          font-size: 11.5px;
          color: var(--muted);
          margin-top: 16px;
        }
        .signin-legal a {
          color: var(--ink-3);
        }
        code {
          font-family: var(--font-mono);
          font-size: 12px;
          background: var(--surface-2);
          padding: 1px 5px;
          border-radius: 4px;
        }
      `}</style>
    </SignInShell>
  )
}

function friendlyAuthError(e: unknown, mode: Mode): string {
  if (e instanceof AuthApiError) {
    if (e.message === "Invalid login credentials") {
      return "Wrong email or password."
    }
    if (e.message.toLowerCase().includes("already registered")) {
      return "An account with this email already exists. Try signing in."
    }
    if (e.message.toLowerCase().includes("password")) {
      return "Password must be at least 8 characters."
    }
    if (e.message) return e.message
  }
  return mode === "sign-in"
    ? "Couldn't sign in. Try again in a moment."
    : "Couldn't create your account. Try again in a moment."
}

function SignInShell({ children }: { children: React.ReactNode }) {
  return <div className="ob-shell signin-shell">{children}</div>
}

function SignInLoading() {
  return (
    <SignInShell>
      <div className="signin-card" style={{ textAlign: "center", color: "var(--muted)" }}>
        Loading…
      </div>
    </SignInShell>
  )
}
