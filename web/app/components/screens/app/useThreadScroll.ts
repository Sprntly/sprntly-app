"use client"

/**
 * The per-conversation thread-viewport scroll behaviour, extracted verbatim from
 * `ChatScreen`: the pinned-follow tracking, the send/new-turn/tab-switch
 * auto-jumps, and the ResizeObserver that keeps the viewport pinned as the
 * answer streams in. Self-contained — it reads only the conversation's turns +
 * which conversation is active + the optimistic pending-send, and returns the
 * three refs/handlers the view wires (`threadScrollRef`, `handleThreadScroll`,
 * `setThreadContentEl`).
 *
 * Part of the shared conversation unit: main calls it in place (one active
 * conversation) and a project slot will call it for its single conversation. The
 * effects live here so the identical scroll behaviour drives every surface — no
 * re-derivation.
 */

import { useCallback, useEffect, useRef } from "react"
import type { ThreadTurn } from "./ChatScreen"

export interface UseThreadScrollDeps {
  /** This conversation's turns (auto-jump keys on a real length increase). */
  thread: ThreadTurn[]
  /** Which conversation is active — main: the active tab id; a project slot: its
   *  single key. The send/tab-switch effects key on it. */
  activeTabId: string | null
  /** The optimistic just-sent bubble — its send re-pins + jumps the viewport
   *  (the intent decision is a round-trip away, so a thread-growth-keyed scroll
   *  would leave the message parked below the fold until the real turn lands). */
  pendingSend: { tabId: string | null } | null
}

export interface ThreadScroll {
  threadScrollRef: React.RefObject<HTMLDivElement | null>
  handleThreadScroll: () => void
  setThreadContentEl: (el: HTMLDivElement | null) => void
}

export function useThreadScroll({ thread, activeTabId, pendingSend }: UseThreadScrollDeps): ThreadScroll {
  // The scrolling thread viewport, so a new question (and the assistant's
  // thinking/answer under it) is scrolled into view instead of staying hidden
  // below the fold in a long conversation.
  const threadScrollRef = useRef<HTMLDivElement>(null)
  // Whether the user is pinned near the bottom. We only auto-follow streaming
  // replies while pinned, so scrolling up to read history isn't yanked back.
  const threadPinnedRef = useRef(true)
  const prevThreadLenRef = useRef(0)

  const scrollThreadToBottom = useCallback((behavior: ScrollBehavior) => {
    const el = threadScrollRef.current
    if (!el) return
    const jump = () => {
      const node = threadScrollRef.current
      if (!node) return
      try {
        node.scrollTo({ top: node.scrollHeight, behavior })
      } catch {
        // jsdom / older engines without Element.scrollTo — set position directly.
        node.scrollTop = node.scrollHeight
      }
    }
    // An instant jump lands on THIS frame: a send has to be on screen on its own
    // commit, not a frame later. The rAF pass then repeats it once the just-added
    // turn (and its thinking skeleton) is laid out, catching the height the first
    // call couldn't measure yet.
    if (behavior !== "smooth") jump()
    requestAnimationFrame(jump)
  }, [])

  // Track whether the user is pinned near the bottom of the thread. Auto-follow
  // only applies while pinned, so scrolling up to read earlier turns during a
  // long answer isn't fought by the follow effect.
  const handleThreadScroll = useCallback(() => {
    const el = threadScrollRef.current
    if (!el) return
    threadPinnedRef.current =
      el.scrollHeight - el.scrollTop - el.clientHeight < 120
  }, [])

  // Callback ref on the thread's content column. A ResizeObserver here keeps the
  // viewport pinned to the bottom as content GROWS — the thinking skeleton
  // appearing, then the answer typing in — not just on the initial render. That
  // covers the async growth a one-shot scroll misses. Re-attaches whenever the
  // content element mounts (tab switch, landing → thread), so it never observes
  // a stale node.
  const threadResizeObsRef = useRef<ResizeObserver | null>(null)
  const setThreadContentEl = useCallback((el: HTMLDivElement | null) => {
    threadResizeObsRef.current?.disconnect()
    threadResizeObsRef.current = null
    if (!el || typeof ResizeObserver === "undefined") return
    const ro = new ResizeObserver(() => {
      const scroller = threadScrollRef.current
      if (scroller && threadPinnedRef.current) scroller.scrollTop = scroller.scrollHeight
    })
    ro.observe(el)
    threadResizeObsRef.current = ro
  }, [])
  useEffect(() => () => threadResizeObsRef.current?.disconnect(), [])

  // The send itself is what has to move the viewport. `pendingSend` renders the
  // user's message on the send's own commit — seconds before the real turn lands
  // (the intent decision is a round-trip away), so a scroll keyed on thread
  // growth left the message parked below the fold that whole time. Re-pin and
  // JUMP: from far up a long thread a smooth animation both takes too long and
  // un-pins the follow behavior on its own way down (the scroll handler samples
  // it mid-animation, well short of the bottom), which is exactly how the answer
  // then streamed in off-screen.
  useEffect(() => {
    if (!pendingSend || pendingSend.tabId !== activeTabId) return
    threadPinnedRef.current = true
    scrollThreadToBottom("auto")
  }, [pendingSend, activeTabId, scrollThreadToBottom])

  // A new turn (the user just asked, or a command seeded its own turn) → re-pin
  // and jump so the question + the assistant's thinking sit in view; the
  // ResizeObserver then follows the answer as it grows. Guard on a real length
  // increase so a reply landing on an existing turn doesn't double-trigger (the
  // observer already handles growth).
  useEffect(() => {
    if (thread.length > prevThreadLenRef.current) {
      threadPinnedRef.current = true
      scrollThreadToBottom("auto")
    }
    prevThreadLenRef.current = thread.length
  }, [thread.length, scrollThreadToBottom])

  // On tab switch/open, land at the bottom (newest turn) without animation and
  // reset the pinned state for the newly shown thread.
  useEffect(() => {
    prevThreadLenRef.current = thread.length
    threadPinnedRef.current = true
    scrollThreadToBottom("auto")
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTabId, scrollThreadToBottom])

  return { threadScrollRef, handleThreadScroll, setThreadContentEl }
}
