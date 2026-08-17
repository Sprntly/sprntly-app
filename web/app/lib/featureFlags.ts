/**
 * Build-time feature flags — cosmetic gates only, NOT security boundaries.
 *
 * `NEXT_PUBLIC_*` vars are inlined at `next build` (see web/next.config.ts's
 * `output: "export"` static export), so every flag here is per-build:
 * flipping it requires a rebuild, not just a redeploy or a runtime toggle.
 * The real dark-guarantee for a gated feature lives on the backend, which
 * reads its own env var at request time and 404s when off.
 */

/**
 * Whether the Projects feature's UI surfaces (nav entry, `/projects` route)
 * should render. Mirrors the backend's `PROJECTS_ENABLED` truthy convention
 * so the two flags agree on what "on" means, even though they gate
 * independently (this one only hides UI; the backend one is the security
 * boundary).
 */
export function projectsEnabled(): boolean {
  const v = (process.env.NEXT_PUBLIC_PROJECTS_ENABLED ?? "").trim().toLowerCase()
  return v === "1" || v === "true" || v === "yes"
}
