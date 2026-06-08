// Dedicated prototype-page route shell. "Generate Prototype" redirects here
// (instead of opening the generate modal inline over the PRD).
//
// Thin shell mirroring web/app/p/[token]/page.tsx: a component that satisfies
// static export, delegating runtime behaviour to the co-located client
// component. Unlike the canvas / public-viewer shells this route has NO dynamic
// segment — the PRD context rides in as a `?prd=<id>` query param read
// client-side — so it needs no generateStaticParams (it is emitted once as a
// static page; the query param is resolved at runtime).
//
// The Suspense boundary is required by static export: PrototypeRoute reads
// useSearchParams(), and Next prerenders this route — without a boundary the
// build errors ("useSearchParams() should be wrapped in a suspense boundary").
// Same pattern as web/app/sign-up/page.tsx.
//
// Lives in the (app) group → behind AuthGate: this is an authed authoring
// surface, like the /design canvas route.
import { Suspense } from "react"
import { PrototypeRoute } from "./PrototypeRoute"

export default function PrototypePage() {
  return (
    <Suspense
      fallback={
        <div className="design-agent-surface da-prototype-page" aria-busy="true" />
      }
    >
      <PrototypeRoute />
    </Suspense>
  )
}
