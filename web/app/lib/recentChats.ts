"use client"

import { useEffect, useState } from "react"
import { conversationsApi, type ConversationRecord } from "./api"

/**
 * The conversation list, shared by the two surfaces that show it: the sidebar's
 * recent-chats section and the full chat-history screen.
 *
 * ONE CACHE, because they render at the same time. The sidebar is mounted on
 * every authenticated screen, so a second independent fetch would mean two
 * calls to `/v1/conversations` on every navigation — and, worse, two answers
 * that can disagree, showing a chat in the nav that the history screen behind
 * it does not list yet.
 *
 * Keyed by user AND company: chats are per-person, and switching workspace must
 * not show the previous one's threads for the frame before the refetch lands.
 */
const listCache = new Map<string, ConversationRecord[]>()

export function chatsCacheKey(userId: string, company: string | null): string {
  return `${userId}:${company ?? "__none__"}`
}

export function cachedChats(key: string): ConversationRecord[] | undefined {
  return listCache.get(key)
}

export function hasCachedChats(key: string): boolean {
  return listCache.has(key)
}

export function setCachedChats(key: string, rows: ConversationRecord[]): void {
  listCache.set(key, rows)
}

/**
 * Load the conversation list, stale-while-revalidate: whatever is cached
 * renders on the first frame, and the refetch replaces it when it lands.
 *
 * Never throws and never surfaces an error — a nav section that cannot load is
 * an empty nav section, not a broken screen. `loaded` distinguishes "no chats
 * yet" from "still asking", which is the difference between rendering an empty
 * state and rendering nothing.
 */
export function useChatsList(key: string | null): {
  chats: ConversationRecord[]
  loaded: boolean
} {
  const [chats, setChats] = useState<ConversationRecord[]>(
    () => (key ? listCache.get(key) : undefined) ?? [],
  )
  const [loaded, setLoaded] = useState(() => !!key && listCache.has(key))

  useEffect(() => {
    // A null key is "nobody is signed in" — `/v1/conversations` is an authed
    // route, and asking it who the anonymous user's chats are is a 401 the
    // caller can neither use nor show.
    if (!key) return
    let cancelled = false
    const cached = listCache.get(key)
    if (cached) {
      setChats(cached)
      setLoaded(true)
    }
    conversationsApi
      .list()
      .then((res) => {
        if (cancelled) return
        listCache.set(key, res.conversations)
        setChats(res.conversations)
        setLoaded(true)
      })
      .catch(() => {
        if (!cancelled) setLoaded(true)
      })
    return () => {
      cancelled = true
    }
  }, [key])

  return { chats, loaded }
}

/** Fired after the resume baton is written, for a chat surface that is ALREADY
 *  mounted and would otherwise never look. */
export const RESUME_EVENT = "sprntly:resume-conv"

/** How many threads the sidebar shows before "View all chats". */
export const SIDEBAR_RECENT_LIMIT = 20

/**
 * The threads to show, newest first, capped.
 *
 * ORDERED BY WHEN THE THREAD STARTED, not by `updated_at`, and that is the
 * opposite of what it looks like it should be. `updated_at` is not a record of
 * user activity: the surface PATCHes a conversation's `prd_id` when it binds a
 * PRD, and that write bumps the column. Three conversations started on three
 * different days — 25 Aug 14:16, 26 Aug 10:07, 26 Aug 10:25 — all carried
 * `updated_at` of 26 Aug 19:13:5x, because one mount patched all three. Sorted
 * by that, unrelated threads clump at the top the moment you open the app,
 * which is what "the same thread keeps repeating in the nav" turned out to be.
 * `created_at` only ever moves when a person starts something.
 *
 * A BLANK TITLE IS NOT A ROW. Some conversations are stored with an empty
 * title (project-bound ones, today); in a list of titles they render as a
 * clickable empty line, which reads as a broken nav rather than as a chat.
 *
 * Pinned chats are NOT floated to the top. That is the history screen's job,
 * where pinning is a visible affordance with a control beside it; in the nav
 * the promise is "what you were just doing", and a thread from March sitting
 * above this morning's would read as a bug rather than as a pin.
 */
