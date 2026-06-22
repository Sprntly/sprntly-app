/**
 * Sequential connector-wizard logic for design-v4 onboarding page 06.
 *
 * Page 06 walks the PM through connector categories one at a time —
 * "each one opens the next" — with a Skip / Done·next control per
 * category. Source of truth for the categories + connectors is
 * CONNECTOR_CATALOG (lib/connectorsCatalog.ts) so the wizard tracks
 * Settings automatically.
 *
 * Pure state helpers (no React) so they're unit-testable under the
 * node/View test pattern.
 */
import { CONNECTOR_CATALOG, connectableCatalog } from "../connectorsCatalog"
import type { ConnectorCategoryRow } from "../../types/content"

/** The category id that gates Continue — at least one must be connected. */
export const REQUIRED_CATEGORY_KEY = "analytics"

/**
 * Categories surfaced in the onboarding wizard, in catalog order.
 *
 * Mirrors Settings → Connectors: only connectors we actually support today
 * (OAuth or API-key wired, per `isConnectableConnector`) are shown, and any
 * category that ends up with no supported connector is hidden entirely — so
 * we never ask the PM to "connect" something they can't yet use (e.g. the
 * whole Analytics category today, or MS Teams under Communication).
 *
 * `alsoKeepIds` (e.g. providers with a live connection) are never hidden even
 * if not yet wired, and a category kept alive by such a provider is retained.
 */
export function wizardCategories(
  alsoKeepIds: ReadonlySet<string> = new Set(),
): ConnectorCategoryRow[] {
  return connectableCatalog(alsoKeepIds)
}

/** Connector ids belonging to the required (Analytics) category. */
export function requiredCategoryIds(): string[] {
  const cat = CONNECTOR_CATALOG.find((c) => c.key === REQUIRED_CATEGORY_KEY)
  return cat ? cat.items.map((i) => i.id) : []
}

/**
 * Has the PM satisfied the hard requirement (≥1 Analytics connector,
 * whether live or selected-this-session)?
 */
export function hasRequiredConnector(selected: ReadonlySet<string>): boolean {
  return requiredCategoryIds().some((id) => selected.has(id))
}

/** Clamp a category index into [0, lastCategory]. */
export function clampStep(step: number): number {
  const last = CONNECTOR_CATALOG.length - 1
  if (step < 0) return 0
  if (step > last) return last
  return step
}

/** True when `step` points at the final category. */
export function isLastCategory(step: number): boolean {
  return step >= CONNECTOR_CATALOG.length - 1
}

/** Next category index (clamped) — used by both Skip and Done·next. */
export function nextStep(step: number): number {
  return clampStep(step + 1)
}

/** Title for a category, decorated with its required/sub label. */
export function categoryTitle(cat: ConnectorCategoryRow): string {
  if (cat.subLabel === "required") return `${cat.title} (at least one required)`
  return cat.subLabel ? `${cat.title} · ${cat.subLabel}` : cat.title
}

/* ── Accordion helpers (sequential unlock) ──────────────────────────
   The design-v4 page renders ALL categories as a vertical accordion:
   a category unlocks only once the previous one is done/skipped, and
   done categories stay re-openable. These pure helpers carry that
   state so the component stays thin. */

/** Mark a category index done/skipped (returns a new set). */
export function markCategoryDone(
  done: ReadonlySet<number>,
  index: number,
): Set<number> {
  const next = new Set(done)
  next.add(index)
  return next
}

/**
 * A category is unlocked when it's the first one, is itself already
 * done (done categories remain re-openable), or the previous category
 * is done/skipped.
 */
export function isCategoryUnlocked(
  done: ReadonlySet<number>,
  index: number,
): boolean {
  return index === 0 || done.has(index) || done.has(index - 1)
}

/**
 * First not-yet-done category index — the accordion section to open
 * after completing one — or null once every category is done.
 */
export function firstIncompleteCategory(
  done: ReadonlySet<number>,
  count: number,
): number | null {
  for (let i = 0; i < count; i++) {
    if (!done.has(i)) return i
  }
  return null
}

/** Toggle a connector id in a selection set (returns a new set). */
export function toggleSelection(
  selected: ReadonlySet<string>,
  id: string,
): Set<string> {
  const next = new Set(selected)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  return next
}
