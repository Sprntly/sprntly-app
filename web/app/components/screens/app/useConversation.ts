"use client"

/**
 * `useConversation` — the shared single-conversation engine main adopts.
 *
 * Built by EXTRACTION of main's inline engine (not a re-derivation), modeled on
 * the live single-conversation composition in `projects/useProjectConversation.ts`
 * (the oracle). It composes the already-shared units (`useMainConversation` today;
 * `useComposer`/`useThreadScroll`/`useConversationGeneration` fold in over the
 * following sub-commits) over an injected `MainConversationAdapter`, so main's tab
 * wrapper mounts ONE engine for the active conversation and the run stays
 * byte-identical.
 *
 * STORE + REFS ARE INJECTED SEAMS (see `conversation/types.ts`). Main's turn store
 * is the tab list (writes reach INACTIVE conversations for background asks), and
 * the three render-mutated refs are HOST-OWNED so the wrapper can thread them into
 * BOTH this engine's async run AND the render-time `mapMainTurns`. The engine
 * therefore reads/writes the store through the handle the adapter mints
 * (`makeTabHandle`) and never owns an internal single-conversation `useState`
 * store when driving main.
 *
 * SUB-COMMIT 2 SCOPE: this hook owns the ask-core wiring only —
 * `makeTabHandle` + the one surface-divergent grounding seam (`resolveAskParams`)
 * + the post-answer `getPrdId`, plus the `useMainConversation` call. It returns
 * the run/stop/action-turn functions (and the handle factory the generation flows
 * still consume from the wrapper). submitAsk / composer / clarify fold in next.
 */

import { useCallback } from "react"
import type { Dispatch, RefObject, SetStateAction } from "react"
import { addToSet, removeFromSet } from "../../../lib/chatAskState"
import { getPendingAsk } from "../../../lib/runAskGeneration"
import type { ChatPersistence } from "../../../lib/chatPersistence"
import type { AskResponse } from "../../../lib/api"
import { useMainConversation, type MainConversation } from "./useMainConversation"
import type { ConversationHandle, ResolveAskParams } from "./conversationCore"
import type { useNextPrompts } from "../../shared/chat-shell/useNextPrompts"
import type { ChatTab, ThreadTurn } from "./ChatScreen"

type PersistedAttachment = { name: string; content: string; key?: string | null; mime?: string | null; size?: number | null }

/**
 * The tab wrapper's injected bindings — main passes its exact tab machinery, so
 * the extracted engine is byte-identical. Every field is a reference main already
 * owns; nothing here is re-derived.
 */
export interface MainConversationAdapter {
  // ── Store access (tab-backed) ──────────────────────────────────────────────
  /** Live view of the tab list (read fresh inside async callbacks). */
  tabsRef: RefObject<ChatTab[]>
  /** The active tab id (the conversation `handleStopAsk` targets). */
  activeTabId: string | null
  /** Live active-tab id, for the handle's `isActive`. */
  activeTabIdRef: RefObject<string | null>
  setTabs: Dispatch<SetStateAction<ChatTab[]>>
  /** Per-tab busy set (composer-blocking, keyed by tab id). */
  setBusyTabs: Dispatch<SetStateAction<ReadonlySet<string>>>
  /** Per-tab in-flight ("asking") guard set. */
  askingTabsRef: RefObject<Set<string>>
  /** Per-tab user-stopped set. */
  stoppedTabsRef: RefObject<Set<string>>
  /** Tenant/company scope the ask + job-resume key by. */
  activeCompany: string
  /** The client-rail + server turn writer (create-once per tab). */
  persistence: ChatPersistence

  // ── Host-owned render refs (never engine state) ────────────────────────────
  mountedRef: RefObject<boolean>
  animatedTurnIds: RefObject<Set<string>>
  askStartRef: RefObject<Map<string, number>>
  resumedTurnsRef: RefObject<Set<string>>

  // ── Persistence spine seams ────────────────────────────────────────────────
  pushPendingConversation: (
    turnId: string,
    query: string,
    key: string,
    attachments?: PersistedAttachment[],
  ) => void
  setActiveConv: (n: number | null) => void
  finalizeConversationTurn: (
    turnId: string,
    updates: { reply?: AskResponse; error?: string },
    key: string,
  ) => Promise<void>

  // ── Cross-cutting ──────────────────────────────────────────────────────────
  nextPrompts: Pick<ReturnType<typeof useNextPrompts>, "onSettled">
  showToast: (title: string, sub: string, link?: string, opts?: { onAction?: () => void; persist?: boolean }) => void
}

export interface Conversation extends MainConversation {
  /** The handle factory the generation flows + submit still consume from the
   *  wrapper until they too fold into this engine. */
  makeHandle: (tabId: string) => ConversationHandle
}

