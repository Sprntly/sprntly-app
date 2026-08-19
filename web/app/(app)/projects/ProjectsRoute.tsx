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

  // Projects is unconditionally on — no feature gate. (The former cosmetic
  // build-time gate was removed alongside the backend request-time one.)
  const id = searchParams.get("id")
  // Optional initial chat-tab selection (`&chat=group|individual`), set by
  // the main-chat PRD fork nav (`ChatScreen.goToProjectPrivateChat` via
  // `projectPath(id, { chat: "individual" })`) so the user lands directly on
  // the forked project's private chat. Anything else is ignored — the shell
  // keeps its own `"group"` default.
  const chat = searchParams.get("chat")
  const initialChat = chat === "individual" || chat === "group" ? chat : undefined

  if (id) {
    return <ProjectDetailScreen projectId={id} initialChat={initialChat} />
  }

  return <ProjectsScreen />
}
