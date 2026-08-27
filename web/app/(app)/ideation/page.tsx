"use client"

import { useEffect } from "react"

// Ideation was renamed back to Backlog (2026-08-27), reversing the July rename
// that went the other way. This file is what that costs: the redirect swapped
// ends, so every /ideation link — bookmarks, a shared URL, an older chat answer
// that handed one out — still lands on the page.
//
// A HARD location.replace (not router.replace), for the reason the previous
// redirect carried in the opposite direction: on a cold first visit the app
// shell rewrites the URL to append ?company= during hydration, and an
// App-Router navigation racing that rewrite gets aborted, leaving a blank page
// (seen on staging). A full document navigation cannot be cancelled by a
// history rewrite.
export default function IdeationRedirect() {
  useEffect(() => {
    window.location.replace("/backlog")
  }, [])
  return null
}
