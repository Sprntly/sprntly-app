"use client"

import { Suspense, useEffect, useState } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { AuthApiError } from "@supabase/supabase-js"
import { useAuth } from "../lib/auth"
import { validatePassword, validateShareDomainEmail, validateWorkEmail } from "../lib/auth-validation"
import { signupApi } from "../lib/api"
import { captureReferralCode } from "../lib/referral"
import { publicPath } from "../lib/public-path"
import { artifactShareApi, type ArtifactShareMetadata } from "../lib/artifactShareApi"
import { prdAccessApi } from "../lib/prdAccessApi"
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
  const shareToken = searchParams.get("share")
  // A bare `?prd=` visit (no `share=` token) — the "full parity" bare-link
  // scope (2026-08-02): same domain-gated sign-up experience, keyed by the
  // PRD's opaque public_id instead of a share row (never the raw sequential
  // id). Only meaningful when there's no share token (share always wins if
  // somehow both are present).
  const prdParam = searchParams.get("prd")
  const prdPublicId = !shareToken && prdParam ? prdParam : null
  const [shareMeta, setShareMeta] = useState<ArtifactShareMetadata | null>(null)
  const [prdMeta, setPrdMeta] = useState<{
    title: string
    owning_company_name: string
    required_email_domain: string | null
  } | null>(null)

  // Fetch once on mount — the entry gate already resolved this same
  // token/prd_id, but a direct /sign-up?share=/?prd= visit (no
  // EntryGateScreen hop) needs its own read. Best-effort: an invalid/expired
  // token or unresolvable prd_id here just means no strip renders — sign-up
  // itself is never blocked by it (ArtifactShareGate/postLoginPath own the
  // actual deny after account creation).
  // Stash a friend's referral code before anything can navigate away. The
  // company it credits is not created until several onboarding steps later —
  // and a Google sign-up leaves the page entirely — so the code has to outlive
  // this URL. Read back once, in the onboarding store, at company creation.
  useEffect(() => {
    captureReferralCode()
  }, [])

  useEffect(() => {
    if (!shareToken) return
    let cancelled = false
    artifactShareApi
      .getMetadata(shareToken)
      .then((meta) => {
        if (!cancelled) setShareMeta(meta)
      })
      .catch(() => {
        /* no strip — not a sign-up blocker */
      })
    return () => {
      cancelled = true
    }
  }, [shareToken])

  useEffect(() => {
    if (!prdPublicId) return
    let cancelled = false
    prdAccessApi
      .getMetadata(prdPublicId)
      .then((meta) => {
        if (!cancelled) setPrdMeta(meta)
      })
      .catch(() => {
        /* no strip — not a sign-up blocker */
      })
    return () => {
      cancelled = true
    }
  }, [prdPublicId])

  const shareContext = shareMeta
    ? {
        title: shareMeta.title || "a document",
        sharerName: shareMeta.sharer_name,
        requiredDomain: shareMeta.required_email_domain,
      }
    : prdMeta
      ? {
          title: prdMeta.title || "a document",
          // No sharer for a bare-link session — the company name is the
          // closest honest "who is this from" this screen can show.
          sharerName: prdMeta.owning_company_name,
          requiredDomain: prdMeta.required_email_domain,
        }
      : undefined

  // Two-step sign-up matching v4 pages 02 (credentials) + 03 (about you).
  // Everything is collected in React state across both steps; the single
  // signUpWithPassword call happens only at the end of step 2 — preserving
  // the existing API contract (and interpretSignUpResponse handling).
  const [step, setStep] = useState<1 | 2>(1)

  const [email, setEmail] = useState(prefillEmail)
  const [password, setPassword] = useState("")
  const [confirmPassword, setConfirmPassword] = useState("")
  const [firstName, setFirstName] = useState("")
  const [lastName, setLastName] = useState("")
  const [role, setRole] = useState("Product Manager")
  const [showPassword, setShowPassword] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  // Separate from `submitting` because the Google button owns its own label,
  // and it is never cleared — the OAuth redirect takes the tab away.
  const [googleSubmitting, setGoogleSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (auth.kind === "authed") {
      void auth.postLoginPath().then((path) => router.replace(path))
    }
  }, [auth, router])

  async function onStep1(e: React.FormEvent) {
    e.preventDefault()
    if (submitting) return
    setError(null)
    const emailErr = validateWorkEmail(email)
    if (emailErr) {
      setError(emailErr)
      return
    }
    // Domain-gated share signup: block BEFORE any signup attempt (including
    // the duplicate-email check below) on a mismatched domain — no Supabase
    // signUp() call is ever made for a doomed signup. A pure client-side
    // check; the real backstop stays server-side (see
    // validateShareDomainEmail's docstring).
    const domainErr = validateShareDomainEmail(email, shareContext?.requiredDomain)
    if (domainErr) {
      setError(domainErr)
      return
    }
    const pwErr = validatePassword(password)
    if (pwErr) {
      setError(pwErr)
      return
    }
    if (password !== confirmPassword) {
      setError("Passwords do not match.")
      return
    }
    // Early duplicate check so a returning user is stopped HERE, not after
    // filling the about-you step. Fail-open on network/API errors — the
    // signUpWithPassword "already_registered" handling remains the backstop.
    setSubmitting(true)
    try {
      const { exists } = await signupApi.emailExists(email)
      if (exists) {
        setError("An account with this email already exists. Try signing in.")
        return
      }
    } catch {
      /* availability check only — proceed; the final signup call re-checks */
    } finally {
      setSubmitting(false)
    }
    setStep(2)
  }

  async function onGoogle() {
    if (submitting || googleSubmitting) return
    setError(null)
    setGoogleSubmitting(true)
    try {
      await auth.signInWithGoogle()
      // No reset on success: the call ends in a full-page redirect to Google,
      // so the button stays busy until this document is gone.
    } catch {
      setError("Couldn't start Google sign-up. Try again.")
      setGoogleSubmitting(false)
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
        // The company/personal split is retired (v6) — every signup is company.
        accountType: "company",
        // Persisted into user_metadata so postLoginPath() can resolve this
        // share on ANY device/session, even one that never saw this tab.
        ...(shareToken ? { pendingShareToken: shareToken } : {}),
        ...(prdPublicId ? { pendingPrdPublicId: prdPublicId } : {}),
      })
      if (result === "already_registered") {
        setError("An account with this email already exists. Try signing in.")
        setStep(1)
        setSubmitting(false)
        return
      }
      if (result === "confirm_email") {
        const verifyParams = new URLSearchParams({ email })
        if (shareToken) verifyParams.set("share", shareToken)
        else if (prdPublicId) verifyParams.set("prd", prdPublicId)
        router.replace(`/verify-email?${verifyParams.toString()}`)
      } else {
        router.replace(await auth.postLoginPath())
      }
      // Deliberately no reset on the success paths. router.replace() is
      // asynchronous and this component stays mounted while the next route
      // loads; clearing `submitting` here flipped the button back to
      // "Continue" mid-navigation, which read as "nothing happened".
    } catch (e) {
      if (e instanceof AuthApiError && e.message.toLowerCase().includes("registered")) {
        setError("An account with this email already exists. Try signing in.")
        setStep(1)
      } else {
        setError("Couldn't create your account. Try again in a moment.")
      }
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
      confirmPassword={confirmPassword}
      showPassword={showPassword}
      submitting={submitting}
      googleSubmitting={googleSubmitting}
      error={error}
      termsHref={publicPath("/terms")}
      privacyHref={publicPath("/privacy")}
      onEmailChange={setEmail}
      onPasswordChange={setPassword}
      onConfirmPasswordChange={setConfirmPassword}
      onToggleShowPassword={() => setShowPassword((v) => !v)}
      onSubmit={(e) => void onStep1(e)}
      onGoogle={onGoogle}
      shareContext={shareContext}
    />
  )
}
