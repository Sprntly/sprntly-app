// Parallel-chats DB persistence tests for ChatScreen.
//
// ChatScreen routes Supabase persistence through `createChatPersistence`
// (app/lib/chatPersistence.ts) — the exact module the screen wires up, driven
// off the per-tab `dbConvId`. These tests exercise that REAL logic against a
// mocked `conversationsApi`, modelling tab state with a tiny in-memory store
// that mirrors how ChatScreen's `setTabConvId` / `getTabConvId` read and write
// `ChatTab.dbConvId`. The assertions therefore hold against the shipped routing
// logic, not a re-implementation.
//
// What each assertion covers (from the bug report):
//   1. Two parallel chats -> two distinct conversations (create called twice,
//      distinct payloads, distinct per-tab dbConvId).
//   2. No cross-contamination: a user turn in tab A -> A's conv id; tab B -> B's.
//   3. Assistant reply routes to the same conversation as its user turn.
//   4. Follow-up in a tab that already has a dbConvId -> addTurn, NOT create.
//   5. Tab switch preserves each tab's dbConvId (no reset/clobber).
//   6. No double-create when two sends hit the SAME tab (serialized) -> one create.
import { beforeEach, describe, expect, it, vi } from "vitest"
import {
  createChatPersistence,
  replyToText,
  type ConversationsPersistenceApi,
} from "../../../../lib/chatPersistence"

// ── Mocked conversationsApi ────────────────────────────────────────────────
let nextConvId: number
let createCalls: Array<{ title: string; preview?: string; query?: string; agent_type?: string }>
let addTurnCalls: Array<{ convId: number; role: "user" | "assistant"; content: string }>

function makeApi(opts?: { createDelayMs?: number }): ConversationsPersistenceApi {
  return {
    create: vi.fn(async (body: Parameters<ConversationsPersistenceApi["create"]>[0]) => {
      createCalls.push({
        title: body.title,
        preview: body.preview,
        query: body.query,
        agent_type: body.agent_type,
      })
      if (opts?.createDelayMs) await new Promise((r) => setTimeout(r, opts.createDelayMs))
      const id = nextConvId++
      return { id } as Awaited<ReturnType<ConversationsPersistenceApi["create"]>>
    }),
    addTurn: vi.fn(async (convId: number, role: "user" | "assistant", content: string) => {
      addTurnCalls.push({ convId, role, content })
      return { id: 999 } as Awaited<ReturnType<ConversationsPersistenceApi["addTurn"]>>
    }),
  }
}

// ── In-memory tab store mirroring ChatTab.dbConvId ─────────────────────────
type TabStore = Map<string, number | null>

function makeHarness(opts?: { createDelayMs?: number }) {
  const api = makeApi(opts)
  const tabs: TabStore = new Map()
  const tagged: Array<{ turnId: string; convId: number }> = []
  const persistence = createChatPersistence({
    getApi: async () => api,
    getTabConvId: (tabId) => tabs.get(tabId) ?? null,
    setTabConvId: (tabId, convId) => tabs.set(tabId, convId),
    onConversationCreated: (turnId, convId) => tagged.push({ turnId, convId }),
  })
  // Seed a tab the way ChatScreen does when a tab is opened (no conv yet).
  const openTab = (tabId: string, dbConvId: number | null = null) => tabs.set(tabId, dbConvId)
  return { api, tabs, tagged, persistence, openTab }
}

beforeEach(() => {
  nextConvId = 100
  createCalls = []
  addTurnCalls = []
})

