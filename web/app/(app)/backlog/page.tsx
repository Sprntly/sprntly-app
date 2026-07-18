"use client"

import { useEffect } from "react"

// The Backlog page was renamed to Ideation. Old links and bookmarks keep
// working via this client-side redirect. A HARD location.replace (not
// router.replace): on a cold first visit the app shell rewrites the URL to
// append ?company= during hydration, and an App-Router navigation racing that
// rewrite gets aborted, leaving a blank page (seen on staging). A full
// document navigation can't be cancelled by history rewrites.
export default function BacklogRedirect() {
  useEffect(() => {
    window.location.replace("/ideation")
  }, [])
  return null
}
