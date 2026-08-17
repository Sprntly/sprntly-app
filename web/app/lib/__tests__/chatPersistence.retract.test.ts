// The durable half of edit-and-resend: a user turn is persisted the moment it
// is sent, so rewriting a question that was stopped before it answered has to
// take the original row back out — otherwise the same thread reopened from
// history shows the abandoned wording as an unanswered question above the
// edited one.
//
// Everything here is best-effort by design: a retract that cannot be resolved
// must be a silent no-op, never a throw that reaches the UI.
import { describe, it, expect, vi } from "vitest"
import { createChatPersistence } from "../chatPersistence"

function makeDeps(opts: { deleteTurn?: ReturnType<typeof vi.fn> } = {}) {
  const create = vi.fn().mockResolvedValue({ id: 100 })
  const addTurn = vi.fn(async () => ({ id: 77 }))
  const deleteTurn = opts.deleteTurn
  let convId: number | null = null
  const deps = {
    getApi: async () => ({ create, addTurn, ...(deleteTurn ? { deleteTurn } : {}) }),
    getTabConvId: () => convId,
    setTabConvId: (_t: string, id: number) => { convId = id },
  }
  return { deps, create, addTurn, deleteTurn }
}

describe("chatPersistence — retractUserTurn", () => {
  it("deletes the row the turn was written as", async () => {
    const deleteTurn = vi.fn().mockResolvedValue({})
    const { deps } = makeDeps({ deleteTurn })
    const p = createChatPersistence(deps)
    await p.pushUserTurn("tab-1", { turnId: "t1", title: "Chat", query: "waht is it" })
    await p.retractUserTurn("tab-1", "t1")
    expect(deleteTurn).toHaveBeenCalledWith(100, 77)
  })

  it("runs AFTER the write that created the row (the append queue orders them)", async () => {
    // Not a nicety: the row id is only known once addTurn resolves, and the
    // edited turn's own append must land after the deletion of the one it
    // replaces.
    const order: string[] = []
    const deleteTurn = vi.fn(async () => { order.push("delete") })
    const { deps, addTurn } = makeDeps({ deleteTurn })
    addTurn.mockImplementation(async () => { order.push("add"); return { id: 77 } })
    const p = createChatPersistence(deps)
    const write = p.pushUserTurn("tab-1", { turnId: "t1", title: "Chat", query: "q" })
    const retract = p.retractUserTurn("tab-1", "t1")
    await Promise.all([write, retract])
    expect(order).toEqual(["add", "delete"])
  })

  it("is a no-op for a turn this session never wrote (e.g. after a reload)", async () => {
    const deleteTurn = vi.fn().mockResolvedValue({})
    const { deps } = makeDeps({ deleteTurn })
    const p = createChatPersistence(deps)
    await p.pushUserTurn("tab-1", { turnId: "t1", title: "Chat", query: "q" })
    await p.retractUserTurn("tab-1", "some-other-turn")
    expect(deleteTurn).not.toHaveBeenCalled()
  })

  it("does not retry against a row id after a failed delete", async () => {
    const deleteTurn = vi.fn().mockRejectedValue(new Error("409"))
    const { deps } = makeDeps({ deleteTurn })
    const p = createChatPersistence(deps)
    await p.pushUserTurn("tab-1", { turnId: "t1", title: "Chat", query: "q" })
    await p.retractUserTurn("tab-1", "t1")
    await p.retractUserTurn("tab-1", "t1")
    expect(deleteTurn).toHaveBeenCalledTimes(1)
  })

  it("swallows a server failure rather than surfacing it", async () => {
    const deleteTurn = vi.fn().mockRejectedValue(new Error("boom"))
    const { deps } = makeDeps({ deleteTurn })
    const p = createChatPersistence(deps)
    await p.pushUserTurn("tab-1", { turnId: "t1", title: "Chat", query: "q" })
    await expect(p.retractUserTurn("tab-1", "t1")).resolves.toBeUndefined()
  })

  it("does nothing when the API surface has no deleteTurn at all", async () => {
    const { deps } = makeDeps()
    const p = createChatPersistence(deps)
    await p.pushUserTurn("tab-1", { turnId: "t1", title: "Chat", query: "q" })
    await expect(p.retractUserTurn("tab-1", "t1")).resolves.toBeUndefined()
  })

  it("keeps parallel tabs' turn ids apart", async () => {
    const deleteTurn = vi.fn().mockResolvedValue({})
    const { deps, addTurn } = makeDeps({ deleteTurn })
    let next = 10
    addTurn.mockImplementation(async () => ({ id: next++ }))
    const p = createChatPersistence(deps)
    await p.pushUserTurn("tab-a", { turnId: "t1", title: "A", query: "a" })
    await p.pushUserTurn("tab-b", { turnId: "t1", title: "B", query: "b" })
    await p.retractUserTurn("tab-b", "t1")
    expect(deleteTurn).toHaveBeenCalledTimes(1)
    expect(deleteTurn.mock.calls[0][1]).toBe(11)
  })
})