describe("ChatScreen parallel-chats DB persistence", () => {
  // Assertion 1 + 2: two tabs -> two distinct conversations, no cross-contamination.
  it("opens two parallel chats as two distinct conversations and routes each user turn to its own conv", async () => {
    const h = makeHarness()
    h.openTab("tab-A")
    h.openTab("tab-B")

    await h.persistence.pushUserTurn("tab-A", { turnId: "t1", title: "A title", query: "hello from A" })
    await h.persistence.pushUserTurn("tab-B", { turnId: "t2", title: "B title", query: "hello from B" })

    // create called twice with distinct payloads
    expect(h.api.create).toHaveBeenCalledTimes(2)
    expect(createCalls[0].query).toBe("hello from A")
    expect(createCalls[1].query).toBe("hello from B")
    expect(createCalls[0].title).toBe("A title")
    expect(createCalls[1].title).toBe("B title")

    // each tab ends up with a distinct dbConvId
    const convA = h.tabs.get("tab-A")
    const convB = h.tabs.get("tab-B")
    expect(convA).toBe(100)
    expect(convB).toBe(101)
    expect(convA).not.toBe(convB)

    // user turn for A -> A's conv; user turn for B -> B's conv (no cross-contamination)
    const aUser = addTurnCalls.find((c) => c.content === "hello from A")
    const bUser = addTurnCalls.find((c) => c.content === "hello from B")
    expect(aUser).toEqual({ convId: convA, role: "user", content: "hello from A" })
    expect(bUser).toEqual({ convId: convB, role: "user", content: "hello from B" })
    // A's message never landed in B's conversation
    expect(addTurnCalls.some((c) => c.content === "hello from A" && c.convId === convB)).toBe(false)
  })

  // Assertion 3: assistant reply routes to the SAME conversation as its user turn.
  it("routes the assistant reply to the same conversation as its user turn", async () => {
    const h = makeHarness()
    h.openTab("tab-A")
    h.openTab("tab-B")

    await h.persistence.pushUserTurn("tab-A", { turnId: "t1", title: "A", query: "ask A" })
    await h.persistence.pushUserTurn("tab-B", { turnId: "t2", title: "B", query: "ask B" })
    await h.persistence.pushAssistantTurn("tab-A", replyToText({ answer: "reply A" }))
    await h.persistence.pushAssistantTurn("tab-B", replyToText({ answer: "reply B" }))

    const convA = h.tabs.get("tab-A")!
    const convB = h.tabs.get("tab-B")!
    const aAssistant = addTurnCalls.find((c) => c.role === "assistant" && c.content === "reply A")
    const bAssistant = addTurnCalls.find((c) => c.role === "assistant" && c.content === "reply B")
    expect(aAssistant).toEqual({ convId: convA, role: "assistant", content: "reply A" })
    expect(bAssistant).toEqual({ convId: convB, role: "assistant", content: "reply B" })
    // assistant for A shares A's user-turn conversation
    const aUser = addTurnCalls.find((c) => c.role === "user" && c.content === "ask A")!
    expect(aAssistant!.convId).toBe(aUser.convId)
  })

  // Assertion 4: follow-up in a tab that already has a dbConvId -> addTurn, no create.
  it("does NOT re-create a conversation for a follow-up in the same tab", async () => {
    const h = makeHarness()
    h.openTab("tab-A")

    await h.persistence.pushUserTurn("tab-A", { turnId: "t1", title: "A", query: "first" })
    await h.persistence.pushAssistantTurn("tab-A", "reply 1")
    expect(h.api.create).toHaveBeenCalledTimes(1)

    // follow-up
    await h.persistence.pushUserTurn("tab-A", { turnId: "t2", title: "A", query: "second" })
    await h.persistence.pushAssistantTurn("tab-A", "reply 2")

    expect(h.api.create).toHaveBeenCalledTimes(1) // still ONE create
    const convA = h.tabs.get("tab-A")!
    // all four turns went to the same conversation
    expect(addTurnCalls.filter((c) => c.convId === convA)).toHaveLength(4)
    expect(addTurnCalls.every((c) => c.convId === convA)).toBe(true)
  })

  // Assertion 5: tab switch preserves each tab's dbConvId (modelled as the store
  // surviving an interleaved access pattern; the store is the single source of
  // truth ChatScreen reads via getTabConvId).
  it("preserves each tab's dbConvId across tab switches (no reset/clobber)", async () => {
    const h = makeHarness()
    h.openTab("tab-A")
    h.openTab("tab-B")

    await h.persistence.pushUserTurn("tab-A", { turnId: "t1", title: "A", query: "A1" })
    await h.persistence.pushUserTurn("tab-B", { turnId: "t2", title: "B", query: "B1" })
    const convA = h.tabs.get("tab-A")
    const convB = h.tabs.get("tab-B")

    // Simulate switching active tab away (B) and back to A, then a follow-up in A.
    // getTabConvId still resolves A's stored id -> no new create, same conv.
    await h.persistence.pushUserTurn("tab-A", { turnId: "t3", title: "A", query: "A2" })

    expect(h.tabs.get("tab-A")).toBe(convA)
    expect(h.tabs.get("tab-B")).toBe(convB)
    expect(h.api.create).toHaveBeenCalledTimes(2) // A + B only; the follow-up reused A
    expect(addTurnCalls.filter((c) => c.convId === convA && c.content === "A2")).toHaveLength(1)
  })

  // Assertion 6: two sends to the SAME tab, racing under the in-flight create
  // (the assistant turn fires before create settles) -> exactly ONE create, and
  // both turns land in that one conversation.
  it("does not double-create when two sends hit the same tab before create settles", async () => {
    const h = makeHarness({ createDelayMs: 20 })
    h.openTab("tab-A")

    // Kick off the user turn (triggers create) and the assistant turn WITHOUT
    // awaiting the user turn first — the assistant call happens while create is
    // still in-flight. Both must await the SAME create and use its id.
    const userP = h.persistence.pushUserTurn("tab-A", { turnId: "t1", title: "A", query: "racing" })
    const assistantP = h.persistence.pushAssistantTurn("tab-A", "reply")
    await Promise.all([userP, assistantP])

    expect(h.api.create).toHaveBeenCalledTimes(1) // exactly one create
    const convA = h.tabs.get("tab-A")!
    const user = addTurnCalls.find((c) => c.role === "user")
    const assistant = addTurnCalls.find((c) => c.role === "assistant")
    expect(user).toEqual({ convId: convA, role: "user", content: "racing" })
    expect(assistant).toEqual({ convId: convA, role: "assistant", content: "reply" })
  })

  // Two rapid sends in the same tab that BOTH would trigger create (e.g. both
  // see a null dbConvId before the first create settles) collapse to one create.
  it("collapses two concurrent first-sends in the same tab to one create", async () => {
    const h = makeHarness({ createDelayMs: 20 })
    h.openTab("tab-A")

    const p1 = h.persistence.pushUserTurn("tab-A", { turnId: "t1", title: "A", query: "q1" })
    const p2 = h.persistence.pushUserTurn("tab-A", { turnId: "t2", title: "A", query: "q2" })
    await Promise.all([p1, p2])

    expect(h.api.create).toHaveBeenCalledTimes(1)
    const convA = h.tabs.get("tab-A")!
    expect(addTurnCalls.filter((c) => c.role === "user")).toHaveLength(2)
    expect(addTurnCalls.every((c) => c.convId === convA)).toBe(true)
  })

  it("swallows persistence failures so a create/addTurn error never throws", async () => {
    const api: ConversationsPersistenceApi = {
      create: vi.fn(async () => { throw new Error("network down") }),
      addTurn: vi.fn(async () => { throw new Error("network down") }),
    }
    const tabs: TabStore = new Map([["tab-A", null]])
    const persistence = createChatPersistence({
      getApi: async () => api,
      getTabConvId: (id) => tabs.get(id) ?? null,
      setTabConvId: (id, c) => tabs.set(id, c),
    })
    // Must resolve, not reject
    await expect(persistence.pushUserTurn("tab-A", { turnId: "t1", title: "A", query: "x" })).resolves.toBeUndefined()
    await expect(persistence.pushAssistantTurn("tab-A", "y")).resolves.toBeUndefined()
  })
})

describe("replyToText", () => {
  it("returns strings as-is", () => {
    expect(replyToText("plain")).toBe("plain")
  })
  it("extracts the answer field from an AskResponse-ish object", () => {
    expect(replyToText({ answer: "the answer", sources: [] })).toBe("the answer")
  })
  it("falls back to a truncated JSON dump when no answer field", () => {
    const out = replyToText({ foo: "bar" })
    expect(out).toContain("foo")
    expect(out.length).toBeLessThanOrEqual(2000)
  })
})
