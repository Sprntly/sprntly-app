// createChatPersistence carries an assistant turn's STRUCTURED reply through to
// the API, so what the turn showed beyond its prose survives the reload.
//
// `content` is a string. For as long as it was the only thing written, "show me
// the PRDs I created" saved the sentence announcing twelve clickable rows and
// none of the rows — reopened from Chat history, the answer read "click one to
// open it with its chat" above empty space.
import { describe, it, expect, vi } from "vitest"
import { createChatPersistence } from "../chatPersistence"

function makeDeps() {
  const create = vi.fn().mockResolvedValue({ id: 100 })
  const addTurn = vi.fn().mockResolvedValue({})
  let convId: number | null = null
  const deps = {
    getApi: async () => ({ create, addTurn }),
    getTabConvId: () => convId,
    setTabConvId: (_t: string, id: number) => { convId = id },
  }
  return { deps, addTurn }
}

const CARDS = [
  { id: 3827, type: "prd", title: "Checkout margin fix", created_at: null, open: { prd_id: 3827 } },
]

describe("chatPersistence — the assistant turn's structured reply", () => {
  it("sends the listing rows alongside the answer text", async () => {
    const { deps, addTurn } = makeDeps()
    const p = createChatPersistence(deps)
    await p.pushUserTurn("tab-1", { turnId: "t1", title: "Chat", query: "show me my prds" })
    await p.pushAssistantTurn("tab-1", "Here are your most recent PRDs.", {
      answer: "Here are your most recent PRDs.",
      artifact_list: CARDS,
    } as never)

    const call = addTurn.mock.calls.at(-1)
    expect(call?.[1]).toBe("assistant")
    expect(call?.[2]).toBe("Here are your most recent PRDs.")
    expect(call?.[4]).toMatchObject({ artifact_list: CARDS })
  })

  it("writes the same content-only row as before when there is nothing structured", async () => {
    const { deps, addTurn } = makeDeps()
    const p = createChatPersistence(deps)
    await p.pushUserTurn("tab-1", { turnId: "t1", title: "Chat", query: "why is churn up" })
    await p.pushAssistantTurn("tab-1", "Churn is up on the invite flow.")

    // Undefined, not an empty object: a plain answer must not start writing a
    // payload the restore path then has to reason about.
    expect(addTurn.mock.calls.at(-1)?.[4]).toBeUndefined()
  })
})
