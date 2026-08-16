"use client"

import { Suspense } from "react"
import { ArtifactsScreen } from "../../components/screens/app/ArtifactsScreen"

// The Suspense boundary is required by static export: ArtifactsScreen now reads
// useSearchParams() (the `?focus=<type>-<id>` deep link a Slack share links to),
// and Next prerenders this route — without a boundary the build errors
// ("useSearchParams() should be wrapped in a suspense boundary"). Same pattern
// as (app)/prototype/page.tsx.
export default function ArtifactsPage() {
  return (
    <Suspense fallback={<div aria-busy="true" />}>
      <ArtifactsScreen />
    </Suspense>
  )
}
