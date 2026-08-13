// The team-document surface — one custom artifact, opened from the Artifacts
// library's "Others" section.
//
// NO DYNAMIC SEGMENT, and that is forced rather than chosen: this app is a
// static export, so `/artifacts/[id]` would need `generateStaticParams` over a
// set of ids that only exist at runtime. The document id rides in as
// `?id=<id>`, exactly as the prototype canvas carries `?prd=<id>` for the same
// reason.
//
// The Suspense boundary is likewise required by static export — DocumentRoute
// reads `useSearchParams()`, and without a boundary the build errors
// ("useSearchParams() should be wrapped in a suspense boundary"). Same shape as
// the prototype route and sign-up.
//
// Lives in the (app) group → behind AuthGate: an authed authoring surface.
import { Suspense } from "react"
import { DocumentRoute } from "./DocumentRoute"

export default function DocumentPage() {
  return (
    <Suspense fallback={null}>
      <DocumentRoute />
    </Suspense>
  )
}
