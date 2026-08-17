"use client"

import { useEffect } from "react"

/**
 * Reliable Escape-to-close for a modal dialog.
 *
 * A React `onKeyDown` on the dialog panel is NOT a reliable source for
 * Escape — the keydown can land somewhere the synthetic handler never sees
 * it (e.g. focus outside the panel subtree, or the event's default handling
 * elsewhere intercepting it first). This hook instead attaches a real
 * `document`-level `keydown` listener for the lifetime the modal is open,
 * so Escape closes the modal regardless of where focus actually is.
 *
 * Shared by every rail modal that needs Escape-to-close
 * (`ArtifactsModal`/`CreateProjectModal`/`MemoryModal`) rather than each one
 * re-implementing its own panel-level branch (reuse-over-invention).
 *
 * Scoped to the open lifetime only: the listener is added when `open`
 * becomes true and removed on close/unmount — no leaked listener once the
 * modal is gone.
 */
export function useEscapeToClose(open: boolean, onClose: () => void): void {
  useEffect(() => {
    if (!open) return
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return
      e.preventDefault()
      onClose()
    }
    document.addEventListener("keydown", handleKeyDown)
    return () => document.removeEventListener("keydown", handleKeyDown)
  }, [open, onClose])
}
