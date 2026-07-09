/**
 * Pure TS mirror of backend `app.design_agent.url_slug.url_slugify` — same
 * casing/separator/fallback rules, hand-kept in lockstep (same convention as
 * `suggestedSlug` in onboard-helpers.ts, explicitly commented "Mirror of
 * backend slug rules"). Lets ShareMenu compute both cosmetic segments
 * client-side from data it already has, with no extra API round-trip.
 */
export function urlSlugify(name: string, fallback: string, maxLength = 40): string {
  let s = (name ?? "").trim().toLowerCase().replace(/[^a-z0-9-]+/g, "-")
  s = s.replace(/-+/g, "-").replace(/^-+|-+$/g, "")
  if (!s) return fallback
  s = s.slice(0, maxLength).replace(/-+$/, "")
  return s || fallback
}
