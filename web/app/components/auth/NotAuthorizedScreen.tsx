// The flat "not authorized" screen for every artifact-share denial. Deliberately
// takes ONLY a `reason` — never a title/artifact id — so an invalid/expired/
// blocked token can never leak the shared artifact's identity to a viewer this
// screen is actively denying (AC3: no artifact title anywhere in the DOM).
import Link from "next/link"
import { AuthShell } from "./AuthShell"
import { InfoCircle } from "./icons"

export type NotAuthorizedReason = "different_company" | "domain_mismatch" | "invalid_token"

const COPY: Record<NotAuthorizedReason, { heading: React.ReactNode; body: string; hint: string }> = {
  different_company: {
    heading: (
      <>
        This isn&apos;t for <em>your team.</em>
      </>
    ),
    body: "The shared document belongs to a different company than the one your account already belongs to.",
    hint: "Ask the person who shared it to invite you to their company instead, or use an account that isn't already part of another workspace.",
  },
  domain_mismatch: {
    heading: (
      <>
        Wrong <em>email domain.</em>
      </>
    ),
    body: "This link is restricted to a specific company's email domain, and your account doesn't match it.",
    hint: "Sign in or sign up with your work email at the company this was shared from, or ask the sharer to re-send it.",
  },
  invalid_token: {
    heading: (
      <>
        This link isn&apos;t <em>valid.</em>
      </>
    ),
    body: "This shared-artifact link is invalid or has expired.",
    hint: "Ask the person who shared it for a fresh link.",
  },
}

export function NotAuthorizedScreen({ reason }: { reason: NotAuthorizedReason }) {
  const copy = COPY[reason]
  return (
    <AuthShell tag="Not authorized" cardClassName="auth-card-center">
      <div className="verify-icon verify-icon--danger" aria-hidden>
        <InfoCircle width={26} height={26} />
      </div>
      <div className="auth-h">{copy.heading}</div>
      <div className="auth-sub">{copy.body}</div>
      <div className="spam-note">
        <InfoCircle width={14} height={14} />
        <div>{copy.hint}</div>
      </div>
      <div className="auth-foot">
        <Link href="/">Continue to your workspace</Link>
      </div>
    </AuthShell>
  )
}
