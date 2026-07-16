import { redirect } from "next/navigation"

// The Backlog page was renamed to Ideation. Old links and bookmarks keep
// working via this permanent redirect.
export default function BacklogRedirect() {
  redirect("/ideation")
}
