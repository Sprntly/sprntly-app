// The flat "not authorized" screen for every artifact-share denial. Deliberately
// takes ONLY a `reason` — never a title/artifact id — so an invalid/expired/
// blocked token can never leak the shared artifact's identity to a viewer this
// screen is actively denying (AC3: no artifact title anywhere in the DOM).
//
// "domain_mismatch" is retired (revision 2026-08-02): a mismatched-domain
// signup is now blocked at the FORM level, before any account/session exists
// to even reach this screen — see validateShareDomainEmail in
// lib/auth-validation.ts. resolve_share_access no longer ever returns that
// reason (see its docstring), so there is nothing left to route here under
// it. A zero-membership caller now reads as "different_company" — see that
// copy's wording below, deliberately worded to cover both "your account
// already belongs elsewhere" AND "your account belongs nowhere yet".
import Link from "next/link"
import { AuthShell } from "./AuthShell"
import { InfoCircle } from "./icons"

export type NotAuthorizedReason = "different_company" | "invalid_token"

const COPY: Record<NotAuthorizedReason, { heading: React.ReactNode; body: string; hint: string }> = {
  different_company: {
    heading: (
      <>
        This isn&apos;t for <em>your team.</em>
      </>
    ),
    body: "The shared document belongs to a company your account isn't a member of.",
    hint: "Ask the person who shared it to invite you to their company instead, or use an account that's already a member.",
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

export function NotAuthorizedScreen({
  reason,
  continueHref = "/",
}: {
  reason: NotAuthorizedReason
  /** The signed-in user's own real home — computed from THEIR account state
   *  (never the artifact), so it's safe to add without violating the
   *  no-title/no-artifact-id invariant above. Defaults to "/" so this
   *  component still renders correctly with no behavior change for any
   *  existing caller that doesn't pass it. */
  continueHref?: string
}) {
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
        <Link href={continueHref}>Continue to your workspace</Link>
      </div>
    </AuthShell>
  )
}
