"use client"

import { useEffect } from "react"

/**
 * Fades out the pre-hydration loading splash (#app-splash, rendered in the
 * root layout) once the client app has mounted. Runs after hydration so there
 * is no server/client markup mismatch. Renders nothing.
 */
export default function SplashRemover() {
  useEffect(() => {
    const splash = document.getElementById("app-splash")
    if (!splash) return
    splash.classList.add("is-hidden")
    // Remove from the DOM after the fade so it never intercepts pointer events.
    const timer = window.setTimeout(() => splash.remove(), 250)
    return () => window.clearTimeout(timer)
  }, [])

  return null
}
