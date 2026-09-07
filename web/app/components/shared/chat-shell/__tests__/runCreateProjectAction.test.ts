// runCreateProjectAction — "create a project for the billing revamp" makes the
// CONTAINER and says so, or says plainly that it didn't.
//
// The failure this action exists to prevent is the one `create_artifact`'s own
// note records: before it, the chat had no project action at all, so a create
// request landed on `answer` — and the model, knowing the product has projects,
// replied as though it had made one. Nothing existed. So the two things pinned
// here are that the confirmation is written only AFTER the create returns, and
// that a failure says nothing was created.
import { beforeEach, describe, expect, it, vi } from "vitest"
import { runCreateProjectAction } from "../conversation/actions"
import type { ChatIntentEnvelope } from "../../../../lib/api"

const create = vi.fn()

vi.mock("../../../../lib/api", () => ({
  projectsApi: { create: (...args: unknown[]) => create(...args) },
  slackShareApi: {},
  ticketDataApi: {},
}))

function envelope(overrides: Partial<ChatIntentEnvelope> = {}): ChatIntentEnvelope {
  return {
    intent: "create_project",
    confidence: 0.9,
    task: "Billing revamp",
    instruction: null,
    artifact_kind: null,
    artifact_type: null,
    artifact_query: null,
    artifact_template_id: null,
    artifact_template_name: null,
    reason: "test",
    source: "llm",
    prd_id: null,
    prd_title: null,
    ...overrides,
  } as ChatIntentEnvelope
}

beforeEach(() => {
  create.mockReset()
})

describe("runCreateProjectAction", () => {
  it("creates the project with the planner's name and hands it to the surface", async () => {
    create.mockResolvedValue({ id: 42, name: "Billing revamp" })
    const emitTurn = vi.fn()
    const onProjectCreated = vi.fn()

    await runCreateProjectAction("create a project for the billing revamp", envelope(), {
      emitTurn,
      onProjectCreated,
    })

    expect(create).toHaveBeenCalledWith({ name: "Billing revamp", origin: "manual" })
    expect(onProjectCreated).toHaveBeenCalledWith({ id: 42, name: "Billing revamp" })
    expect(emitTurn.mock.calls[0][0].reply.answer).toContain("Billing revamp")
    // No source thread known to this surface — the "add members" copy, not a
    // claim that something came along with it.
    expect(emitTurn.mock.calls[0][0].reply.answer).toContain("add members")
  })

  // BUG: "start a project with this" from an ongoing chat used to land in an
  // EMPTY project — no history, no artifacts — because the create call never
  // carried the conversation it came from. `sourceConversationId` is the fix's
  // frontend half; the backend half (bind + backfill) is
  // `test_projects_routes.py`'s `test_create_with_conversation_id_*`.
  it("passes the surface's bound conversation through, and confirms that it came along", async () => {
    create.mockResolvedValue({ id: 43, name: "Billing revamp" })
    const emitTurn = vi.fn()

    await runCreateProjectAction("start a project with this", envelope(), {
      emitTurn,
      sourceConversationId: async () => 55,
    })

    expect(create).toHaveBeenCalledWith({
      name: "Billing revamp", origin: "manual", conversation_id: 55,
    })
    const answer = emitTurn.mock.calls[0][0].reply.answer
    expect(answer).toContain("came with it")
    expect(answer).not.toContain("add members")
  })

  it("a null sourceConversationId (resolution failed) is a safe no-op, same as none at all", async () => {
    create.mockResolvedValue({ id: 44, name: "Billing revamp" })
    const emitTurn = vi.fn()

    await runCreateProjectAction("start a project with this", envelope(), {
      emitTurn,
      sourceConversationId: async () => null,
    })

    expect(create).toHaveBeenCalledWith({ name: "Billing revamp", origin: "manual" })
  })

  it("says nothing was created when the create fails, and does not navigate", async () => {
    create.mockRejectedValue(new Error("seat limit reached"))
    const emitTurn = vi.fn()
    const onProjectCreated = vi.fn()

    await runCreateProjectAction("make a project for onboarding", envelope(), {
      emitTurn,
      onProjectCreated,
    })

    const answer = emitTurn.mock.calls[0][0].reply.answer
    expect(answer).toContain("couldn't create the project")
    expect(answer).toContain("seat limit reached")
    expect(answer).toContain("Nothing was created")
    expect(onProjectCreated).not.toHaveBeenCalled()
  })

  it("asks what to call it rather than minting an untitled container", async () => {
    const emitTurn = vi.fn()

    await runCreateProjectAction("create a project", envelope({ task: "   " }), { emitTurn })

    expect(create).not.toHaveBeenCalled()
    expect(emitTurn.mock.calls[0][0].reply.answer).toContain("what should it be called")
  })

  it("a surface that supplies no onProjectCreated still gets its confirmation", async () => {
    create.mockResolvedValue({ id: 7, name: "Pricing 2027" })
    const emitTurn = vi.fn()

    await runCreateProjectAction("start a project called Pricing 2027", envelope(), { emitTurn })

    expect(create).toHaveBeenCalled()
    expect(emitTurn.mock.calls[0][0].reply.answer).toContain("Pricing 2027")
  })
})