export function useConversation(adapter: MainConversationAdapter): Conversation {
  const {
    tabsRef,
    activeTabId,
    activeTabIdRef,
    setTabs,
    setBusyTabs,
    askingTabsRef,
    stoppedTabsRef,
    activeCompany,
    persistence,
    mountedRef,
    animatedTurnIds,
    askStartRef,
    resumedTurnsRef,
    pushPendingConversation,
    setActiveConv,
    finalizeConversationTurn,
    nextPrompts,
    showToast,
  } = adapter

  // `makeTabHandle` mints a `ConversationHandle` onto ONE tab, wrapping the tab
  // multiplexer accessors (turn patch, busy, stop/asking flags, pending-ask
  // lookup) so the ask-core reads/writes a conversation WITHOUT hardcoding the
  // `setTabs(prev => prev.map(...))`. Behaviour is byte-unchanged from the inline
  // version — every method is a thin 1:1 wrapper over what the run already did.
  const makeHandle = useCallback(
    (tabId: string): ConversationHandle => ({
      key: tabId,
      getTurns: () => tabsRef.current?.find((t) => t.id === tabId)?.thread ?? [],
      patchTurns: (update) =>
        setTabs((prev) =>
          prev.map((t) => {
            if (t.id !== tabId) return t
            // Preserve "return the SAME tab ref when the thread is untouched": only
            // mint a new tab object when `update` returns a new thread array (a
            // no-op stop returns the input array unchanged).
            const next = update(t.thread)
            return next === t.thread ? t : { ...t, thread: next }
          }),
        ),
      setBusy: (busy) =>
        setBusyTabs((prev) => (busy ? addToSet(prev, tabId) : removeFromSet(prev, tabId))),
      markStopped: () => {
        stoppedTabsRef.current?.add(tabId)
      },
      isStopped: () => stoppedTabsRef.current?.has(tabId) ?? false,
      clearAsking: () => {
        askingTabsRef.current?.delete(tabId)
      },
      pendingAsk: () => getPendingAsk(activeCompany, tabId),
      isAsking: () => askingTabsRef.current?.has(tabId) ?? false,
      exists: () => tabsRef.current?.some((t) => t.id === tabId) ?? false,
      patchMeta: (partial) =>
        setTabs((prev) => prev.map((t) => (t.id === tabId ? { ...t, ...partial } : t))),
      isActive: () => activeTabIdRef.current === tabId,
      dbConvId: () => tabsRef.current?.find((t) => t.id === tabId)?.dbConvId ?? null,
      getMeta: () => tabsRef.current?.find((t) => t.id === tabId) ?? null,
    }),
    [tabsRef, activeTabIdRef, setTabs, setBusyTabs, stoppedTabsRef, askingTabsRef, activeCompany],
  )

  // ── Ask grounding (the one surface-divergent seam) ────────────────────────
  // Resolve THIS send's conversation id + grounding at request time: reuse the
  // tab's dbConvId (or create the row once via the shared persistence), then
  // layer main's PRD>evidence>ticket-set priority. Injected into the ask-core so
  // the run body stays surface-agnostic.
  const resolveAskParams = useCallback<ResolveAskParams>(
    async (key, { turnId, displayQuery }) => {
      const convId =
        tabsRef.current?.find((t) => t.id === key)?.dbConvId ??
        (await persistence.ensureConversation(key, {
          turnId,
          title: displayQuery.length > 52 ? `${displayQuery.slice(0, 49)}…` : displayQuery,
          query: displayQuery,
        }))
      // Re-read AFTER the await — tabsRef, not a closure — so a conversation
      // created (or a PRD that finished generating) AFTER the tab opened is still
      // picked up.
      const targetTab = tabsRef.current?.find((t) => t.id === key)
      return {
        convId: convId ?? null,
        grounding: {
          ...(convId != null ? { conversation_id: convId } : {}),
          ...(targetTab?.prdId != null ? { prd_id: targetTab.prdId } : {}),
          ...(targetTab?.prdId == null && targetTab?.evidenceId != null
            ? { evidence_id: targetTab.evidenceId }
            : {}),
          ...(targetTab?.prdId == null && targetTab?.evidenceId == null
            && targetTab?.ticketSetId != null
            ? { ticket_set_id: targetTab.ticketSetId }
            : {}),
        },
      }
    },
    [tabsRef, persistence],
  )
  // The grounding PRD id for the post-answer suggestion fetch, read fresh at
  // settle (a PRD that finished generating mid-ask is still picked up).
  const getPrdId = useCallback(
    (key: string) => tabsRef.current?.find((t) => t.id === key)?.prdId ?? null,
    [tabsRef],
  )

  // ── The single-conversation ask-core (send-run + stop + action-turn) ───────
  const engine = useMainConversation({
    makeHandle,
    activeKey: activeTabId,
    activeCompany,
    askingRef: askingTabsRef,
    setBusy: setBusyTabs,
    resolveAskParams,
    getPrdId,
    mountedRef,
    animatedTurnIds,
    askStartRef,
    resumedTurnsRef,
    pushPendingConversation,
    setActiveConv,
    finalizeConversationTurn,
    nextPrompts,
    showToast,
  })

  return { ...engine, makeHandle }
}
