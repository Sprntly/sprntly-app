"use client"

// A one-click "join this workspace" prompt, mounted inside GuestArtifactViewer
// above the guest's read-only content. "Not now" collapses it to a slim
// footer for the rest of the CURRENT mount only (component-local state, not
// persisted across reloads) — the spec asks only for within-session dismissal.
// Uses inline styles against existing tokens rather than new CSS classes, per
// the ticket's own reuse audit (no globals.css addition declared for Group B).
import { useState } from "react"
import { IconSparkle } from "./app-icons"

export function JoinWorkspaceBanner({
  owningCompanyName,
  onJoin,
}: {
  owningCompanyName: string
  onJoin: () => void
}) {
  const [dismissed, setDismissed] = useState(false)

  if (dismissed) {
    return (
      <div
        data-testid="join-banner-footer"
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 10,
          padding: "8px 16px",
          background: "var(--accent-soft)",
          borderTop: "1px solid var(--accent-2)",
          fontSize: 12,
        }}
      >
        <span>Want full access to {owningCompanyName}&apos;s workspace?</span>
        <button type="button" className="btn btn-ghost btn-sm" onClick={onJoin}>
          Join
        </button>
      </div>
    )
  }

  return (
    <div
      data-testid="join-banner"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "12px 16px",
        background: "var(--accent-soft)",
        border: "1px solid var(--accent-2)",
        borderRadius: "var(--radius-sm)",
        margin: "0 16px 12px",
      }}
    >
      <IconSparkle size={18} />
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: "var(--ink)" }}>
          Join {owningCompanyName}&apos;s workspace
        </div>
        <div style={{ fontSize: 12, color: "var(--ink-2)", marginTop: 2 }}>
          Get full access to this workspace&apos;s briefs, chats, and tickets.
        </div>
      </div>
      <button type="button" className="btn btn-ghost btn-sm" onClick={() => setDismissed(true)}>
        Not now
      </button>
      <button type="button" className="btn btn-primary btn-sm" onClick={onJoin}>
        Join
      </button>
    </div>
  )
}
