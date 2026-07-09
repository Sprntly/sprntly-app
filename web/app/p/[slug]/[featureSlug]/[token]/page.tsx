// Canonical public viewer route `/p/<slug>/<featureSlug>/<token>` — thin SERVER
// shell, same pattern as the 2-segment canonical route. Both `slug` and
// `featureSlug` are COSMETIC; resolution is by TOKEN alone, which is always the
// LAST `/p` segment regardless of depth (shareTokenFromPathname — unchanged,
// already depth-agnostic). The 2-segment route is UNTOUCHED and keeps resolving
// already-shared 2-seg links forever: Next.js matches these as two DISTINCT
// routes by exact segment count (same precedent as today's coexisting 1-seg
// legacy + 2-seg canonical routes) — no redirect needed.
import { PublicTokenViewer } from "../../../PublicTokenViewer"

export function generateStaticParams() {
  return [{ slug: "_", featureSlug: "_", token: "_" }]
}

export default function CanonicalThreeSegmentPrototypePage() {
  return <PublicTokenViewer />
}
