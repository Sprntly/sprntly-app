/**
 * Referral code capture — a two-page handoff with nothing in between.
 *
 * A code arrives as `?ref=` on a link a friend shared, but the company it
 * credits is not created until several onboarding steps later, and a Google
 * sign-up leaves the site entirely and comes back on a fresh navigation. So the
 * code has to outlive the URL it arrived on.
 *
 * Its own module rather than a corner of `lib/api`: nothing here talks to the
 * network, and `lib/api` is mocked wholesale by a great many suites — adding
 * non-API exports there breaks every test whose mock does not list them.
 */

const REFERRAL_KEY = "sprntly:referral_code"

/** Stash `?ref=` if present. Safe to call on every mount. */
export function captureReferralCode(search?: string): void {
  if (typeof window === "undefined") return
  try {
    const code = new URLSearchParams(search ?? window.location.search).get("ref")
    if (code) window.localStorage.setItem(REFERRAL_KEY, code)
  } catch {
    /* private browsing / storage disabled — referral attribution is optional */
  }
}

/**
 * Read and clear the stashed code.
 *
 * Cleared ON READ so a second workspace created by the same person cannot
 * re-claim the same referral. The backend refuses a spent code anyway, but
 * doing it here means the retry never happens.
 */
export function takeReferralCode(): string | null {
  if (typeof window === "undefined") return null
  try {
    const code = window.localStorage.getItem(REFERRAL_KEY)
    if (code) window.localStorage.removeItem(REFERRAL_KEY)
    return code
  } catch {
    return null
  }
}
