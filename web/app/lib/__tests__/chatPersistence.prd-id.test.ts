// createChatPersistence stamps a PRD tab's prd_id onto the conversation it
// creates, so a reopened PRD can later find + rehydrate that conversation via
// conversationsApi.byPrd. Plain (non-PRD) tabs must NOT send a prd_id.
import { describe, it, expect, vi } from "vitest"
import { createChatPersistence } from "../chatPersistence"

function makeDeps(prdId: number | null) {
  const create = vi.fn().mockResolvedValue({ id: 100 })
  const addTurn = vi.fn().mockResolvedValue({})
  let convId: number | null = null
  const deps = {
    getApi: async () => ({ create, addTurn }),
    getTabConvId: () => convId,
    getTabPrdId: () => prdId,
    setTabConvId: (_t: string, id: number) => { convId = id },
  }
  return { deps, create, addTurn }
}

describe("chatPersistence — prd_id stamping", () => {
  it("passes prd_id to create for a PRD tab", async () => {
    const { deps, create } = makeDeps(5)
    const p = createChatPersistence(deps)
    await p.pushUserTurn("tab-1", { turnId: "t1", title: "PRD chat", query: "hi" })
    expect(create).toHaveBeenCalledTimes(1)
    expect(create.mock.calls[0][0]).toMatchObject({ prd_id: 5 })
  })

  it("omits prd_id for a plain (non-PRD) tab", async () => {
    const { deps, create } = makeDeps(null)
    const p = createChatPersistence(deps)
    await p.pushUserTurn("tab-1", { turnId: "t1", title: "Chat", query: "hi" })
    expect(create).toHaveBeenCalledTimes(1)
    expect(create.mock.calls[0][0]).not.toHaveProperty("prd_id")
  })
})
