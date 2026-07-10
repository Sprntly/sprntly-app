import { redirect } from "next/navigation"

// The chats surface moved to `/history`. Keep this legacy route as a permanent
// redirect so old links/bookmarks (and any cached deep links) still resolve.
export default function ChatsRedirectPage() {
  redirect("/history")
}
