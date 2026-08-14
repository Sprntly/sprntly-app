// @vitest-environment jsdom
//
// ProjectIndividualChat — private classify→dispatch (AD-P13a / AC14). With
// the classifier flag ON, a send classifies via the project-scoped
// `projectsApi.resolveIntent` (server-resolves the edit target over this
// project's own PRDs — NOT `chatIntentApi.resolve(question, {})`, which
// sends no target and lets the `_NEEDS_PRD` downgrade rewrite `edit_prd` to
// `answer`) and routes through the SHARED `dispatchChatIntent` primitive:
// `edit_prd` hits the project chat-edit route, `generate_prd`/
// `generate_tickets` hit the generate routes THEN auto-attach, `answer`
// (and any classify failure) falls open to the prior `/v1/ask`-only send —
// see `ProjectIndividualChat.test.tsx` for the flag-OFF / byte-identical-
// send suite.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false, media: query, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    }) as unknown as MediaQueryList
}

const runAskGenerationMock = vi.fn()
const resolveIntentMock = vi.fn()
const prdChatEditMock = vi.fn()
const addArtifactMock = vi.fn()
const individualChatMock = vi.fn((id: number) =>
  Promise.resolve({
    id: 9001, project_id: id, user_id: "u1", kind: "individual" as const,
    created_at: "2026-08-11T00:00:00Z", updated_at: "2026-08-11T00:00:00Z",
  }),
)
const individualTurnsMock = vi.fn().mockResolvedValue([])
const ledgerMock = vi.fn().mockResolvedValue([])
const generateFromInsightMock = vi.fn()
const getJobMock = vi.fn()

vi.mock("../../../../../lib/runAskGeneration", async () => {
  const actual = await vi.importActual<typeof import("../../../../../lib/runAskGeneration")>(
    "../../../../../lib/runAskGeneration",
  )
  return {
    ...actual,
    runAskGeneration: (...a: unknown[]) => runAskGenerationMock(...a),
    resumeAskGeneration: vi.fn(),
    getPendingAsk: vi.fn().mockReturnValue(null),
  }
})

const runPrdGenerationFromTaskMock = vi.fn()
vi.mock("../../../../../lib/runPrdGeneration", () => ({
  runPrdGenerationFromTask: (...a: unknown[]) => runPrdGenerationFromTaskMock(...a),
}))

vi.mock("../../../../../lib/poll", () => ({
  sleepUntilNextPoll: () => Promise.resolve(),
}))

vi.mock("../../../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../../../lib/api")>("../../../../../lib/api")
  return {
    ...actual,
    projectsApi: {
      ...actual.projectsApi,
      individualChat: (...a: unknown[]) => individualChatMock(...(a as [number])),
      individualTurns: (...a: unknown[]) => individualTurnsMock(...a),
      ledger: (...a: unknown[]) => ledgerMock(...a),
      prdChatEdit: (...a: unknown[]) => prdChatEditMock(...a),
      addArtifact: (...a: unknown[]) => addArtifactMock(...a),
      // Same `resolveIntentMock` the pre-fix suite mounted on
      // `chatIntentApi.resolve` — re-mounted here because the component
      // now classifies via the project-scoped resolver instead. Mock-
      // target rename only; the 5 cases below are unmodified.
      resolveIntent: (...a: unknown[]) => resolveIntentMock(...a),
    },
    storiesApi: {
      ...actual.storiesApi,
      generateFromInsight: (...a: unknown[]) => generateFromInsightMock(...a),
      getJob: (...a: unknown[]) => getJobMock(...a),
    },
  }
})

vi.mock("../../../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme", setActiveCompany: vi.fn(), activeCompanyDisplayName: "Acme" }),
}))
vi.mock("../../../../../context/WorkspaceContext", () => ({
  useWorkspace: () => ({
    loading: false, profile: null,
    workspace: { feature_flags: { chat_intent_envelope: true } },
    refresh: async () => {},
  }),
}))
vi.mock("../../../../../lib/auth", () => ({
  useAuth: () => ({ kind: "authed" as const, user: { id: "u1" } }),
}))
// Pre-existing, out-of-scope gap this ticket does NOT fix (verified against
// unmodified origin/release/projects@ca2f6f92 — `ProjectIndividualChat.test.
// tsx`'s own component-rendering tests are ALREADY red there): the always-
// mounted `ProjectPrdPatchBanner` (the retired propose/review PRD-edit
// banner, left in place for a later ticket to delete) calls `useNavigation()`
// unconditionally, which throws without a `NavigationProvider`. Minimal mock
// so THIS file's dispatch coverage isn't blocked by an unrelated,
// pre-existing bug outside this ticket's surface.
vi.mock("../../../../../context/NavigationContext", () => ({
  useNavigation: () => ({ showToast: vi.fn() }),
}))

import { ProjectIndividualChat } from "../ProjectIndividualChat"

beforeEach(() => {
  runAskGenerationMock.mockReset()
  resolveIntentMock.mockReset()
  prdChatEditMock.mockReset()
  addArtifactMock.mockReset()
  individualChatMock.mockClear()
  individualTurnsMock.mockReset().mockResolvedValue([])
  ledgerMock.mockReset().mockResolvedValue([])
  generateFromInsightMock.mockReset()
  getJobMock.mockReset()
  runPrdGenerationFromTaskMock.mockReset()
})
afterEach(() => cleanup())

async function sendMessage(text: string) {
  render(React.createElement(ProjectIndividualChat, { projectId: 202 }))
  const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
  await act(async () => {
    fireEvent.change(textarea, { target: { value: text } })
  })
  await act(async () => {
    fireEvent.click(screen.getByLabelText("Send"))
  })
}

