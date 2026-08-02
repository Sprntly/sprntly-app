"use client"

import { useEffect, useState } from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import {
  clearPendingInviteSession,
  getPendingInviteSession,
  getSupabase,
  isSupabaseConfigured,
  type PendingInviteSession,
} from "../lib/supabase/client"
import { clearSessionScopedStorage } from "../lib/auth"
import { AuthShell } from "../components/auth/AuthShell"

const SET_PASSWORD_PATH = "/set-password"

/**
 * Blocked-invite landing. Two modes:
 *
 * 1. One-user-one-company invariant (2026-07, default). postLoginPath routes
 *    here when a signed-in user has a pending workspace invite from a DIFFERENT
 *    company than the one they already belong to. The backend refuses such
 *    accepts (409 on /v1/invites/accept); before this page the refusal was
 *    invisible — the invitee just landed in their own workspace while the
 *    invite dangled forever. Sending stays unrestricted; the block (and its
 *    explanation) lives here, at the point the invitee tries to come in.
 *
 * 2. Kept-session mode (`?kept=1`, 2026-07). /auth/callback routes here when an
 *    invite magic link is opened in a browser already signed in as a DIFFERENT
 *    user. Adopting the invite would silently log the existing user out, so the
 *    callback restores their session, holds the (already minted) invitee
 *    session in memory, and lands here so the user can CHOOSE:
 *      - Stay signed in as the current account (default — nothing changes), or
 *      - Switch into the invited account (no dead-link re-click needed).
 *    The invite link is one-time, so if the held session is gone (e.g. the page
 *    was reloaded) we can only offer "stay" + guidance to get a fresh invite.
 */
export default function InviteConflictPage() {
  const router = useRouter()
  const [email, setEmail] = useState<string | null>(null)
  const [kept, setKept] = useState(false)
  const [pending, setPending] = useState<PendingInviteSession | null>(null)
  const [switching, setSwitching] = useState(false)
  const [switchError, setSwitchError] = useState(false)

  useEffect(() => {
    setKept(new URLSearchParams(window.location.search).has("kept"))
    setPending(getPendingInviteSession())
    if (!isSupabaseConfigured()) return
    void getSupabase()
      .auth.getSession()
      .then(({ data: { session } }) => setEmail(session?.user.email ?? null))
  }, [])

  // Keep the current account — discard the held invitee session and go home.
  function stay() {
    clearPendingInviteSession()
    router.replace("/")
  }

  // Adopt the invited account. Leaving the current account, so clear its
  // session-scoped UI state first, then swap the auth session to the invitee
  // (already minted by the link) and send them to finish setting a password.
  async function switchToInvited() {
    if (!pending || switching) return
    setSwitching(true)
    setSwitchError(false)
    clearSessionScopedStorage()
    try {
      const { error } = await getSupabase().auth.setSession({
        access_token: pending.accessToken,
        refresh_token: pending.refreshToken,
      })
      if (error) throw error
      clearPendingInviteSession()
      router.replace(SET_PASSWORD_PATH)
    } catch {
      setSwitchError(true)
      setSwitching(false)
    }
  }

  if (kept) {
    // The held invitee session is still around → offer the real choice.
    if (pending) {
      return (
        <AuthShell tag="Workspace invite">
          <div className="auth-h">
            You&apos;re already <em>signed in.</em>
          </div>
          <div className="auth-sub">
            This invite was sent to{" "}
            <strong>{pending.email ?? "another address"}</strong>, but you&apos;re
            already signed in{email ? (
              <>
                {" "}as <strong>{email}</strong>
              </>
            ) : null}. We didn&apos;t switch you automatically — choose which
            account you want to use.
          </div>
          {switchError && (
            <div className="auth-error">
              Couldn&apos;t switch accounts. Your current session is unchanged —
              ask the inviter to re-send the invite.
            </div>
          )}
          <div className="ic-choices">
            <button
              type="button"
              className="ic-choice is-primary"
              onClick={stay}
              disabled={switching}
            >
              <span className="ic-choice-cap">Stay signed in</span>
              <span className="ic-choice-email">
                {email ?? "your current account"}
              </span>
            </button>
            <button
              type="button"
              className="ic-choice"
              onClick={() => void switchToInvited()}
              disabled={switching}
            >
              <span className="ic-choice-cap">
                {switching ? "Switching…" : "Switch account"}
              </span>
              <span className="ic-choice-email">
                {pending.email ?? "the invited account"}
              </span>
            </button>
          </div>
        </AuthShell>
      )
    }

    // No held session (e.g. the page was reloaded). The one-time link is spent,
    // so tell the truth and point at the recoverable paths.
    return (
      <AuthShell tag="Workspace invite">
        <div className="auth-h">
          You&apos;re still <em>signed in.</em>
        </div>
        <div className="auth-sub">
          We kept your current account{email ? (
            <>
              {" "}(<strong>{email}</strong>)
            </>
          ) : null}{" "}
          signed in instead of switching to the invited account. Invite links
          can only be used once, so reopening the same link now shows
          &ldquo;invalid.&rdquo; To accept the invite, ask the inviter to
          re-send it and open the new link while signed out — or use
          &ldquo;Forgot password&rdquo; to set a password for the invited email,
          then sign in.
        </div>
        <div className="auth-foot">
          <Link href="/">Continue to your workspace</Link>
        </div>
      </AuthShell>
    )
  }

  return (
    <AuthShell tag="Workspace invite">
      <div className="auth-h">
        This invite can&apos;t be <em>accepted.</em>
      </div>
      <div className="auth-sub">
        {email ? (
          <>
            You were invited to another team&apos;s workspace, but{" "}
            <strong>{email}</strong> already belongs to a different company.
          </>
        ) : (
          "You were invited to another team's workspace, but this email already belongs to a different company."
        )}{" "}
        For now an account can only be part of one company — ask the inviter to
        use a different email address for you.
      </div>
      <div className="auth-foot">
        <Link href="/">Continue to your workspace</Link>
      </div>
    </AuthShell>
  )
}
