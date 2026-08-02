"use client"

import { useCallback, useEffect, useState } from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import type { EmailOtpType } from "@supabase/supabase-js"
import {
  getPriorSessionSnapshot,
  getSupabase,
  isSupabaseConfigured,
  postLoginPath,
  setPendingInviteSession,
} from "../../lib/supabase/client"
import { AuthShell } from "../../components/auth/AuthShell"

/**
 * Scanner-proof auth-link landing (2026-07-22 Freezing Point incident).
 *
 * Emailed auth links used to be the raw Supabase /auth/v1/verify URL, which
 * is single-use and consumed by a bare GET — corporate mail scanners
 * (SafeLinks etc.) prefetch every link in an email, so the link was dead
 * before a human ever clicked it. Invite emails now link HERE with
 * `?token_hash=…&type=invite` instead: loading the page consumes nothing;
 * the token is only spent by `verifyOtp` when the user clicks the button.
 *
 * After verifyOtp mints the session this page mirrors /auth/callback's
 * routing: the invite-conflict guard (an invite opened in a browser signed
 * in as a DIFFERENT user must not hijack that session), then /set-password
 * for invites, /reset-password for recovery, postLoginPath() otherwise.
 */

const FLOW_TYPES: EmailOtpType[] = [
  "invite",
  "recovery",
  "magiclink",
  "signup",
  "email",
  "email_change",
]

function readParams(): { tokenHash: string; flowType: EmailOtpType } {
  if (typeof window === "undefined") return { tokenHash: "", flowType: "invite" }
  const params = new URLSearchParams(window.location.search)
  const rawType = params.get("type") ?? "invite"
  return {
    tokenHash: params.get("token_hash") ?? "",
    flowType: (FLOW_TYPES as string[]).includes(rawType)
      ? (rawType as EmailOtpType)
      : "invite",
  }
}

export default function AuthConfirmPage() {
  const router = useRouter()
  const [{ tokenHash, flowType }] = useState(readParams)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!isSupabaseConfigured() || !tokenHash) {
      router.replace("/sign-in")
    }
  }, [router, tokenHash])

  const accept = useCallback(async () => {
    setSubmitting(true)
    setError(null)
    const supabase = getSupabase()
    // Who was signed in BEFORE this link — captured before verifyOtp swaps
    // the persisted session to the invitee (mirrors /auth/callback).
    const prior = getPriorSessionSnapshot()
    const { data, error: verifyError } = await supabase.auth.verifyOtp({
      type: flowType,
      token_hash: tokenHash,
    })
    const session = data?.session
    if (verifyError || !session) {
      setSubmitting(false)
      setError(
        "This link has expired or was already used. Ask your teammate to " +
          "re-send the invite, or sign in if you already have an account.",
      )
      return
    }
    if (flowType === "invite" && prior && prior.userId !== session.user.id) {
      // Same guard as /auth/callback: hold the minted invitee session in
      // memory, restore the original account, let /invite-conflict decide.
      setPendingInviteSession({
        email: session.user.email ?? null,
        accessToken: session.access_token,
        refreshToken: session.refresh_token,
      })
      try {
        await supabase.auth.setSession({
          access_token: prior.accessToken,
          refresh_token: prior.refreshToken,
        })
      } catch {
        // Best-effort restore; either way we do NOT enter as the invitee.
      }
      router.replace("/invite-conflict?kept=1")
      return
    }
    if (flowType === "recovery") {
      router.replace("/reset-password")
      return
    }
    if (flowType === "invite") {
      router.replace("/set-password")
      return
    }
    router.replace(await postLoginPath())
  }, [flowType, router, tokenHash])

  const heading =
    flowType === "invite" ? (
      <>
        Join your <em>team.</em>
      </>
    ) : (
      <>
        Confirm to <em>continue.</em>
      </>
    )
  const cta = flowType === "invite" ? "Accept invitation" : "Continue"

  return (
    <AuthShell tag={flowType === "invite" ? "Join your team" : undefined}>
      <div className="auth-h">{heading}</div>
      {error ? (
        <>
          <div className="auth-sub">{error}</div>
          <div className="auth-foot">
            <Link href="/sign-in">Go to sign in</Link>
          </div>
        </>
      ) : (
        <>
          <div className="auth-sub">
            {flowType === "invite"
              ? "You've been invited to a Sprntly workspace. Click below to accept and set up your account."
              : "Click below to confirm and continue."}
          </div>
          <button
            type="button"
            className="btn btn-brand btn-block"
            onClick={() => void accept()}
            disabled={submitting}
          >
            {submitting ? "One moment…" : cta}
          </button>
        </>
      )}
    </AuthShell>
  )
}
