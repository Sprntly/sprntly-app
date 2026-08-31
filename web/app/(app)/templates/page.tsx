"use client"

import { useEffect } from "react"

// Templates moved INTO Settings (2026-08-27) and this route stopped being its
// home; it lingered as a second door to the same screen, which is how the
// command palette ended up listing Templates twice. The screen now lives at
// `/settings?section=templates` only.
//
// This file is what removing the route costs: `/templates` has been handed out
// — bookmarks, a shared URL, and every chat answer the backend's app_map wrote
// while it still advertised the path — so the link has to keep landing.
//
// A HARD location.replace (not router.replace), for the reason `/ideation`'s
// redirect carries: on a cold first visit the app shell rewrites the URL to
// append ?company= during hydration, and an App-Router navigation racing that
// rewrite gets aborted, leaving a blank page. A full document navigation
// cannot be cancelled by a history rewrite.
export default function TemplatesRedirect() {
  useEffect(() => {
    window.location.replace("/settings?section=templates")
  }, [])
  return null
}
