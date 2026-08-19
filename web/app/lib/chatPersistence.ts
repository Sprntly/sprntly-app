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
  /**
   * First-class get-or-create for a key's conversation id, used ONLY by
   * `ensureConversation`. When omitted, `ensureConversation` falls back to the
   * built-in per-tab resolver (`resolveConvId`) — which is how MAIN keeps its
   * identical `inFlightCreates`/`knownConvIds`/append-queue semantics and, per
   * ChatScreen's ask-grounding note, shares the very same in-flight create the
   * turn persistence uses (create-once per tab). A surface that owns a DURABLE
   * conversation row (e.g. a project chat) injects its own get-or-create here
   * instead of post-hoc replacing `ensureConversation`, so the shared create
   * path routes to that row rather than minting a throwaway one.
   */
  resolveConversationId?: (
    key: string,
    create: { turnId: string; title: string; query: string },
  ) => Promise<number | null>
}

export function createChatPersistence(deps: ChatPersistenceDeps) {
  // Per-tab in-flight create promises. Keyed by tabId; resolves to the new conv id.
  const inFlightCreates = new Map<string, Promise<number>>()

  // Conversation ids this module has already created, remembered independently
  // of React.
  //
  // `deps.getTabConvId` reads the tab's `dbConvId` out of a ref that only
  // refreshes on RENDER, and the in-flight entry is dropped the moment the
  // create settles. Between those two points a tab has a real conversation that
  // neither source can name — and a write landing in that window was silently
  // discarded (`convId == null` → return). Turns pushed back-to-back never hit
  // it; a reply written a network round-trip after its user turn does, which is
  // exactly what the deferred PRD-command acknowledgment is.
  const knownConvIds = new Map<string, number>()

  /** The tab's conversation id from any source that can currently name it. */
  function currentConvId(tabId: string): number | null {
    return deps.getTabConvId(tabId) ?? knownConvIds.get(tabId) ?? null
  }

  // Per-tab APPEND QUEUE — turns reach the DB in the order they were written.
  //
  // A user turn and its reply are pushed back to back, and each used to issue
  // its own request the moment its own awaits settled. Two POSTs in flight at
  // once means the server can insert them either way round, and the restore
  // pairs a user turn with the row IMMEDIATELY after it — so an inverted pair
  // comes back as a question with no answer. That is the reported bug: leaving
  // a generating-PRD chat and reopening it from history showed "No response was
  // generated for this message." exactly where the acknowledgment had been, on
  // a tab that was visibly still generating.
  //
  // Queuing by TAB (not globally) keeps parallel chats independent, and the
  // queue is joined synchronously at call time — after the awaits would be too
  // late, since whichever push finished resolving first would queue first and
  // the race would simply move.
  const appendQueues = new Map<string, Promise<unknown>>()

  function enqueueAppend(tabId: string, work: () => Promise<void>): Promise<void> {
    const prev = appendQueues.get(tabId) ?? Promise.resolve()
    // `work` runs whether the previous append resolved or rejected — one failed
    // write must not wedge every later turn on this tab.
    const next = prev.then(work, work)
    appendQueues.set(tabId, next.then(() => undefined, () => undefined))
    return next
  }

  /**
   * Resolve the conversation id to write to for `tabId`, creating the
   * conversation exactly once if the tab has none yet. Concurrent callers for
   * the same tab share a single create via `inFlightCreates`.
   */
  function resolveConvId(
    tabId: string,
    create: { turnId: string; title: string; query: string },
  ): Promise<number | null> {
    const existing = currentConvId(tabId)
    if (existing != null) return Promise.resolve(existing)

    const inFlight = inFlightCreates.get(tabId)
    if (inFlight) return inFlight

    // Register the in-flight create SYNCHRONOUSLY (before any await) so a
    // concurrent caller for the same tab — e.g. the assistant turn firing right
    // after the user turn, before `getApi()` settles — shares this same create
    // instead of starting a second one. This is the core of the "ONE conversation
    // per tab" invariant under fire-and-forget timing.
    const createPromise: Promise<number> = (async () => {
      const api = await deps.getApi()
      // Read the tab's PRD id as LATE as possible: a PRD command stamps it on
      // the tab the moment its generate call returns, which can land while this
      // create is still in flight. Reading it here rather than up front means
      // the row is often inserted already bound to its PRD.
      const prdId = deps.getTabPrdId?.(tabId) ?? undefined
      const conv = await api.create({
        title: create.title,
        preview: create.query.slice(0, 200),
        query: create.query,
        agent_type: "ask",
        ...(prdId != null ? { prd_id: prdId } : {}),
      })
      knownConvIds.set(tabId, conv.id)
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
  function pushUserTurn(
    tabId: string,
    args: {
      turnId: string
      title: string
      query: string
      /** Extracted text of files attached to this turn — persisted with it so a
       *  reloaded thread (and the chat→PRD flow) still sees the documents. The
       *  optional `key`/`mime` point at the ORIGINAL file in storage so the
       *  reopened chip can render/download the real document. */
      attachments?: { name: string; content: string; key?: string | null; mime?: string | null; size?: number | null }[]
    },
  ): Promise<void> {
    return enqueueAppend(tabId, async () => {
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
    })
  }

  /**
   * Persist the assistant reply for `tabId`. Queued behind its user turn (see
   * enqueueAppend) so the pair lands in the order it was written.
   */
  function pushAssistantTurn(tabId: string, replyText: string): Promise<void> {
    return enqueueAppend(tabId, async () => {
      try {
        let convId = currentConvId(tabId)
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
    })
  }

  /**
   * Create this tab's conversation NOW (if it has none) and resolve its id,
   * WITHOUT writing a turn — the turn follows through the normal
   * pushUserTurn path, which reuses this same conversation.
   *
   * Used by the PRD command flows, which must hand the conversation id to
   * `POST /v1/prd/import` / `generate-from-task` so the backend can bind the
   * two rows the moment it creates the PRD. Doing that on the client instead
   * meant the link was lost whenever the user navigated away mid-generation.
   *
   * Registers the create synchronously (via resolveConvId's in-flight map), so
   * a caller that fires immediately after this returns will share it rather
   * than racing a second create. Resolves null on failure — every caller must
   * treat the id as best-effort and proceed without it.
   */
  function ensureConversation(
    tabId: string,
    args: { turnId: string; title: string; query: string },
  ): Promise<number | null> {
    try {
      // A surface with a durable row (project chat) injects its get-or-create
      // via `resolveConversationId`; main omits it and uses the built-in
      // `resolveConvId`, which shares the tab's single in-flight create with
      // pushUserTurn/pushAssistantTurn.
      if (deps.resolveConversationId) {
        return deps.resolveConversationId(tabId, args).catch(() => null)
      }
      return resolveConvId(tabId, args).catch(() => null)
    } catch {
      return Promise.resolve(null)
    }
  }

  return { pushUserTurn, pushAssistantTurn, resolveConvId, ensureConversation }
}

export type ChatPersistence = ReturnType<typeof createChatPersistence>

/** Extract a plain-text reply string from an AskResponse-ish value. */
export function replyToText(reply: unknown): string {
  if (typeof reply === "string") return reply
  const answer = (reply as { answer?: unknown } | null)?.answer
  if (typeof answer === "string") return answer
  return JSON.stringify(reply).slice(0, 2000)
}
