"use client"

import { useEffect } from "react"
import { useRouter } from "next/navigation"

// The Backlog page was renamed to Ideation. Old links and bookmarks keep
// working via this client-side redirect (the app is a static export, so a
// server redirect() would not survive `next build`).
export default function BacklogRedirect() {
  const router = useRouter()
  useEffect(() => {
    router.replace("/ideation")
  }, [router])
  return null
}
