// @vitest-environment jsdom
//
// useProjectPrivateThread — the structured generation-clarify gate (§D):
// `runGeneratePrd` runs `prdApi.clarifyTask` FIRST; insufficient parks
// generation on the turn's `clarify` field (no `runPrdGenerationFromTask`
// call yet); `submitClarify` folds the answers via the shared
// `clarifyAnswersText` and generates exactly once; `skipClarify` generates
// with the original task; sufficient/fail-open generates immediately,
// unchanged. The pre-existing single-pick `pickOptions` edit-target gate
// (`onClarify`/`pickOption`) is a SEPARATE, untouched path — proven here too.
// Mirrors `useProjectPrivateThread.fixes.test.tsx`'s own mocking pattern.
import * as React from "react"
import { act, cleanup, render, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const runAskGenerationMock = vi.fn()
const resolveIntentMock = vi.fn()
const addArtifactMock = vi.fn()
const clarifyTaskMock = vi.fn()
const individualChatMock = vi.fn((id: number) =>
  Promise.resolve({
    id: 9001, project_id: id, user_id: "u1", kind: "individual" as const,
    created_at: "2026-08-11T00:00:00Z", updated_at: "2026-08-11T00:00:00Z",
  }),
)
const individualTurnsMock = vi.fn().mockResolvedValue([])
const ledgerMock = vi.fn().mockResolvedValue([])
const persistIndividualTurnsMock = vi.fn().mockResolvedValue({ ok: true })
const runPrdGenerationFromTaskMock = vi.fn()

vi.mock("../../../../../lib/runAskGeneration", async () => {
  const actual = await vi.importActual<typeof import("../../../../../lib/runAskGeneration")>("../../../../../lib/runAskGeneration")
  return { ...actual, runAskGeneration: (...a: unknown[]) => runAskGenerationMock(...a), resumeAskGeneration: vi.fn(), getPendingAsk: vi.fn().mockReturnValue(null) }
})
vi.mock("../../../../../lib/runPrdGeneration", () => ({ runPrdGenerationFromTask: (...a: unknown[]) => runPrdGenerationFromTaskMock(...a) }))
vi.mock("../../../../../lib/poll", () => ({ sleepUntilNextPoll: () => Promise.resolve() }))
vi.mock("../../../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../../../lib/api")>("../../../../../lib/api")
  return {
    ...actual,
    prdApi: { ...actual.prdApi, clarifyTask: (...a: unknown[]) => clarifyTaskMock(...a) },
    projectsApi: {
      ...actual.projectsApi,
      individualChat: (...a: unknown[]) => individualChatMock(...(a as [number])),
      individualTurns: (...a: unknown[]) => individualTurnsMock(...a),
      ledger: (...a: unknown[]) => ledgerMock(...a),
      addArtifact: (...a: unknown[]) => addArtifactMock(...a),
      resolveIntent: (...a: unknown[]) => resolveIntentMock(...a),
      persistIndividualTurns: (...a: unknown[]) => persistIndividualTurnsMock(...a),
    },
  }
})
vi.mock("../../../../../context/CompanyContext", () => ({ useCompany: () => ({ activeCompany: "acme", setActiveCompany: vi.fn(), activeCompanyDisplayName: "Acme" }) }))
vi.mock("../../../../../context/WorkspaceContext", () => ({
  useWorkspace: () => ({ loading: false, profile: null, workspace: { feature_flags: { chat_intent_envelope: true } }, refresh: async () => {} }),
}))
vi.mock("../../../../../lib/auth", () => ({ useAuth: () => ({ kind: "authed" as const, user: { id: "u1" } }) }))

const { realtimeSpy } = vi.hoisted(() => ({ realtimeSpy: vi.fn() }))
vi.mock("../useRealtimeChannel", () => ({
  useRealtimeChannel: (topic: string | null, handlers: { onEvent?: (e: string, p: unknown) => void; onReconcile?: () => void }) => {
    realtimeSpy(topic, handlers)
    return { status: "degraded", degraded: true, presenceMembers: [], typers: [], sendTyping: vi.fn() }
  },
}))

import { useProjectPrivateThread, type UseProjectPrivateThread } from "../useProjectPrivateThread"

let latest: UseProjectPrivateThread | null = null
function Harness({ projectId }: { projectId: number }) {
  const engine = useProjectPrivateThread(projectId)
  latest = engine
  return React.createElement("div", { "data-testid": "busy" }, String(engine.busy))
}
const flush = async () => {
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
    await Promise.resolve()
  })
}

