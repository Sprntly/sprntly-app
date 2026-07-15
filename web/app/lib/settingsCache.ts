/**
 * Sign-out reset registry for the settings panes' in-memory SWR caches.
 *
 * Several settings panes (Connectors, MCP, Team, Admin) keep a module-scoped
 * cache of their last-loaded data so that switching settings tabs and coming
 * back renders the previous state INSTANTLY and revalidates in the background,
 * instead of showing a loading spinner every visit. Those caches live in the
 * pane modules (module state survives the component remounting), but they must
 * be cleared on sign-out so a DIFFERENT user logging in on the same browser
 * never flashes the previous account's connectors/tokens/team before the first
 * revalidation lands.
 *
 * Each pane registers a reset callback here at module load; `resetSettingsCaches`
 * runs them all. This registry lives in the lib layer so the auth provider can
 * call it without importing (and bundling) any settings component code.
 */
const _resetters = new Set<() => void>()

/** Register a settings-pane cache reset, run on sign-out. Idempotent per fn. */
export function registerSettingsCacheReset(fn: () => void): void {
  _resetters.add(fn)
}

/** Clear every registered settings-pane cache (called on sign-out). */
export function resetSettingsCaches(): void {
  for (const fn of _resetters) {
    try {
      fn()
    } catch {
      // A single pane's reset failing must never block sign-out cleanup.
    }
  }
}
