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