export function recentChats(
  chats: ConversationRecord[],
  limit: number = SIDEBAR_RECENT_LIMIT,
): ConversationRecord[] {
  return chats
    .filter((c) => (c.title || "").trim().length > 0)
    .sort((a, b) => startedAt(b) - startedAt(a))
    .slice(0, limit)
}

/**
 * The stamp beside a nav row's title.
 *
 * WHY A TIMESTAMP AT ALL. A conversation's title is the first message,
 * verbatim, so asking the same question twice produces two rows reading
 * identically — four of "just show me the prds that i created", each a
 * different thread with a different answer, one of them six turns long. With
 * nothing but the title on the row they read as one chat repeating.
 *
 * IT ALWAYS CARRIES THE TIME, and that is the whole point. The first version
 * of this said "2d" for anything older than today — and the four rows it
 * existed to separate were all asked on the SAME day, 45 minutes apart, so
 * every one of them read "2d" and nothing was disambiguated. A day is not
 * precise enough to tell two asks of one question apart; a clock is.
 *
 * The row renders this over the end of the title on hover rather than beside
 * it (see `.sb-chat-when`), so the length costs the title no width.
 */
export function chatStamp(iso: string, now: Date = new Date()): string {
  const ts = Date.parse(iso)
  if (!Number.isFinite(ts)) return ""
  const then = new Date(ts)
  const clock = `${then.getHours()}:${String(then.getMinutes()).padStart(2, "0")}`
  const sameDay =
    then.getFullYear() === now.getFullYear() &&
    then.getMonth() === now.getMonth() &&
    then.getDate() === now.getDate()
  if (sameDay) return clock
  const month = then.toLocaleString("en-US", { month: "short" })
  return `${then.getDate()} ${month}, ${clock}`
}

function startedAt(c: ConversationRecord): number {
  const t = c.created_at ? Date.parse(c.created_at) : NaN
  // A row with no parseable date sorts last rather than throwing the whole
  // comparison into NaN, which would leave the list in arbitrary order.
  return Number.isFinite(t) ? t : 0
}

/**
 * Open a saved thread.
 *
 * The handoff is a localStorage baton rather than a route param: ChatScreen
 * reads `sprntly_resume_conv` on mount, opens the tab immediately in a loading
 * state, and hydrates its own turns. So this navigates without waiting on a
 * fetch, and a double-click is harmless.
 *
 * `fallbackTurns` carries the saved preview so the thread has something to
 * render if that hydration comes back empty or fails — the row the user
 * clicked always shows the exchange it promised.
 */
export function resumeConversation(
  chat: Pick<ConversationRecord, "id" | "title"> & {
    query?: string | null
    reply?: unknown
    prd_id?: number | null
    project_id?: number | null
  },
  goToChat: () => void,
): void {
  const fallbackTurns: { role: string; content: string }[] = []
  if (chat.query) {
    fallbackTurns.push({ role: "user", content: chat.query })
    const replyText =
      typeof chat.reply === "string"
        ? chat.reply
        : ((chat.reply as { answer?: string } | null)?.answer ?? "")
    if (replyText) fallbackTurns.push({ role: "assistant", content: replyText })
  }
  try {
    localStorage.setItem(
      "sprntly_resume_conv",
      JSON.stringify({
        dbId: chat.id,
        title: chat.title,
        fallbackTurns,
        prdId: chat.prd_id ?? null,
        projectId: chat.project_id ?? null,
      }),
    )
  } catch {
    // Private mode, or storage full. The chat surface still opens; it just
    // opens on the last thread instead of this one, which beats not
    // navigating at all.
  }
  // TELL THE SURFACE, don't only navigate. ChatScreen reads the baton on mount
  // and when the route CHANGES to chat — so clicking a thread in the nav while
  // already on the chat surface wrote this and nothing ever read it: the click
  // did nothing at all. From the history screen the route changed, which is
  // why that path always worked and this one did not.
  try {
    window.dispatchEvent(new CustomEvent(RESUME_EVENT))
  } catch {
    // No window (SSR/tests without a DOM) — the navigation below still runs
    // and the mount-time read picks the baton up.
  }
  goToChat()
}
