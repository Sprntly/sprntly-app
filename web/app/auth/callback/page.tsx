"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import type { Session } from "@supabase/supabase-js"
import {
  getPriorSessionSnapshot,
  getSupabase,
  isSupabaseConfigured,
  postLoginPath,
  setPendingInviteSession,
} from "../../lib/supabase/client"
import { isInviteFlow, isRecoveryFlow } from "../../lib/authRecovery"

const RESET_PASSWORD_PATH = "/reset-password"
const SET_PASSWORD_PATH = "/set-password"

export default function AuthCallbackPage() {
  const router = useRouter()
  const [message, setMessage] = useState("Completing sign-in…")

  useEffect(() => {
    if (!isSupabaseConfigured()) {
      router.replace("/sign-in")
      return
    }

    const supabase = getSupabase()
    let subscription: { unsubscribe: () => void } | null = null
    let timeoutId: ReturnType<typeof setTimeout> | null = null
    let cancelled = false
    // Capture the flow flags from the URL once — supabase strips the
    // hash after detectSessionInUrl runs, so we can't re-read it later.
    const recovery = isRecoveryFlow(window.location.href)
    // Workspace-invite landing (admin invite link): the brand-new invitee is
    // authenticated by the link but has NO password yet — force them through
    // /set-password before the app (2026-07-17 invite rules).
    const invite = isInviteFlow(window.location.href)
    // Who was signed in BEFORE this link initialized the client — captured
    // pre-detectSessionInUrl, so it survives the invite session overwriting it.
    const prior = getPriorSessionSnapshot()

    async function nextPath(): Promise<string> {
      if (recovery) return RESET_PASSWORD_PATH
      if (invite) return SET_PASSWORD_PATH
      return await postLoginPath()
    }

    // Route once a session is in hand — but guard the invite case: if an invite
    // magic link was opened in a browser already signed in as a DIFFERENT user,
    // Supabase has swapped the persisted session to the invitee, silently
    // logging the original user out. Hold the (already minted) invitee session
    // in memory — its one-time link can't be reopened — then restore the
    // original account and let /invite-conflict offer a choice between the two.
    // Only invite links are guarded — an OAuth/password sign-in is a deliberate
    // account switch and must be allowed to replace the session.
    async function routeForSession(active: Session): Promise<void> {
      if (invite && prior && prior.userId !== active.user.id) {
        setPendingInviteSession({
          email: active.user.email ?? null,
          accessToken: active.access_token,
          refreshToken: active.refresh_token,
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
      router.replace(await nextPath())
    }

    async function finish() {
      const params = new URLSearchParams(window.location.search)
      const code = params.get("code")
      if (code) {
        const { error } = await supabase.auth.exchangeCodeForSession(code)
        if (error && !cancelled) {
          setMessage("Sign-in failed. Redirecting…")
          router.replace("/sign-in")
          return
        }
      }

      const {
        data: { session },
      } = await supabase.auth.getSession()
      if (session && !cancelled) {
        await routeForSession(session)
        return
      }

      const { data } = supabase.auth.onAuthStateChange(async (event, nextSession) => {
        // Supabase fires PASSWORD_RECOVERY when the recovery session is
        // established — treat it as recovery even if the URL didn't
        // carry type=recovery (defensive across SDK versions).
        if (event === "PASSWORD_RECOVERY" && !cancelled) {
          router.replace(RESET_PASSWORD_PATH)
          return
        }
        if (nextSession && !cancelled) {
          await routeForSession(nextSession)
        }
      })
      subscription = data.subscription

      timeoutId = setTimeout(async () => {
        if (cancelled) return
        const {
          data: { session: late },
        } = await supabase.auth.getSession()
        if (late) {
          await routeForSession(late)
        } else {
          setMessage("Sign-in failed. Redirecting…")
          router.replace("/sign-in")
        }
      }, 4000)
    }

    void finish()

    return () => {
      cancelled = true
      subscription?.unsubscribe()
      if (timeoutId) clearTimeout(timeoutId)
    }
  }, [router])

  return (
    <div className="ob-shell" style={{ justifyContent: "center" }}>
      <p style={{ color: "var(--muted)", fontSize: 14 }}>{message}</p>
    </div>
  )
}
