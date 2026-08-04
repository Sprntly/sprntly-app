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
import {
  CONNECTOR_CATALOG,
  connectableCatalog,
  UPLOADS_PROVIDER_ID,
} from "../connectorsCatalog"
import type { ConnectorCategoryRow } from "../../types/content"

/**
 * The Analytics category key.
 *
 * Nothing in onboarding is mandatory — Continue is never gated on a connector
 * — but a LIVE connection in this category is what decides whether the
 * post-review define-metrics sub-flow runs (see hasLiveAnalyticsConnection).
 */
export const REQUIRED_CATEGORY_KEY = "analytics"

/**
 * The categories the v6 onboarding wizard walks through, in screenshot-spec
 * order (matches CONNECTOR_CATALOG order). Revenue is the one deliberate
 * omission — it stays in Settings → Connectors.
 */
export const ONBOARDING_CONNECTOR_CATEGORIES: readonly string[] = [
  "analytics",
  "voice",
  // Research is a wizard category as well as a Settings one (product decision
  // 2026-08-02): a PM arrives with existing research long before they can
  // connect a repository, and it's real brief evidence, so we ask for it during
  // onboarding. It survives wizardCategories' empty-category drop via the
  // catalog's `keepWhenEmpty` flag — see connectableCatalog.
  "research",
  "crm",
  "pm",
  "monitoring",
  "design",
  "code",
  "comms",
  // Company documentation joined the wizard 2026-08-03. Confluence and Google
  // Docs are both OAuth-wired, and a team wiki is the densest product context
  // we can read on day one — leaving it Settings-only meant most PMs never
  // wired it at all. It renders LAST (catalog order, after Communications)
  // rather than beside the evidence shelves: it is context, not customer
  // signal, so it should not push the analytics/voice categories down.
  "docs",
]

/**
 * Categories surfaced in the onboarding wizard, in catalog order, limited to
 * ONBOARDING_CONNECTOR_CATEGORIES.
 *
 * Mirrors Settings → Connectors: only connectors we actually support today
 * (OAuth or API-key wired, per `isConnectableConnector`) are shown, and any
 * category that ends up with no supported connector is hidden entirely — so
 * we never ask the PM to "connect" something they can't yet use (e.g. the
 * whole Analytics category today, or MS Teams under Communication).
 *
 * `alsoKeepIds` (e.g. providers with a live connection) are never hidden even
 * if not yet wired, and a category kept alive by such a provider is retained.
 *
 * One provider is dropped unconditionally: `uploads` under Company
 * documentation. It is the user's own named document sources, not a
 * third-party integration — it has no auth flow for the wizard's connect modal
 * to open, and the "Add a document source" picker that drives it is rendered
 * only by Settings → Connectors. Settings excludes it from its connector rows
 * for exactly the same reason; onboarding would otherwise show a tile that
 * opens an empty modal.
 */
export function wizardCategories(
  alsoKeepIds: ReadonlySet<string> = new Set(),
): ConnectorCategoryRow[] {
  return connectableCatalog(alsoKeepIds)
    .filter((c) => ONBOARDING_CONNECTOR_CATEGORIES.includes(c.key))
    .map((c) => ({
      ...c,
      items: c.items.filter((i) => i.id !== UPLOADS_PROVIDER_ID),
    }))
    // Re-run the empty-category drop: connectableCatalog ran it before we
    // removed `uploads`, so a category carried solely by that provider would
    // otherwise survive as an empty shelf.
    .filter((c) => c.items.length > 0 || c.keepWhenEmpty === true)
}

/** Connector ids belonging to the Analytics category. */
export function requiredCategoryIds(): string[] {
  const cat = CONNECTOR_CATALOG.find((c) => c.key === REQUIRED_CATEGORY_KEY)
  return cat ? cat.items.map((i) => i.id) : []
}

/**
 * Is there a LIVE analytics connection (not merely "planned this session")?
 *
 * Gates the post-review define-metrics sub-flow: that flow exists to map each
 * metric onto real analytics events, so with no analytics connector it has
 * nothing to detect and we finish onboarding straight from Review instead.
 *
 * Prefers the backend's `types` (source of truth, mirrors
 * backend/app/connectors/catalog.py) and falls back to the local catalog's
 * Analytics ids for older payloads that predate the field.
 */
export function hasLiveAnalyticsConnection(
  connections: readonly { provider: string; status: string; types?: string[] }[],
): boolean {
  const analyticsIds = new Set(requiredCategoryIds())
  return connections.some(
    (c) =>
      c.status === "active" &&
      (c.types?.includes(REQUIRED_CATEGORY_KEY) || analyticsIds.has(c.provider)),
  )
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
