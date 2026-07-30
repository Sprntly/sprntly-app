"use client"

import { useEffect, useRef, useState } from "react"

// Drawer timings. MUST match --cpanel-in-ms / --cpanel-out-ms in globals.css:
// the CSS animates the slide, these drive when the transform class comes off
// (in) and when the panel actually unmounts (out).
export const CPANEL_IN_MS = 260
export const CPANEL_OUT_MS = 200

/**
 * Drives a cpanel drawer's mount lifecycle so it can animate BOTH ways.
 *
 * Flipping `open` to false used to unmount the panel on the spot — it vanished
 * while the main column was still easing its padding shut. Here the panel stays
 * mounted for the length of the exit animation and only then comes off the tree.
 *
 * Phases: "in" and "out" are the two animating states and each carries the
 * matching class; "idle" is the settled, open panel with NO transform on it
 * (see the .cpanel--in comment in globals.css for why that matters).
 *
 * Used by ContentPanel — the ONE slide-over, which every artifact (evidence,
 * PRD, tickets, reports) opens into as a tab rather than bringing its own drawer.
 */
export function useCpanelPhase(open: boolean) {
  const [mounted, setMounted] = useState(open)
  const [phase, setPhase] = useState<"in" | "idle" | "out">(open ? "in" : "idle")
  // Read inside the effect without re-running it: the effect reacts to `open`
  // only, so a mount/unmount it performs itself can't restart the animation.
  const mountedRef = useRef(mounted)
  mountedRef.current = mounted

  useEffect(() => {
    if (open) {
      setMounted(true)
      setPhase("in")
      const t = setTimeout(() => setPhase("idle"), CPANEL_IN_MS)
      return () => clearTimeout(t)
    }
    // Never mounted (first render with the panel closed) — nothing to play out.
    if (!mountedRef.current) return
    setPhase("out")
    const t = setTimeout(() => {
      setMounted(false)
      setPhase("idle")
    }, CPANEL_OUT_MS)
    return () => clearTimeout(t)
  }, [open])

  return { mounted, phase }
}
