"use client"

import { Suspense, useEffect, useState } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { AuthApiError } from "@supabase/supabase-js"
import { useAuth } from "../lib/auth"
import { validatePassword, validateWorkEmail } from "../lib/auth-validation"
import { publicPath } from "../lib/public-path"
import { AuthShell } from "../components/auth/AuthShell"
import { SignUpStep1View, SignUpStep2View } from "../components/auth/SignUpView"

export default function SignUpPage() {
  return (
    <Suspense
      fallback={
        <AuthShell tag="Create account">
          <div className="auth-sub">Loading…</div>
        </AuthShell>
      }
    >
      <SignUpForm />
    </Suspense>
  )
}

function SignUpForm() {
  const auth = useAuth()
  const router = useRouter()
  const searchParams = useSearchParams()
  const prefillEmail = searchParams.get("email") ?? ""

  // Two-step sign-up matching v4 pages 02 (credentials) + 03 (about you).
  // Everything is collected in React state across both steps; the single
  // signUpWithPassword call happens only at the end of step 2 — preserving
  // the existing API contract (and interpretSignUpResponse handling).
  const [step, setStep] = useState<1 | 2>(1)

  const [email, setEmail] = useState(prefillEmail)
  const [password, setPassword] = useState("")
  const [firstName, setFirstName] = useState("")
  const [lastName, setLastName] = useState("")
  const [role, setRole] = useState("Product Manager")
  const [showPassword, setShowPassword] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (auth.kind === "authed") {
      void auth.postLoginPath().then((path) => router.replace(path))
    }
  }, [auth, router])

  function onStep1(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    const emailErr = validateWorkEmail(email)
    if (emailErr) {
      setError(emailErr)
      return
    }
    const pwErr = validatePassword(password)
    if (pwErr) {
      setError(pwErr)
      return
    }
    setStep(2)
  }

  async function onGoogle() {
    setError(null)
    try {
      await auth.signInWithGoogle()
    } catch {
      setError("Couldn't start Google sign-up. Try again.")
    }
  }

  async function onCreate(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    if (!firstName.trim() || !lastName.trim()) {
      setError("First and last name are required.")
      return
    }
    if (!role) {
      setError("Tell us your role so we can tailor your workspace.")
      return
    }
    setSubmitting(true)
    try {
      const result = await auth.signUpWithPassword({
        email,
        password,
        firstName,
        lastName,
        role,
      })
      if (result === "already_registered") {
        setError("An account with this email already exists. Try signing in.")
        setStep(1)
        return
      }
      if (result === "confirm_email") {
        router.replace(`/verify-email?email=${encodeURIComponent(email)}`)
      } else {
        router.replace(await auth.postLoginPath())
      }
    } catch (e) {
      if (e instanceof AuthApiError && e.message.toLowerCase().includes("registered")) {
        setError("An account with this email already exists. Try signing in.")
        setStep(1)
      } else {
        setError("Couldn't create your account. Try again in a moment.")
      }
    } finally {
      setSubmitting(false)
    }
  }

  if (auth.kind === "loading" || auth.kind === "authed") {
    return (
      <AuthShell tag="Create account">
        <div className="auth-sub">Loading…</div>
      </AuthShell>
    )
  }

  if (step === 2) {
    return (
      <SignUpStep2View
        email={email}
        firstName={firstName}
        lastName={lastName}
        role={role}
        submitting={submitting}
        error={error}
        onFirstNameChange={setFirstName}
        onLastNameChange={setLastName}
        onRoleChange={setRole}
        onSubmit={onCreate}
        onBack={() => {
          setError(null)
          setStep(1)
        }}
      />
    )
  }

  return (
    <SignUpStep1View
      email={email}
      password={password}
      showPassword={showPassword}
      error={error}
      termsHref={publicPath("/terms")}
      privacyHref={publicPath("/privacy")}
      onEmailChange={setEmail}
      onPasswordChange={setPassword}
      onToggleShowPassword={() => setShowPassword((v) => !v)}
      onSubmit={onStep1}
      onGoogle={onGoogle}
    />
  )
}
