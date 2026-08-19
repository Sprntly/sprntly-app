// The durable half of editing or retrying a past prompt: the thread on screen
// rewinds to the turn being re-asked, and the persisted conversation has to
// rewind with it — otherwise the same conversation reopened from history shows
// the old question, its old answer, AND the new pair.
//
// Everything here is best-effort by design: a rewind that cannot be resolved
// must be a silent no-op, never a throw that reaches the UI.
import { describe, it, expect, vi } from "vitest"
import { createChatPersistence } from "../chatPersistence"

function makeDeps(opts: { rewindToTurn?: ReturnType<typeof vi.fn> } = {}) {
  const create = vi.fn().mockResolvedValue({ id: 100 })
  const addTurn = vi.fn(async () => ({ id: 77 }))
  const rewindToTurn = opts.rewindToTurn
  let convId: number | null = null
  const deps = {
    getApi: async () => ({ create, addTurn, ...(rewindToTurn ? { rewindToTurn } : {}) }),
    getTabConvId: () => convId,
    setTabConvId: (_t: string, id: number) => { convId = id },
  }
  return { deps, create, addTurn, rewindToTurn }
}

describe("chatPersistence — rewindToUserTurn", () => {
  it("rewinds to the row the turn was written as", async () => {
    const rewindToTurn = vi.fn().mockResolvedValue({})
    const { deps } = makeDeps({ rewindToTurn })
    const p = createChatPersistence(deps)
    await p.pushUserTurn("tab-1", { turnId: "t1", title: "Chat", query: "waht is it" })
    await p.rewindToUserTurn("tab-1", "t1")
    expect(rewindToTurn).toHaveBeenCalledWith(100, 77)
  })

  it("runs AFTER the write that created the row (the append queue orders them)", async () => {
    // Not a nicety: the row id is only known once addTurn resolves, and the
    // re-asked turn's own append must land after the rewind that removed the
    // one it replaces.
    const order: string[] = []
    const rewindToTurn = vi.fn(async () => { order.push("rewind") })
    const { deps, addTurn } = makeDeps({ rewindToTurn })
    addTurn.mockImplementation(async () => { order.push("add"); return { id: 77 } })
    const p = createChatPersistence(deps)
    const write = p.pushUserTurn("tab-1", { turnId: "t1", title: "Chat", query: "q" })
    const rewind = p.rewindToUserTurn("tab-1", "t1")
    await Promise.all([write, rewind])
    expect(order).toEqual(["add", "rewind"])
  })

  it("uses the hint for a turn this session never wrote (a rehydrated thread)", async () => {
    // Editing a prompt in a chat reopened from history: the id map covers only
    // what this session sent, so the row id rides on the restored turn itself.
    const rewindToTurn = vi.fn().mockResolvedValue({})
    const { deps } = makeDeps({ rewindToTurn })
    const p = createChatPersistence(deps)
    // Give the tab a conversation without recording an id for `old-turn`.
    await p.pushUserTurn("tab-1", { turnId: "recent", title: "Chat", query: "q" })
    await p.rewindToUserTurn("tab-1", "old-turn", 42)
    expect(rewindToTurn).toHaveBeenCalledWith(100, 42)
  })

  it("prefers this session's own id over a stale hint", async () => {
    const rewindToTurn = vi.fn().mockResolvedValue({})
    const { deps } = makeDeps({ rewindToTurn })
    const p = createChatPersistence(deps)
    await p.pushUserTurn("tab-1", { turnId: "t1", title: "Chat", query: "q" })
    await p.rewindToUserTurn("tab-1", "t1", 999)
    expect(rewindToTurn).toHaveBeenCalledWith(100, 77)
  })

  it("is a no-op when neither source can name the row", async () => {
    const rewindToTurn = vi.fn().mockResolvedValue({})
    const { deps } = makeDeps({ rewindToTurn })
    const p = createChatPersistence(deps)
    await p.pushUserTurn("tab-1", { turnId: "t1", title: "Chat", query: "q" })
    await p.rewindToUserTurn("tab-1", "some-other-turn")
    expect(rewindToTurn).not.toHaveBeenCalled()
  })

  it("does not retry against a row id after a failed rewind", async () => {
    const rewindToTurn = vi.fn().mockRejectedValue(new Error("409"))
    const { deps } = makeDeps({ rewindToTurn })
    const p = createChatPersistence(deps)
    await p.pushUserTurn("tab-1", { turnId: "t1", title: "Chat", query: "q" })
    await p.rewindToUserTurn("tab-1", "t1")
    await p.rewindToUserTurn("tab-1", "t1")
    expect(rewindToTurn).toHaveBeenCalledTimes(1)
  })

  it("swallows a server failure rather than surfacing it", async () => {
    const rewindToTurn = vi.fn().mockRejectedValue(new Error("boom"))
    const { deps } = makeDeps({ rewindToTurn })
    const p = createChatPersistence(deps)
    await p.pushUserTurn("tab-1", { turnId: "t1", title: "Chat", query: "q" })
    await expect(p.rewindToUserTurn("tab-1", "t1")).resolves.toBeUndefined()
  })

  it("does nothing when the API surface has no rewindToTurn at all", async () => {
    const { deps } = makeDeps()
    const p = createChatPersistence(deps)
    await p.pushUserTurn("tab-1", { turnId: "t1", title: "Chat", query: "q" })
    await expect(p.rewindToUserTurn("tab-1", "t1")).resolves.toBeUndefined()
  })

  it("keeps parallel tabs' turn ids apart", async () => {
    const rewindToTurn = vi.fn().mockResolvedValue({})
    const { deps, addTurn } = makeDeps({ rewindToTurn })
    let next = 10
    addTurn.mockImplementation(async () => ({ id: next++ }))
    const p = createChatPersistence(deps)
    await p.pushUserTurn("tab-a", { turnId: "t1", title: "A", query: "a" })
    await p.pushUserTurn("tab-b", { turnId: "t1", title: "B", query: "b" })
    await p.rewindToUserTurn("tab-b", "t1")
    expect(rewindToTurn).toHaveBeenCalledTimes(1)
    expect(rewindToTurn.mock.calls[0][1]).toBe(11)
  })
})
