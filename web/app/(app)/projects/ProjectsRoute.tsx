"use client"

// Client surface for the flat `/projects` route (AD-P14). Reads `?id` from
// the URL: absent → the list (`ProjectsScreen`); present → the detail shell
// (`ProjectDetailScreen`), keyed on this same `id` (same co-location
// pattern as `web/app/(app)/prototype/PrototypeRoute.tsx`: page.tsx
// satisfies static export, this owns the runtime behaviour). No `[id]`
// dynamic segment anywhere.
import { useSearchParams } from "next/navigation"
import { ProjectsScreen } from "../../components/screens/app/projects/ProjectsScreen"
import { ProjectDetailScreen } from "../../components/screens/app/projects/ProjectDetailScreen"

export function ProjectsRoute() {
  const searchParams = useSearchParams()
  const id = searchParams.get("id")

  if (id) {
    return <ProjectDetailScreen projectId={id} />
  }

  return <ProjectsScreen />
}
