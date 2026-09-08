"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { useWorkspace } from "../../context/WorkspaceContext"
import { isOutOfCredits } from "../../lib/billingAccess"

/**
 * "You're out of credits" — before you find out by being refused.
 *
 * `PaymentRequiredPrompt` beside this already catches the 402: it is a modal,
 * it fires at the moment a generation is rejected, and it says the right
 * thing. What it cannot do is warn. Someone at zero opens the app, writes a
 * prompt, waits for a generation to start and only then learns it was never
 * going to run — with the work they typed still on screen and nothing having
 * happened. This is the standing version of the same fact, sitting quietly at
 * the bottom of the app rather than interrupting.
 *
 * TWO WAYS OUT, and the second is not decoration. Buying is owner/admin only
 * (`/v1/billing/checkout` refuses anyone else), so a member who reads "buy
 * credits" and clicks it lands on a screen that will not let them act. A
 * referral is the one route open to everyone: it costs nothing, and it pays
 * the company real credits when the friend's first invoice clears. Offering
 * only the button most readers cannot use would make the banner a dead end
 * for exactly the people most likely to hit it.
 *
 * DISMISSIBLE, and only for the session. The state it reports is not urgent
 * in the way a modal is — nothing is mid-failure — so someone reading a
 * document should be able to put it away. But it comes back on the next visit,
 * because the workspace is still stuck and a banner that never returns is a
 * banner that stopped telling the truth.
 */
export function OutOfCreditsBanner() {
  const router = useRouter()
  const { workspace } = useWorkspace()
  const [dismissed, setDismissed] = useState(false)

  if (dismissed) return null
  // Every "not now" case — payments off, unknown balance, a lapsed
  // subscription that has its own lock, an unmetered plan — lives in the
  // predicate, so this component has one question to ask.
  if (!isOutOfCredits(workspace)) return null

  return (
    <div className="credits-banner" role="status" data-testid="out-of-credits-banner">
      <span className="credits-banner-text">
        <strong>You&apos;re out of credits.</strong> Generations won&apos;t run
        until this workspace has more.
      </span>
      <span className="credits-banner-actions">
        <button
          type="button"
          className="btn btn-secondary credits-banner-btn"
          data-testid="out-of-credits-refer"
          onClick={() => router.push("/settings?section=billing")}
        >
          Invite a friend
        </button>
        <button
          type="button"
          className="btn primary credits-banner-btn"
          data-testid="out-of-credits-buy"
          onClick={() => router.push("/settings?section=billing")}
        >
          Buy credits
        </button>
        <button
          type="button"
          className="credits-banner-close"
          aria-label="Dismiss"
          data-testid="out-of-credits-dismiss"
          onClick={() => setDismissed(true)}
        >
          ×
        </button>
      </span>
    </div>
  )
}
