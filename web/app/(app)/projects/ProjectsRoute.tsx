"use client"

// Client surface for the flat `/projects` route (AD-P14). Reads `?id` from
// the URL: absent → the list (`ProjectsScreen`); present → the detail shell
// (`ProjectDetailScreen`), keyed on this same `id` (same co-location
// pattern as `web/app/(app)/prototype/PrototypeRoute.tsx`: page.tsx
// satisfies static export, this owns the runtime behaviour). No `[id]`
// dynamic segment anywhere.
import { useEffect } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { ProjectsScreen } from "../../components/screens/app/projects/ProjectsScreen"
import { ProjectDetailScreen } from "../../components/screens/app/projects/ProjectDetailScreen"
import { projectsEnabled } from "../../lib/featureFlags"

export function ProjectsRoute() {
  const router = useRouter()
  const searchParams = useSearchParams()

  // Cosmetic build-time gate (mirrors the backend's request-time
  // PROJECTS_ENABLED 404, which is the real security boundary). When off,
  // bounce straight back to "/" before reading `?id` — the OnboardingRequiredGuard
  // client-redirect idiom — so a direct `/projects?...` URL can never reach
  // ProjectsScreen/ProjectDetailScreen in a build where the feature is dark.
  const enabled = projectsEnabled()
  useEffect(() => {
    if (!enabled) router.replace("/")
  }, [enabled, router])
  if (!enabled) return null

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
