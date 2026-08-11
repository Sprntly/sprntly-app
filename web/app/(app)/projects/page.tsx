// Flat route mount for Projects (AD-P14): ONE route, `?id=<id>` selects the
// view — NOT a `[id]` dynamic segment. Under `output:"export"` a dynamic
// segment fails `next build` unless statically enumerable; a flat route +
// query param needs neither `generateStaticParams` nor the sentinel-param/
// nginx-rewrite trick. Exactly the `/prototype?prd=<id>` pattern
// (`web/app/(app)/prototype/page.tsx`).
//
// Thin server shell satisfying static export; ProjectsRoute (client, reads
// `?id` via useSearchParams) owns the runtime branch. The Suspense boundary
// is required for the same reason PrototypeRoute needs one: Next prerenders
// this route at build time, and useSearchParams() must be wrapped in
// Suspense or the build errors.
import { Suspense } from "react"
import { ProjectsRoute } from "./ProjectsRoute"

export default function ProjectsPage() {
  return (
    <Suspense fallback={<div className="design-agent-surface" aria-busy="true" />}>
      <ProjectsRoute />
    </Suspense>
  )
}
