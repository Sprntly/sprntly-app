"use client"

import { Suspense } from "react"
import { useSearchParams } from "next/navigation"
import { ArtifactsScreen } from "../../components/screens/app/ArtifactsScreen"

// `?focus=<type>-<id>` — the artifact a link asked to open (what a Slack share
// links to for the kinds with no per-artifact route of their own).
//
// READ HERE, NOT IN THE SCREEN. The route already owns the URL, and keeping
// `useSearchParams()` out of ArtifactsScreen keeps that component renderable
// from props alone — the first cut called the hook inside it and broke fifteen
// tests across four suites that mock `next/navigation` without
// `useSearchParams`, none of which had anything to do with this feature.
//
// The Suspense boundary is required by static export: Next prerenders this
// route, and reading search params without one fails the build
// ("useSearchParams() should be wrapped in a suspense boundary"). Same pattern
// as (app)/prototype/page.tsx.
function ArtifactsRoute() {
  const focus = useSearchParams().get("focus")
  return <ArtifactsScreen focus={focus} />
}

export default function ArtifactsPage() {
  return (
    <Suspense fallback={<div aria-busy="true" />}>
      <ArtifactsRoute />
    </Suspense>
  )
}
