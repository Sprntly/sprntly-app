"use client"

import { Suspense, useEffect, useState } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { useAuth } from "../lib/auth"
import {
  authLockoutRemainingMs,
  clearSignInAttempts,
  describeSignInError,
  recordFailedSignIn,
  validateWorkEmail,
} from "../lib/auth-validation"
import { publicPath } from "../lib/public-path"
import { artifactShareApi } from "../lib/artifactShareApi"
import { AuthShell } from "../components/auth/AuthShell"
import { SignInView } from "../components/auth/SignInView"

export default function SignInPage() {
  return (
    <Suspense
      fallback={
        <AuthShell tag="Sign in">
          <div className="auth-sub">Loading…</div>
        </AuthShell>
      }
    >
      <SignInForm />
    </Suspense>
  )
}

function SignInForm() {
  const auth = useAuth()
  const router = useRouter()
  const searchParams = useSearchParams()
  // An existing account signing in via a shared-artifact link carries the
  // token on the URL, not in user_metadata — postLoginPath()'s own pending-
  // share resolution only fires for a token set at SIGN-UP time (a fresh
  // signup, per the artifact-share flow's other entry point), so it never
  // sees this one. Resolving it here is the sibling case for a RETURNING
  // user: same outcome-to-path mapping, applied on top of whatever
  // postLoginPath() would otherwise return.
  const shareToken = searchParams.get("share")
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [showPassword, setShowPassword] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [forgotMode, setForgotMode] = useState(false)
  const [lockoutMs, setLockoutMs] = useState(0)

  // Best-effort: an invalid/expired token, or any resolve failure, falls
  // through to the caller's own default path — never strands the user on a
  // blank screen, and never re-derives the deny reason differently from the
  // server's own resolve() outcome.
  async function withShareResolved(defaultPath: string): Promise<string> {
    if (!shareToken) return defaultPath
    try {
      const outcome = await artifactShareApi.resolve(shareToken)
      if (outcome.outcome === "guest_view") {
        return `/?prd=${outcome.artifact_id}&share=${shareToken}`
      }
      if (outcome.outcome === "blocked") {
        return `/not-authorized?share=${shareToken}&reason=${outcome.reason}`
      }
    } catch {
      /* resolve failed — fall through to defaultPath */
    }
    return defaultPath
  }

  useEffect(() => {
    if (auth.kind === "authed") {
      void auth
        .postLoginPath()
        .then((path) => withShareResolved(path))
        .then((path) => router.replace(path))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auth, router, shareToken])

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
      const defaultPath = await auth.postLoginPath()
      router.replace(await withShareResolved(defaultPath))
      // Stay in the submitting state — the button keeps its loading label
      // until navigation unmounts this page.
    } catch (e) {
      const { message, countsAsFailedAttempt } = describeSignInError(e)
      if (countsAsFailedAttempt) {
        recordFailedSignIn()
        setLockoutMs(authLockoutRemainingMs())
      }
      setError(message)
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
    } catch {
      // Swallow either way — never reveal whether the address is registered.
    }
    // The recovery email carries a 6-digit code; /reset-password collects it.
    router.push(`/reset-password?email=${encodeURIComponent(email)}`)
  }

  async function onGoogle() {
    setError(null)
    try {
      await auth.signInWithGoogle()
    } catch {
      setError("Couldn't start Google sign-in. Try again.")
    }
  }

  // While a password submit is in flight, keep the form on screen (button in
  // its loading state) even after auth flips to "authed" — the full-screen
  // "Loading…" swap is only for visitors who arrive already signed in.
  if ((auth.kind === "loading" || auth.kind === "authed") && !submitting) {
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