// ── The create is VISIBLE while it runs ──────────────────────────────────────
//
// REPORTED: "no indication that it is creating a project, and then it
// automatically takes me to the project screen". The create binds the
// conversation and sweeps the thread's artifacts onto the new project, so it is
// not instant — and it used to run with the composer cleared and nothing on
// screen, the project itself being the first thing the user saw.
//
// `runActionTurn` is the surface's async-command lifecycle (optimistic turn →
// busy → settle → persist), the same one the PRD edit and the Slack share use.
// These pin that the action goes through it, that the round trip which resolves
// the conversation happens INSIDE it, and that the navigation waits for it.
describe("runCreateProjectAction — the wait is on screen", () => {
  /** A stand-in for the surface's async-turn primitive that records the order
   *  things happened in, the way the real one does: seed, run, settle. */
  function recordingTurn(log: string[]) {
    return async (query: string, worker: () => Promise<{ reply: { answer: string } }>) => {
      log.push(`seeded:${query}`)
      const patch = await worker()
      log.push(`settled:${patch.reply.answer.slice(0, 20)}`)
      return { turnId: "t1" }
    }
  }

  it("runs through the async-turn lifecycle, not a settled turn after the fact", async () => {
    create.mockResolvedValue({ id: 42, name: "Billing revamp" })
    const log: string[] = []
    const emitTurn = vi.fn()

    await runCreateProjectAction("create a project for the billing revamp", envelope(), {
      emitTurn,
      runActionTurn: recordingTurn(log),
    })

    // The turn exists BEFORE the work runs — that is the whole indication.
    expect(log[0]).toBe("seeded:create a project for the billing revamp")
    expect(log[1]).toContain("settled:")
    // …and it is ONE turn, settled in place, not a second one posted beside it.
    expect(emitTurn).not.toHaveBeenCalled()
  })

  it("resolves the conversation INSIDE the turn, not in front of it", async () => {
    // On a fresh tab this call CREATES the conversation row. Awaited by the
    // caller it was the first half of the blank window; it has to happen where
    // the wait state can cover it.
    create.mockResolvedValue({ id: 42, name: "Billing revamp" })
    const log: string[] = []

    await runCreateProjectAction("start a project with this", envelope(), {
      emitTurn: vi.fn(),
      runActionTurn: recordingTurn(log),
      sourceConversationId: async () => { log.push("resolved-conversation"); return 55 },
    })

    expect(log).toEqual([
      "seeded:start a project with this",
      "resolved-conversation",
      expect.stringContaining("settled:"),
    ])
    expect(create).toHaveBeenCalledWith({
      name: "Billing revamp", origin: "manual", conversation_id: 55,
    })
  })

  it("navigates only AFTER the turn settles, so the confirmation is not raced", async () => {
    // The thread travels WITH the project, so a redirect fired mid-flight could
    // land it there missing its own last line.
    create.mockResolvedValue({ id: 42, name: "Billing revamp" })
    const log: string[] = []

    await runCreateProjectAction("create a project for the billing revamp", envelope(), {
      emitTurn: vi.fn(),
      runActionTurn: recordingTurn(log),
      onProjectCreated: () => { log.push("navigated") },
    })

    expect(log[log.length - 1]).toBe("navigated")
  })

  it("a failed create settles the turn and navigates NOWHERE", async () => {
    create.mockRejectedValue(new Error("seat limit reached"))
    const log: string[] = []
    const onProjectCreated = vi.fn()
    let settled = ""

    await runCreateProjectAction("make a project for onboarding", envelope(), {
      emitTurn: vi.fn(),
      runActionTurn: async (_q, worker) => {
        const patch = await worker()
        settled = patch.reply.answer
        log.push("settled")
        return { turnId: "t1" }
      },
      onProjectCreated,
    })

    expect(settled).toContain("Nothing was created")
    expect(settled).toContain("seat limit reached")
    expect(onProjectCreated).not.toHaveBeenCalled()
  })

  it("a surface with no async primitive still works, exactly as before", async () => {
    // The fallback is the pre-change behaviour verbatim: one settled turn, no
    // wait state, because that surface has nowhere to put one.
    create.mockResolvedValue({ id: 9, name: "Pricing 2027" })
    const emitTurn = vi.fn()
    const onProjectCreated = vi.fn()

    await runCreateProjectAction("start a project called Pricing 2027", envelope(), {
      emitTurn,
      onProjectCreated,
    })

    expect(emitTurn).toHaveBeenCalledTimes(1)
    expect(emitTurn.mock.calls[0][0].reply.answer).toContain("Pricing 2027")
    expect(emitTurn.mock.calls[0][0].query).toBe("start a project called Pricing 2027")
    expect(onProjectCreated).toHaveBeenCalledWith({ id: 9, name: "Pricing 2027" })
  })
})
