"use client"

// MentionPickerOverlay — a project-local, fixed-position overlay that floats the
// group @-mention picker just ABOVE the shared composer's textarea. Anchored via
// the composer ref only (getBoundingClientRect); touches NO shared composer
// code. Re-measures on open, resize, and scroll so it tracks the composer.
import { useLayoutEffect, useState, type ReactNode, type RefObject } from "react"

export function MentionPickerOverlay({
  open,
  node,
  anchorRef,
}: {
  open: boolean
  node: ReactNode
  anchorRef: RefObject<HTMLTextAreaElement | null>
}) {
  const [pos, setPos] = useState<{ left: number; bottom: number; width: number } | null>(null)

  useLayoutEffect(() => {
    if (!open) {
      setPos(null)
      return
    }
    const measure = () => {
      const el = anchorRef.current
      if (!el) return
      const r = el.getBoundingClientRect()
      // Open UPWARD from the composer: the overlay's bottom sits just above the
      // textarea's top edge, left-aligned and matched to its width.
      setPos({ left: r.left, bottom: window.innerHeight - r.top + 6, width: r.width })
    }
    measure()
    window.addEventListener("resize", measure)
    window.addEventListener("scroll", measure, true)
    return () => {
      window.removeEventListener("resize", measure)
      window.removeEventListener("scroll", measure, true)
    }
  }, [open, anchorRef])

  if (!open || !node || !pos) return null
  return (
    <div
      style={{ position: "fixed", left: pos.left, bottom: pos.bottom, width: pos.width, zIndex: 60 }}
      data-testid="gc-mention-overlay"
    >
      {node}
    </div>
  )
}
