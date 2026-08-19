"use client"

// useMentionNotifications — the recipient side of @-mention tagging. Subscribes
// to the viewer's OWN per-user project channel and, on a `mention.received`
// signal (published server-side when someone @mentions them), fires a toast and
// bumps an unread count. Mounted at the project chat host so it is alive across
// the group⇆individual swap. Private per recipient — the group channel never
// carries this (AD-TNM2/AD-P30).
import { useCallback, useState } from "react"
import { useWorkspace } from "../../../../context/WorkspaceContext"
import { useNavigation } from "../../../../context/NavigationContext"
import { useRealtimeChannel } from "./useRealtimeChannel"

/** The `mention.received` DTO (backend `_MENTION_SIGNAL_DTO_KEYS`) — ids + names
 *  only, never message text or project content. */
type MentionSignal = {
  project_id?: number
  project_name?: string | null
  actor_name?: string | null
  kind?: string
}

export interface UseMentionNotifications {
  unreadCount: number
  clear: () => void
}

export function useMentionNotifications(projectId: number | string): UseMentionNotifications {
  const { profile } = useWorkspace()
  const { showToast } = useNavigation()
  const selfUserId = (profile as { id?: string | null } | null)?.id ?? null
  const [unread, setUnread] = useState(0)

  const onEvent = useCallback((event: string, payload: unknown) => {
    if (event !== "mention.received" || !payload || typeof payload !== "object") return
    const sig = payload as MentionSignal
    const who = sig.actor_name || "Someone"
    const where = sig.project_name ? ` in ${sig.project_name}` : ""
    showToast("You were mentioned", `${who} mentioned you${where}`)
    setUnread((n) => n + 1)
  }, [showToast])

  // Subscribe only once the viewer's id is known; the per-user topic keeps this
  // nudge private to the recipient.
  useRealtimeChannel(selfUserId ? `project:${projectId}:user:${selfUserId}` : null, { onEvent })

  const clear = useCallback(() => setUnread(0), [])
  return { unreadCount: unread, clear }
}
