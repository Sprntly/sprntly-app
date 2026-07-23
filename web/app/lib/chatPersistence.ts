// Per-tab Supabase conversation persistence for the parallel-chat ChatScreen.
//
// ChatScreen supports multiple parallel chat TABS, each of which must map to its
// OWN Supabase conversation. The persistence here is driven entirely off the
// per-tab `dbConvId` (read/written via the injected `getTabConvId` /
// `setTabConvId` callbacks, which the component wires to its `tabs` state) — it
// holds NO shared mutable conversation id of its own, so sending in tab B can
// never record into tab A's conversation.
//
// Timing invariant — ONE conversation per tab:
//   submitAsk persists the user turn (pushUserTurn) and, after `askApi.ask`
//   resolves, persists the assistant reply (pushAssistantTurn). When a tab has
//   no conversation yet, the user turn triggers `conversationsApi.create`. The
//   assistant turn may be requested before that create's promise settles. To
//   keep both turns in the SAME conversation and avoid a duplicate create, the
//   create is recorded as a per-tab in-flight promise (`inFlightCreates`); both
//   the user-turn and assistant-turn paths await that single promise and use its
//   resolved id. The in-flight entry is cleared once it settles.
//
// All API calls stay fire-and-forget resilient: a persistence failure is
// swallowed (`.catch`) so it never breaks the UI.

import type { conversationsApi as ConversationsApi } from "./api"

/** Minimal slice of conversationsApi this module needs (eases mocking in tests). */
export type ConversationsPersistenceApi = Pick<
  typeof ConversationsApi,
  "create" | "addTurn"
>

export type ChatPersistenceDeps = {
  /** Lazily resolve the conversations API (the component imports it dynamically). */
  getApi: () => Promise<ConversationsPersistenceApi>
  /** Read a tab's current Supabase conversation id (null if not yet created). */
  getTabConvId: (tabId: string) => number | null
  /**
   * Read the PRD id a tab is about (null for plain chat/brief tabs). Stamped onto
   * the conversation at create time so a reopened PRD can find + rehydrate it via
   * conversationsApi.byPrd.
   */
  getTabPrdId?: (tabId: string) => number | null
  /** Persist a newly-created conversation id onto its tab. */
  setTabConvId: (tabId: string, convId: number) => void
  /**
   * Called right after a conversation is created so the in-memory rail entry can
   * be tagged with the DB id (the `_dbId` tagging ChatScreen does today).
   */
  onConversationCreated?: (turnId: string, convId: number) => void
}

export function createChatPersistence(deps: ChatPersistenceDeps) {
  // Per-tab in-flight create promises. Keyed by tabId; resolves to the new conv id.
  const inFlightCreates = new Map<string, Promise<number>>()

  /**
   * Resolve the conversation id to write to for `tabId`, creating the
   * conversation exactly once if the tab has none yet. Concurrent callers for
   * the same tab share a single create via `inFlightCreates`.
   */
  function resolveConvId(
    tabId: string,
    create: { turnId: string; title: string; query: string },
  ): Promise<number | null> {
    const existing = deps.getTabConvId(tabId)
    if (existing != null) return Promise.resolve(existing)

    const inFlight = inFlightCreates.get(tabId)
    if (inFlight) return inFlight

    // Register the in-flight create SYNCHRONOUSLY (before any await) so a
    // concurrent caller for the same tab — e.g. the assistant turn firing right
    // after the user turn, before `getApi()` settles — shares this same create
    // instead of starting a second one. This is the core of the "ONE conversation
    // per tab" invariant under fire-and-forget timing.
    const prdId = deps.getTabPrdId?.(tabId) ?? undefined
    const createPromise: Promise<number> = (async () => {
      const api = await deps.getApi()
      const conv = await api.create({
        title: create.title,
        preview: create.query.slice(0, 200),
        query: create.query,
        agent_type: "ask",
        ...(prdId != null ? { prd_id: prdId } : {}),
      })
      deps.setTabConvId(tabId, conv.id)
      deps.onConversationCreated?.(create.turnId, conv.id)
      return conv.id
    })()
    inFlightCreates.set(tabId, createPromise)
    createPromise
      .catch(() => undefined)
      .finally(() => {
        // Clear once settled (only if still the current entry) so a later, truly
        // new conversation for this tab could be created if it were ever reset.
        if (inFlightCreates.get(tabId) === createPromise) {
          inFlightCreates.delete(tabId)
        }
      })
    return createPromise
  }

  /**
   * Persist the user's query for `tabId`. Creates the conversation if needed
   * (storing its id on the tab), then adds the user turn against it.
   */
  async function pushUserTurn(
    tabId: string,
    args: {
      turnId: string
      title: string
      query: string
      /** Extracted text of files attached to this turn — persisted with it so a
       *  reloaded thread (and the chat→PRD flow) still sees the documents. */
      attachments?: { name: string; content: string }[]
    },
  ): Promise<void> {
    try {
      const convId = await resolveConvId(tabId, {
        turnId: args.turnId,
        title: args.title,
        query: args.query,
      })
      if (convId == null) return
      const api = await deps.getApi()
      await api.addTurn(convId, "user", args.query, args.attachments)
    } catch {
      /* fire-and-forget: never break the UI on a persistence failure */
    }
  }

  /**
   * Persist the assistant reply for `tabId`. Awaits any in-flight create so the
   * assistant turn lands in the SAME conversation as its user turn.
   */
  async function pushAssistantTurn(tabId: string, replyText: string): Promise<void> {
    try {
      let convId = deps.getTabConvId(tabId)
      if (convId == null) {
        const inFlight = inFlightCreates.get(tabId)
        if (inFlight) convId = await inFlight
      }
      if (convId == null) return
      const api = await deps.getApi()
      await api.addTurn(convId, "assistant", replyText)
    } catch {
      /* fire-and-forget */
    }
  }

  return { pushUserTurn, pushAssistantTurn, resolveConvId }
}

export type ChatPersistence = ReturnType<typeof createChatPersistence>

/** Extract a plain-text reply string from an AskResponse-ish value. */
export function replyToText(reply: unknown): string {
  if (typeof reply === "string") return reply
  const answer = (reply as { answer?: unknown } | null)?.answer
  if (typeof answer === "string") return answer
  return JSON.stringify(reply).slice(0, 2000)
}
