"use client"

import { useCallback, useEffect, useState } from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { AuthApiError } from "@supabase/supabase-js"
import {
  getSupabase,
  isSupabaseConfigured,
  postLoginPath,
} from "../lib/supabase/client"
import { fetchUserProfile, updateUserProfile } from "../lib/onboarding/store"
import { validatePassword } from "../lib/auth-validation"
import { AuthShell } from "../components/auth/AuthShell"
import { PasswordStrengthBar } from "../components/auth/PasswordStrengthBar"
import { ArrowRight, Eye, EyeOff } from "../components/auth/icons"

/**
 * Set-password landing for INVITED users (2026-07-17 invite rules).
 *
 * The flow:
 *   1. A teammate invites them → Supabase admin invite email
 *   2. Clicking the link lands on /auth/callback with type=invite — the
 *      invitee is authenticated by the link but has NO password yet — and
 *      the callback routes here
 *   3. They enter their first/last name and create a password →
 *      supabase.auth.updateUser({ password }) + updateUserProfile (an
 *      admin-invited auth user has an empty profile row — no signup form
 *      ever collected their name)
 *   4. Continue → postLoginPath() (which auto-accepts their pending
 *      workspace invite and lands them in the team's workspace)
 *
 * Existing accounts never reach this page: their invite email is a plain
 * sign-in notification, not an auth link. Opened without a session (expired
 * link, direct navigation) it renders a back-to-sign-in state — the sibling
 * of /reset-password's no-session handling.
 */
export default function SetPasswordPage() {
  const router = useRouter()
  const [hasSession, setHasSession] = useState<boolean | null>(null)
  const [email, setEmail] = useState<string | null>(null)
  const [firstName, setFirstName] = useState("")
  const [lastName, setLastName] = useState("")
  const [password, setPassword] = useState("")
  const [confirm, setConfirm] = useState("")
  const [showPassword, setShowPassword] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // The invite link produced the session via /auth/callback; without one,
  // updateUser would only return a confusing auth error — render the
  // no-session state instead.
  useEffect(() => {
    if (!isSupabaseConfigured()) {
      setHasSession(false)
      return
    }
    void getSupabase()
      .auth.getSession()
      .then(({ data: { session } }) => {
        setHasSession(Boolean(session))
        setEmail(session?.user.email ?? null)
        // Seed the name from any auth metadata (usually empty for
        // admin-invited users; never clobber something already typed).
        const meta = (session?.user.user_metadata ?? {}) as Record<string, unknown>
        const str = (v: unknown) => (typeof v === "string" ? v.trim() : "")
        setFirstName((prev) => prev || str(meta.first_name) || str(meta.given_name))
        setLastName((prev) => prev || str(meta.last_name) || str(meta.family_name))
      })
  }, [])

  const onSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault()
      setError(null)
      if (!firstName.trim() || !lastName.trim()) {
        setError("First and last name are required.")
        return
      }
      const pwErr = validatePassword(password)
      if (pwErr) {
        setError(pwErr)
        return
      }
      if (password !== confirm) {
        setError("Passwords do not match.")
        return
      }
      setSubmitting(true)
      try {
        const supabase = getSupabase()
        const { data, error: updateErr } = await supabase.auth.updateUser({
          password,
          data: { first_name: firstName.trim(), last_name: lastName.trim() },
        })
        if (updateErr) throw updateErr
        // Persist the name on the profiles row (source of truth for how the
        // app addresses them). Preserve any role/priorities already there.
        const userId = data.user?.id
        if (userId) {
          try {
            const existing = await fetchUserProfile(userId)
            await updateUserProfile(userId, {
              first_name: firstName,
              last_name: lastName,
              role: existing?.role ?? null,
            })
          } catch {
            /* name save is best-effort — Settings → Profile can fix it */
          }
        }
        // Into the app — postLoginPath auto-accepts the pending invite.
        router.replace(await postLoginPath())
      } catch (err) {
        setError(
          err instanceof AuthApiError
            ? err.message
            : "Couldn't set your password. Try again in a moment.",
        )
        setSubmitting(false)
      }
    },
    [firstName, lastName, password, confirm, router],
  )

  if (hasSession === null) {
    return (
      <AuthShell tag="Join your team">
        <div className="auth-sub">Loading…</div>
      </AuthShell>
    )
  }

  if (!hasSession) {
    return (
      <AuthShell tag="Join your team">
        <div className="auth-h">
          This invite link has <em>expired.</em>
        </div>
        <div className="auth-sub">
          Ask your teammate to re-send the invite, or sign in if you already
          have an account.
        </div>
        <div className="auth-foot">
          <Link href="/sign-in">Go to sign in</Link>
        </div>
      </AuthShell>
    )
  }

  return (
    <AuthShell tag="Join your team">
      <div className="auth-h">
        Create your <em>account.</em>
      </div>
      <div className="auth-sub">
        {email ? (
          <>
            You&apos;re joining as <strong>{email}</strong>. Tell us your name
            and set a password to finish creating your account.
          </>
        ) : (
          "Tell us your name and set a password to finish creating your account."
        )}
      </div>

      <form onSubmit={(e) => void onSubmit(e)}>
        <div className="auth-form-grid">
          <div className="field">
            <div className="field-l">
              <label htmlFor="sp-first-name">First name</label>{" "}
              <span className="req">*</span>
            </div>
            <input
              id="sp-first-name"
              className="inp"
              value={firstName}
              onChange={(e) => setFirstName(e.target.value)}
              maxLength={50}
              autoComplete="given-name"
              required
            />
          </div>
          <div className="field">
            <div className="field-l">
              <label htmlFor="sp-last-name">Last name</label>{" "}
              <span className="req">*</span>
            </div>
            <input
              id="sp-last-name"
              className="inp"
              value={lastName}
              onChange={(e) => setLastName(e.target.value)}
              maxLength={50}
              autoComplete="family-name"
              required
            />
          </div>
        </div>
        <div className="field">
          <div className="field-l">
            <label htmlFor="new-password">Password</label>{" "}
            <span className="req">*</span>
          </div>
          <div className="inp-pwd-wrap">
            <input
              id="new-password"
              type={showPassword ? "text" : "password"}
              className="inp"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Min 8 chars, 1 number, 1 symbol"
              autoComplete="new-password"
              required
            />
            <button
              type="button"
              className="pwd-toggle"
              aria-label={showPassword ? "Hide password" : "Show password"}
              onClick={() => setShowPassword((v) => !v)}
            >
              {showPassword ? <EyeOff /> : <Eye />}
            </button>
          </div>
          <PasswordStrengthBar password={password} />
        </div>
        <div className="field">
          <div className="field-l">
            <label htmlFor="confirm-password">Confirm password</label>{" "}
            <span className="req">*</span>
          </div>
          <input
            id="confirm-password"
            type={showPassword ? "text" : "password"}
            className="inp"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            autoComplete="new-password"
            required
          />
        </div>
        {error && <div className="auth-error">{error}</div>}
        <button
          type="submit"
          className="btn btn-brand btn-block"
          style={{ marginTop: 6 }}
          disabled={submitting}
        >
          {submitting ? "Joining…" : "Set password & join"}
          {!submitting && <ArrowRight width={14} height={14} />}
        </button>
      </form>
    </AuthShell>
  )
}
