"use client"

import { useEffect, useState } from "react"
import Link from "next/link"
import { getSupabase, isSupabaseConfigured } from "../lib/supabase/client"
import { AuthShell } from "../components/auth/AuthShell"

/**
 * Blocked-invite landing (one-user-one-company invariant, 2026-07).
 *
 * postLoginPath routes here when a signed-in user has a pending workspace
 * invite from a DIFFERENT company than the one they already belong to. The
 * backend refuses such accepts (409 on /v1/invites/accept), and before this
 * page the refusal was invisible: the invitee just landed in their own
 * workspace while the invite dangled forever. Sending the invite stays
 * unrestricted — the block (and its explanation) lives here, at the point
 * the invitee tries to come in.
 */
export default function InviteConflictPage() {
  const [email, setEmail] = useState<string | null>(null)

  useEffect(() => {
    if (!isSupabaseConfigured()) return
    void getSupabase()
      .auth.getSession()
      .then(({ data: { session } }) => setEmail(session?.user.email ?? null))
  }, [])

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
