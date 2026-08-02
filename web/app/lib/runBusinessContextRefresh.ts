// Async Business Context refresh.
//
// POST /v1/company/business-context/refresh kicks off a background job on
// the backend (companies.business_context_refresh_status/error — a
// singleton per tenant, see app/db/business_context_refresh.py) and returns
// immediately; this polls GET .../refresh-status with the shared
// visibility-aware `pollUntil` (lib/poll.ts) until it leaves 'generating'.
//
// Unlike runAskGeneration/runPrdGeneration, there's no job id to persist
// across a remount: the status is keyed on the caller's own company via
// require_company, so re-polling after a remount just re-reads the same
// row — nothing to resume by id, nothing to clean up on unmount either.

import { businessContextApi } from "./api"
import type { BusinessContextRefreshStatus } from "./api"
import { pollUntil } from "./poll"

// Wall-clock budget, matching the server's own orphan-sweep window
// (ORPHAN_BUSINESS_CONTEXT_REFRESH_AFTER_MINUTES) — if the job hasn't
// reported done/error by the time the SERVER would consider it orphaned,
// there's nothing more the client gains by continuing to wait.
const MAX_MS = 15 * 60 * 1000
const POLL_INTERVAL_MS = 3000

export class BusinessContextRefreshFailedError extends Error {}
export class BusinessContextRefreshTimeoutError extends BusinessContextRefreshFailedError {}

/**
 * POST refresh, then poll refresh-status until it leaves 'generating'.
 * Resolves (void) on 'done'. Throws on a backend-reported error or a
 * client-side wall-clock timeout — the caller is responsible for reloading
 * the doc after this resolves (the status endpoint carries no doc content).
 */
export async function runBusinessContextRefresh(): Promise<void> {
  await businessContextApi.refresh()
  const final = await pollUntil<BusinessContextRefreshStatus>({
    fetchStatus: () => businessContextApi.refreshStatus(),
    isDone: (v) => v.status !== "generating",
    maxMs: MAX_MS,
    intervalMs: POLL_INTERVAL_MS,
  })
  if (final.status === "error") {
    throw new BusinessContextRefreshFailedError(
      final.error || "Business context refresh failed",
    )
  }
  if (final.status === "generating") {
    // Wall-clock budget exhausted while still generating — the server job
    // may yet finish (its own orphan window is the same length), it's just
    // that this poll gave up watching. Distinguish from a real failure so
    // the caller can word it as "still running" rather than "failed".
    throw new BusinessContextRefreshTimeoutError(
      "Business context refresh is taking longer than expected — check back shortly",
    )
  }
  // 'done' — nothing more to do; the caller reloads the doc.
}
