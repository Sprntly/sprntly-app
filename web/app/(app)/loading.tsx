"use client"

import { usePathname } from "next/navigation"
import { AppLayout } from "../components/screens/app/AppLayout"

/** Instant route-transition fallback for the (app) group. router.push runs in a
 *  React transition, so Next keeps the OLD page on screen until the destination
 *  route's chunk is downloaded — which reads as "I clicked a tab and nothing
 *  happened". This boundary swaps in immediately instead.
 *
 *  Each screen renders its own AppLayout (the sidebar + chrome are part of the
 *  page, not the route layout), so the fallback renders the same AppLayout to
 *  keep the sidebar visually continuous during the swap — the context providers
 *  it needs live in (app)/layout.tsx ABOVE this boundary. Onboarding routes are
 *  chrome-less (no sidebar), so they get a bare centered spinner. */
export default function AppRouteLoading() {
  const pathname = usePathname()
  if (pathname?.startsWith("/onboarding")) {
    return (
      <div className="route-loading route-loading--bare" role="status" aria-label="Loading">
        <div className="onb-spinner" />
      </div>
    )
  }
  return (
    <AppLayout>
      <div className="route-loading" role="status" aria-label="Loading">
        <div className="onb-spinner" />
      </div>
    </AppLayout>
  )
}