describe("ProjectIndividualChat — classify→dispatch (flag on)", () => {
  it("edit_prd calls the project route, not /v1/ask", async () => {
    resolveIntentMock.mockResolvedValue({
      intent: "edit_prd", confidence: 0.9, task: null,
      instruction: "tighten the problem statement", reason: "edit", source: "llm",
      prd_id: null, prd_title: null,
    })
    prdChatEditMock.mockResolvedValue({
      edited: true, prd: { payload_md: "<p>tightened</p>" },
      sections_changed: ["Problem"], summary: "Tightened the problem statement.",
    })

    await sendMessage("tighten the problem statement")

    await waitFor(() => expect(prdChatEditMock).toHaveBeenCalledTimes(1))
    expect(prdChatEditMock).toHaveBeenCalledWith(202, "tighten the problem statement")
    expect(runAskGenerationMock).not.toHaveBeenCalled()
    await waitFor(() =>
      expect(screen.getByTestId("ic-msg-agent").textContent).toContain("Tightened the problem statement."),
    )
  })

  it("generate_tickets kicks off the generate route THEN auto-attaches", async () => {
    resolveIntentMock.mockResolvedValue({
      intent: "generate_tickets", confidence: 0.9, task: "the webhook retry work",
      instruction: null, reason: "tickets", source: "llm", prd_id: null, prd_title: null,
    })
    generateFromInsightMock.mockResolvedValue({ job_id: 55, status: "generating", ticket_set_id: 7 })
    getJobMock.mockResolvedValue({ status: "ready", stories: [] })
    addArtifactMock.mockResolvedValue({ project_id: 202, artifact_type: "ticket_set", artifact_id: 7 })

    await sendMessage("break this into work items")

    await waitFor(() => expect(addArtifactMock).toHaveBeenCalledTimes(1))
    expect(generateFromInsightMock).toHaveBeenCalledWith("break this into work items", null)
    expect(addArtifactMock).toHaveBeenCalledWith(202, "ticket_set", 7)
    expect(runAskGenerationMock).not.toHaveBeenCalled()
    await waitFor(() => expect(screen.getByTestId("ic-msg-agent")).toBeTruthy())
  })

  it("generate_prd kicks off the generate route THEN auto-attaches", async () => {
    resolveIntentMock.mockResolvedValue({
      intent: "generate_prd", confidence: 0.9, task: "dark mode on mobile",
      instruction: null, reason: "generate", source: "llm", prd_id: null, prd_title: null,
    })
    runPrdGenerationFromTaskMock.mockResolvedValue({
      ok: true, prd: { prd_id: 501, title: "Dark mode", metaLine: "", sections: [] },
    })
    addArtifactMock.mockResolvedValue({ project_id: 202, artifact_type: "prd", artifact_id: 501 })

    await sendMessage("generate a PRD for dark mode")

    await waitFor(() => expect(addArtifactMock).toHaveBeenCalledTimes(1))
    expect(runPrdGenerationFromTaskMock).toHaveBeenCalledWith("dark mode on mobile")
    expect(addArtifactMock).toHaveBeenCalledWith(202, "prd", 501)
    expect(runAskGenerationMock).not.toHaveBeenCalled()
  })

  it("an answer verdict calls /v1/ask, same as the flag-off send", async () => {
    resolveIntentMock.mockResolvedValue({
      intent: "answer", confidence: 0.9, task: null, instruction: null,
      reason: "question", source: "llm", prd_id: null, prd_title: null,
    })
    runAskGenerationMock.mockResolvedValue({
      answer: "Flat $49/mo.", key_points: [], citations: [], confidence: 1, unanswered: "",
    })

    await sendMessage("what did we land on for pricing?")

    await waitFor(() => expect(runAskGenerationMock).toHaveBeenCalledTimes(1))
    expect(runAskGenerationMock).toHaveBeenCalledWith(
      "what did we land on for pricing?", "acme", "project-individual-202",
      expect.objectContaining({ project_id: 202 }),
    )
    expect(prdChatEditMock).not.toHaveBeenCalled()
  })

  it("a classify failure falls open to the prior /v1/ask-only send", async () => {
    resolveIntentMock.mockRejectedValue(new Error("network down"))
    runAskGenerationMock.mockResolvedValue({
      answer: "still answers", key_points: [], citations: [], confidence: 1, unanswered: "",
    })

    await sendMessage("anything urgent this week?")

    await waitFor(() => expect(runAskGenerationMock).toHaveBeenCalledTimes(1))
    expect(prdChatEditMock).not.toHaveBeenCalled()
    expect(addArtifactMock).not.toHaveBeenCalled()
  })

  it("send classifies via the project-scoped resolver, not chatIntentApi.resolve(_, {})", async () => {
    resolveIntentMock.mockResolvedValue({
      intent: "answer", confidence: 0.9, task: null, instruction: null,
      reason: "question", source: "llm", prd_id: null, prd_title: null,
    })
    runAskGenerationMock.mockResolvedValue({
      answer: "ok", key_points: [], citations: [], confidence: 1, unanswered: "",
    })

    await sendMessage("what's the status?")

    await waitFor(() => expect(resolveIntentMock).toHaveBeenCalledTimes(1))
    // Project-scoped call shape: (projectId, message, { conversationId }) —
    // never the old empty-opts call (`chatIntentApi.resolve(question, {})`)
    // that carried no target and triggered the `_NEEDS_PRD` downgrade.
    expect(resolveIntentMock).toHaveBeenCalledWith(
      202, "what's the status?", { conversationId: 9001 },
    )
  })
})
