"use client"

// Mounts inside the EXISTING .modal-overlay/.modal shared shell via
// ConfirmDialog (grepped: ConfirmDialog already owns escape/backdrop-cancel,
// a busy-locked confirm button, and role="dialog" aria-modal="true" — the
// same shared wrapper ApproveModal/InviteModal build on) — no hand-rolled
// overlay/focus-trap.
//
// On confirm: calls artifactShareApi.join(token) exactly once (ConfirmDialog's
// own `busy` lock is what makes a rapid double-click result in exactly one
// network call — the button is disabled the instant the first click starts
// the request), fires the real Toast with the join-attribution copy, then
// forces a FULL reload of the real app. The reload is deliberate: the guest's
// session now has real company/workspace membership and must go through the
// normal WorkspaceProvider/OnboardingRequiredGuard/AppShell pipeline exactly
// like any other member — those providers must re-initialize fresh rather
// than trying to hot-swap this standalone viewer's state into them.
import { useState } from "react"
import { ConfirmDialog } from "./ConfirmDialog"
import { useNavigation } from "../../context/NavigationContext"
import { useAuth } from "../../lib/auth"
import { artifactShareApi } from "../../lib/artifactShareApi"

export function JoinConfirmModal({
  open,
  token,
  artifactId,
  sharerName,
  onClose,
}: {
  open: boolean
  token: string
  artifactId: number
  sharerName: string
  onClose: () => void
}) {
  const { showToast } = useNavigation()
  const auth = useAuth()
  const [busy, setBusy] = useState(false)

  const handleConfirm = async () => {
    if (busy) return
    setBusy(true)
    try {
      const result = await artifactShareApi.join(token)
      showToast(
        "Joined workspace",
        `You now have full access, shared by ${result.sharer_name}.`,
      )
      // Best-effort — the hard reload below re-derives auth state fresh
      // regardless of whether this resolves in time.
      void auth.refresh()
      window.location.assign(`/?prd=${artifactId}`)
    } catch {
      setBusy(false)
      showToast("Couldn't join", "Something went wrong — try again.")
    }
  }

  return (
    <ConfirmDialog
      open={open}
      title={`Join ${sharerName}'s workspace?`}
      body="You'll get full access to this workspace's briefs, chats, and tickets."
      confirmLabel="Join workspace"
      busyLabel="Joining…"
      busy={busy}
      onConfirm={() => void handleConfirm()}
      onCancel={onClose}
    />
  )
}
