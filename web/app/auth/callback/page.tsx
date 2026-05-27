"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { getSupabase, isSupabaseConfigured, postLoginPath } from "../../lib/supabase/client"

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
        router.replace(await postLoginPath())
        return
      }

      const { data } = supabase.auth.onAuthStateChange(async (_event, nextSession) => {
        if (nextSession && !cancelled) {
          router.replace(await postLoginPath())
        }
      })
      subscription = data.subscription

      timeoutId = setTimeout(async () => {
        if (cancelled) return
        const {
          data: { session: late },
        } = await supabase.auth.getSession()
        if (late) {
          router.replace(await postLoginPath())
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