beforeEach(() => {
  runAskGenerationMock.mockReset()
  resolveIntentMock.mockReset()
  addArtifactMock.mockReset()
  addArtifactMock.mockResolvedValue({ project_id: 3, artifact_type: "prd", artifact_id: 11 })
  clarifyTaskMock.mockReset()
  individualChatMock.mockClear()
  individualTurnsMock.mockReset().mockResolvedValue([])
  ledgerMock.mockReset().mockResolvedValue([])
  persistIndividualTurnsMock.mockReset().mockResolvedValue({ ok: true })
  runPrdGenerationFromTaskMock.mockReset()
  realtimeSpy.mockClear()
  latest = null
})
afterEach(() => cleanup())

function findClarifyTurn() {
  return latest!.turns.find((t) => !!t.clarify)
}

describe("useProjectPrivateThread — structured generation-clarify gate (§D)", () => {
  it("test_generate_parks_on_clarify_questions (AC3)", async () => {
    resolveIntentMock.mockResolvedValue({ intent: "generate_prd", confidence: 0.9, task: "dark mode on mobile" })
    clarifyTaskMock.mockResolvedValue({
      sufficient: false,
      questions: [{ prompt: "Who are the target users?", options: ["Admins", "End users"] }],
      missing: ["Target users"],
    })
    render(React.createElement(Harness, { projectId: 3 }))
    await flush()
    act(() => latest!.send("generate a PRD for dark mode on mobile"))
    await flush()

    expect(runPrdGenerationFromTaskMock).not.toHaveBeenCalled()
    const turn = findClarifyTurn()
    expect(turn).toBeTruthy()
    expect(turn!.clarify!.questions).toHaveLength(1)
    expect(turn!.pending).not.toBe(true)
    expect(latest!.busy).toBe(false)
  })

  it("test_submit_clarify_folds_answers_and_generates_once (AC3, AC6)", async () => {
    resolveIntentMock.mockResolvedValue({ intent: "generate_prd", confidence: 0.9, task: "dark mode on mobile" })
    clarifyTaskMock.mockResolvedValue({
      sufficient: false,
      questions: [{ prompt: "Who are the target users?", options: ["Admins", "End users"] }],
      missing: ["Target users"],
    })
    runPrdGenerationFromTaskMock.mockResolvedValue({ ok: true, prd: { prd_id: 11, title: "Dark mode" } })

    render(React.createElement(Harness, { projectId: 3 }))
    await flush()
    act(() => latest!.send("generate a PRD for dark mode on mobile"))
    await flush()
    const turnId = findClarifyTurn()!.id

    act(() => latest!.submitClarify(turnId, [{ prompt: "Who are the target users?", answer: "Admins" }]))
    await flush()
    await waitFor(() => expect(latest!.busy).toBe(false))

    expect(runPrdGenerationFromTaskMock).toHaveBeenCalledTimes(1)
    const [foldedTask] = runPrdGenerationFromTaskMock.mock.calls[0]
    expect(foldedTask).toContain("dark mode on mobile")
    expect(foldedTask).toContain("Who are the target users?")
    expect(foldedTask).toContain("Admins")
    expect(addArtifactMock).toHaveBeenCalledWith(3, "prd", 11)
    const turn = latest!.turns.find((t) => t.id === turnId)
    expect(turn!.clarify!.resolved).toEqual({ answers: [{ prompt: "Who are the target users?", answer: "Admins" }], mode: "card" })
    expect(turn!.reply?.answer).toContain("Dark mode")
  })

  it("test_skip_clarify_generates_original_task (AC3)", async () => {
    resolveIntentMock.mockResolvedValue({ intent: "generate_prd", confidence: 0.9, task: "dark mode on mobile" })
    clarifyTaskMock.mockResolvedValue({
      sufficient: false,
      questions: [{ prompt: "Who are the target users?", options: [] }],
      missing: ["Target users"],
    })
    runPrdGenerationFromTaskMock.mockResolvedValue({ ok: true, prd: { prd_id: 12, title: "Dark mode v2" } })

    render(React.createElement(Harness, { projectId: 3 }))
    await flush()
    act(() => latest!.send("generate a PRD for dark mode on mobile"))
    await flush()
    const turnId = findClarifyTurn()!.id

    act(() => latest!.skipClarify(turnId))
    await flush()
    await waitFor(() => expect(latest!.busy).toBe(false))

    expect(runPrdGenerationFromTaskMock).toHaveBeenCalledTimes(1)
    expect(runPrdGenerationFromTaskMock.mock.calls[0][0]).toBe("dark mode on mobile")
    const turn = latest!.turns.find((t) => t.id === turnId)
    expect(turn!.clarify!.resolved).toEqual({ answers: [], mode: "skip" })
  })

  it("test_no_questions_generates_immediately (AC3, unchanged)", async () => {
    resolveIntentMock.mockResolvedValue({ intent: "generate_prd", confidence: 0.9, task: "dark mode on mobile" })
    clarifyTaskMock.mockResolvedValue({ sufficient: true, questions: [], missing: [] })
    runPrdGenerationFromTaskMock.mockResolvedValue({ ok: true, prd: { prd_id: 13, title: "Dark mode v3" } })

    render(React.createElement(Harness, { projectId: 3 }))
    await flush()
    act(() => latest!.send("generate a PRD for dark mode on mobile"))
    await flush()
    await waitFor(() => expect(latest!.busy).toBe(false))

    expect(runPrdGenerationFromTaskMock).toHaveBeenCalledTimes(1)
    expect(runPrdGenerationFromTaskMock.mock.calls[0][0]).toBe("dark mode on mobile")
    expect(findClarifyTurn()).toBeUndefined()
    const turn = latest!.turns[latest!.turns.length - 1]
    expect(turn.reply?.answer).toContain("Dark mode v3")
  })

  it("test_clarify_fail_open_generates_immediately_on_error", async () => {
    resolveIntentMock.mockResolvedValue({ intent: "generate_prd", confidence: 0.9, task: "dark mode on mobile" })
    clarifyTaskMock.mockRejectedValue(new Error("network"))
    runPrdGenerationFromTaskMock.mockResolvedValue({ ok: true, prd: { prd_id: 14, title: "Dark mode v4" } })

    render(React.createElement(Harness, { projectId: 3 }))
    await flush()
    act(() => latest!.send("generate a PRD for dark mode on mobile"))
    await flush()
    await waitFor(() => expect(latest!.busy).toBe(false))

    expect(runPrdGenerationFromTaskMock).toHaveBeenCalledTimes(1)
    expect(findClarifyTurn()).toBeUndefined()
  })

  it("test_edit_pickoptions_path_untouched (AC4)", async () => {
    // `intent: "clarify"` is `dispatchChatIntent`'s EDIT-target disambiguation
    // envelope, routed to the pre-existing `onClarify` executor (untouched by
    // this ticket) — NOT the new structured generation-clarify gate, which
    // only ever engages inside `runGeneratePrd`/`generate_prd`.
    resolveIntentMock.mockResolvedValue({
      intent: "clarify", confidence: 0.9, task: null,
      clarification: "Which PRD did you mean?",
      prd_options: [{ id: 501, title: "Onboarding" }, { id: 502, title: "Billing" }],
      reason: "ambiguous target", source: "llm", prd_id: null, prd_title: null,
    })
    render(React.createElement(Harness, { projectId: 3 }))
    await flush()
    act(() => latest!.send("tighten the scope"))
    await flush()

    // The new structured generation-clarify gate was NOT engaged.
    expect(clarifyTaskMock).not.toHaveBeenCalled()
    expect(findClarifyTurn()).toBeUndefined()

    // The pre-existing single-pick edit-target gate still renders its own
    // `pickOptions` — a DIFFERENT field, unaffected by this ticket.
    const turn = latest!.turns[latest!.turns.length - 1]
    expect(turn.pickOptions).toEqual([
      { id: "501", title: "Onboarding", instruction: "tighten the scope" },
      { id: "502", title: "Billing", instruction: "tighten the scope" },
    ])

    // `pickOption` still closes the loop exactly as before.
    persistIndividualTurnsMock.mockClear()
    const prdChatEditMock = vi.fn().mockResolvedValue({ edited: true, summary: "Updated the PRD." })
    const { projectsApi } = await import("../../../../../lib/api")
    projectsApi.prdChatEdit = prdChatEditMock
    act(() => latest!.pickOption(turn.id, { id: "501", title: "Onboarding", instruction: "tighten the scope" }))
    await flush()
    expect(prdChatEditMock).toHaveBeenCalledWith(3, "tighten the scope", 501, expect.any(String))
  })
})
