"use client"

import { useEffect } from "react"

/** Never hold the splash longer than this, whatever else is outstanding. A
 *  loading screen that outlives the thing it is covering is worse than a brief
 *  unstyled flash, so every wait below is a race against this. */
const MAX_HOLD_MS = 2500

/** Matches the `#app-splash` opacity transition in the root layout's critical
 *  CSS. The node is removed only after the fade so it never sits invisible
 *  over the app swallowing clicks. */
const FADE_MS = 250

/**
 * Fades out the pre-hydration loading splash (`#app-splash`, rendered in the
 * root layout) once the app is actually ready to be looked at.
 *
 * It used to fade on the first effect after hydration, which is EARLIER than
 * the app is worth showing: React had mounted, but the webfonts were still
 * loading and the shell had not painted, so the splash cleared onto a bare or
 * re-flowing page and the reader watched the app assemble itself. The mark
 * animating in the splash made the gap obvious — it stopped while the page was
 * still visibly arriving.
 *
 * So the fade now waits for three things, whichever finishes last:
 *
 *   1. hydration (this effect running at all),
 *   2. `document.fonts.ready` — the app is Geist throughout, and clearing
 *      before the face loads guarantees a visible reflow, and
 *   3. two animation frames after that, so the first painted frame of the real
 *      shell has actually been composited.
 *
 * All of it races `MAX_HOLD_MS`. A browser without the Font Loading API, a font
 * CDN that never answers, a hung frame — none of them can strand the splash.
 *
 * The mark and the label live in one element and fade as one, so they leave
 * together rather than the logo blinking out ahead of the text.
 */
export default function SplashRemover() {
  useEffect(() => {
    const splash = document.getElementById("app-splash")
    if (!splash) return

    let done = false
    let removeTimer = 0

    const dismiss = () => {
      if (done) return
      done = true
      splash.classList.add("is-hidden")
      removeTimer = window.setTimeout(() => splash.remove(), FADE_MS)
    }

    // The backstop, armed first so nothing below can outlive it.
    const cap = window.setTimeout(dismiss, MAX_HOLD_MS)

    const afterPaint = () => {
      // Two frames: the first schedules the shell's paint, the second runs
      // once it has been composited.
      requestAnimationFrame(() => requestAnimationFrame(dismiss))
    }

    const fonts = (document as Document & { fonts?: FontFaceSet }).fonts
    if (fonts?.ready) {
      fonts.ready.then(afterPaint).catch(afterPaint)
    } else {
      afterPaint()
    }

    return () => {
      window.clearTimeout(cap)
      window.clearTimeout(removeTimer)
    }
  }, [])

  return null
}
