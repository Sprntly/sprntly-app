/**
 * Memoized prefetch for the two LLM drafts the closing onboarding screens
 * display, so generation runs in the BACKGROUND while the user is still on
 * earlier steps instead of starting when the display screen mounts:
 *
 *   - business-context prose ("Here's what we learned", step 9) — kicked from
 *     the invite step's mount (every draft input — company, product, metrics,
 *     team, strategy, decisions — is saved by then; invites don't affect it),
 *   - per-metric definitions (define-metrics sub-flow) — kicked from the
 *     review step's mount, drafting while the user reads/edits the context.
 *
 * Module-level singletons (not React state) so the in-flight promise survives
 * step navigation; the DISPLAY screens call the same function and either join
 * the in-flight request or get the resolved value instantly. Keyed by
 * workspace (and, for definitions, the picked-metric set) so a changed input
 * re-drafts rather than serving a stale cache; a failed request clears its
 * slot so the display screen's call can retry.
 */
import { onboardingApi } from "../api"
import type { MetricDefinition } from "./types"

let bcSlot: { key: string; promise: Promise<string> } | null = null

export function prefetchBusinessContextDraft(workspaceId: string): Promise<string> {
  if (bcSlot && bcSlot.key !== workspaceId) bcSlot = null
  if (!bcSlot) {
    const promise = onboardingApi.draftBusinessContext().then((r) => r.draft)
    promise.catch(() => {
      if (bcSlot?.promise === promise) bcSlot = null
    })
    bcSlot = { key: workspaceId, promise }
  }
  return bcSlot.promise
}

let mdSlot: { key: string; promise: Promise<MetricDefinition[]> } | null = null

function metricsKey(workspaceId: string, metrics: string[]): string {
  return (
    workspaceId +
    "::" +
    metrics
      .map((m) => m.trim().toLowerCase())
      .filter(Boolean)
      .sort()
      .join("|")
  )
}

export function prefetchMetricDefinitions(
  workspaceId: string,
  metrics: string[],
): Promise<MetricDefinition[]> {
  const key = metricsKey(workspaceId, metrics)
  if (mdSlot && mdSlot.key !== key) mdSlot = null
  if (!mdSlot) {
    const promise = onboardingApi.draftMetricDefinitions(metrics).then((r) =>
      r.definitions.map((d) => ({
        metric: d.metric,
        definition: d.definition ?? "",
        mapping: d.mapping ?? "",
        baseline: d.baseline ?? null,
      })),
    )
    promise.catch(() => {
      if (mdSlot?.promise === promise) mdSlot = null
    })
    mdSlot = { key, promise }
  }
  return mdSlot.promise
}

/** Test hook — the singletons otherwise leak between cases in one module. */
export function _resetDraftPrefetchForTests(): void {
  bcSlot = null
  mdSlot = null
}
