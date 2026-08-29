"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { onPaymentRequired, type PaymentRequired } from "../../lib/api"

/**
 * What a 402 looks like to a person.
 *
 * Mounted once at the app shell. Every billable route — there are eight — can
 * return 402, and until now none of them said so: the structured body the
 * backend sends (`subscription_inactive` vs `insufficient_credits`, with the
 * numbers) was thrown away and each surface rendered its own generic failure.
 * A customer clicked Generate, something went wrong, and nothing anywhere
 * mentioned billing.
 *
 * The two reasons need different words because they need different actions:
 * one is "your card lapsed, choose a plan", the other is "this costs more than
 * you have, top up". Telling them apart is the whole reason the backend
 * bothered to send `error` in the first place.
 */
export function PaymentRequiredPrompt() {
  const router = useRouter()
  const [prompt, setPrompt] = useState<PaymentRequired | null>(null)

  useEffect(() => onPaymentRequired(setPrompt), [])

  if (!prompt) return null

  const lapsed = prompt.reason === "subscription_inactive"
  const title = lapsed ? "Your subscription has ended" : "You're out of credits"
  const cta = lapsed ? "Choose a plan" : "Buy more credits"

  return (
    <div className="pay-req-backdrop" role="dialog" aria-modal="true" aria-label={title}>
      <div className="pay-req">
        <h2 className="pay-req-title">{title}</h2>
        <p className="pay-req-body">{prompt.message}</p>
        {!lapsed && prompt.needed != null && prompt.balance != null && (
          <p className="pay-req-nums">
            This costs <strong>{prompt.needed.toLocaleString()}</strong> credits and you
            have <strong>{prompt.balance.toLocaleString()}</strong>.
          </p>
        )}
        <div className="pay-req-actions">
          {/* Dismiss first in the DOM but second visually: the primary action is
              fixing it, and a modal with no way out is a trap. */}
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => setPrompt(null)}
            data-testid="pay-req-dismiss"
          >
            Not now
          </button>
          <button
            type="button"
            className="btn primary"
            data-testid="pay-req-cta"
            onClick={() => {
              setPrompt(null)
              router.push("/settings?section=billing")
            }}
          >
            {cta}
          </button>
        </div>
      </div>
    </div>
  )
}
