"use client"

// Client surface for the flat `/projects` route (AD-P14). Reads `?id` from
// the URL: absent → the list (`ProjectsScreen`, this ticket); present → the
// detail view. `ProjectDetailScreen` itself is a follow-up ticket — until it
// lands, the `?id` branch renders a lightweight placeholder rather than
// 404ing or silently falling back to the list, so the route already
// resolves both branches its final shape needs (same co-location pattern as
// `web/app/(app)/prototype/PrototypeRoute.tsx`: page.tsx satisfies static
// export, this owns the runtime behaviour).
import { useSearchParams } from "next/navigation"
import { AppLayout } from "../../components/screens/app/AppLayout"
import { EmptyPane } from "../../components/shared/EmptyPane"
import { ProjectsScreen } from "../../components/screens/app/projects/ProjectsScreen"

export function ProjectsRoute() {
  const searchParams = useSearchParams()
  const id = searchParams.get("id")

  if (id) {
    // A follow-up ticket mounts ProjectDetailScreen here, keyed on this same `id`.
    return (
      <AppLayout>
        <div style={{ maxWidth: 1220, margin: "0 auto", padding: "0 4px" }}>
          <EmptyPane title="Project detail is coming soon" hint="This view lands with the next Projects ticket." />
        </div>
      </AppLayout>
    )
  }

  return <ProjectsScreen />
}
