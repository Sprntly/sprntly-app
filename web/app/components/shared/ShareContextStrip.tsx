"use client"

// The `.welcome-banner` recipe (already used on sign-up step 2's "Account
// created" strip), reused verbatim for every "you're here because someone
// shared X with you" moment: sign-up step 1, verify-email, and the guest
// drawer header. One component, three call sites — never a bespoke banner
// per screen.
export type ShareContextStripKind = "sign-up" | "verify" | "drawer"

const COPY: Record<ShareContextStripKind, (sharerName: string) => string> = {
  "sign-up": (sharerName) => `Create an account to view it, shared by ${sharerName}.`,
  verify: (sharerName) => `Verify your email to view it, shared by ${sharerName}.`,
  drawer: (sharerName) => `Shared by ${sharerName} · read-only`,
}

export function ShareContextStrip({
  kind,
  title,
  sharerName,
}: {
  kind: ShareContextStripKind
  title: string
  sharerName: string
}) {
  return (
    <div className="welcome-banner welcome-banner--share" data-testid="share-context-strip">
      <span className="wb-icon" aria-hidden>
        ↗
      </span>
      <div>
        <div className="t">{title}</div>
        <div className="s">{COPY[kind](sharerName)}</div>
      </div>
    </div>
  )
}
