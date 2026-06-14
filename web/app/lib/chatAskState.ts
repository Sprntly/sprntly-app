// Per-tab in-flight ("asking") + busy state for the parallel-chat ChatScreen.
//
// ChatScreen supports multiple parallel chat TABS that must be able to have
// their OWN ask in flight CONCURRENTLY: sending in tab A must not block sending
// in tab B, their `askApi.ask` calls run at the same time, and each reply routes
// to its own tab. This module is the single source of truth for that concurrency
// contract, extracted (like chatPersistence) so the exact shipped logic can be
// unit-tested without a heavy full-component mount.
//
// Two pieces of per-tab state:
//   - askingTabs (a Set, held in a ref by the component): the authoritative
//     double-send guard. A tab present in the set has an ask in flight and may
//     NOT start a second one; other tabs are unaffected (so they send in parallel).
//   - busyTabs (a Set, held in React state by the component): drives the
//     composer's busy/disabled + "thinking" indicator. The composer reads it for
//     the ACTIVE tab only (`isComposerBusy`), so switching to an idle tab shows an
//     enabled composer even while another tab is still loading.
//
// The component wires `runTabAsk` into `submitAsk` (after it has resolved
// `targetTabId` and appended the user turn). `runTabAsk` performs the per-tab
// guard, marks asking/busy, runs the injected ask, routes the result to the
// captured `targetTabId`, and clears asking/busy in `finally` — safe even if the
// tab was closed mid-flight.

/** A mutable set of tab ids with an ask currently in flight (the ref's `.current`). */
export type AskingTabs = Set<string>

/** Whether `tabId` currently has an ask in flight. */
export function isTabAsking(asking: AskingTabs, tabId: string): boolean {
  return asking.has(tabId)
}

/**
 * Derive the composer's busy/disabled state from the ACTIVE tab only. Another
 * tab being mid-ask must NOT disable this composer.
 */
export function isComposerBusy(busyTabs: ReadonlySet<string>, activeTabId: string | null): boolean {
  return activeTabId != null && busyTabs.has(activeTabId)
}

/** Immutable Set add — for `setBusyTabs(prev => addToSet(prev, id))`. */
export function addToSet<T>(prev: ReadonlySet<T>, value: T): Set<T> {
  return new Set(prev).add(value)
}

/**
 * Immutable Set delete — for `setBusyTabs(prev => removeFromSet(prev, id))`.
 * Returns the same reference if `value` is absent so React can bail on the update.
 */
export function removeFromSet<T>(prev: ReadonlySet<T>, value: T): ReadonlySet<T> {
  if (!prev.has(value)) return prev
  const next = new Set(prev)
  next.delete(value)
  return next
}

export type RunTabAskDeps<TResult> = {
  /** The resolved target tab for this ask (captured up-front by the component). */
  targetTabId: string
  /** The asking-tabs ref's `.current` Set (mutated in place). */
  asking: AskingTabs
  /** Mark/clear the tab's busy state (component's `setBusyTabs` with immutable Set updates). */
  setBusy: (updater: (prev: ReadonlySet<string>) => ReadonlySet<string>) => void
  /** The actual ask call (e.g. `() => askApi.ask(query, company)`). Runs concurrently per tab. */
  ask: () => Promise<TResult>
  /** Route a successful reply to `targetTabId`'s thread/persistence. */
  onResult: (targetTabId: string, result: TResult) => void
  /** Route an error to `targetTabId`'s thread/persistence. */
  onError: (targetTabId: string, error: unknown) => void
}

/**
 * Run one tab's ask with the per-tab in-flight guard.
 *
 * Returns `false` WITHOUT running anything if the target tab already has an ask
 * in flight (the authoritative double-send guard). Otherwise marks the tab
 * asking + busy, runs `ask()` (which can overlap with other tabs' asks), routes
 * the resolved result/error to the captured `targetTabId`, and clears the
 * asking/busy state in `finally`. Returns `true` once the ask has started.
 *
 * The clear is safe even if the tab was closed mid-flight: Set.delete on a
 * missing key is a no-op and `removeFromSet` bails when the key is absent.
 */
export async function runTabAsk<TResult>(deps: RunTabAskDeps<TResult>): Promise<boolean> {
  const { targetTabId, asking, setBusy, ask, onResult, onError } = deps
  if (asking.has(targetTabId)) return false
  asking.add(targetTabId)
  setBusy((prev) => addToSet(prev, targetTabId))
  try {
    const result = await ask()
    onResult(targetTabId, result)
  } catch (e) {
    onError(targetTabId, e)
  } finally {
    asking.delete(targetTabId)
    setBusy((prev) => removeFromSet(prev, targetTabId))
  }
  return true
}
