// @vitest-environment jsdom
//
// ProjectPrivateChat — private classify→dispatch (AD-P13a / AC14). With
// the classifier flag ON, a send classifies via the project-scoped
// `projectsApi.resolveIntent` (server-resolves the edit target over this
// project's own PRDs — NOT `chatIntentApi.resolve(question, {})`, which
// sends no target and lets the `_NEEDS_PRD` downgrade rewrite `edit_prd` to
// `answer`) and routes through the SHARED `dispatchChatIntent` primitive:
// `edit_prd` hits the project chat-edit route, `generate_prd`/
// `generate_tickets` hit the generate routes THEN auto-attach, `answer`
// (and any classify failure) falls open to the prior `/v1/ask`-only send —
// see `ProjectPrivateChat.test.tsx` for the flag-OFF / byte-identical-
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
const persistIndividualTurnsMock = vi.fn().mockResolvedValue({ user_turn_id: 1, assistant_turn_id: 2 })

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
      persistIndividualTurns: (...a: unknown[]) => persistIndividualTurnsMock(...a),
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
// unmodified origin/release/projects@ca2f6f92 — `ProjectPrivateChat.test.
// tsx`'s own component-rendering tests are ALREADY red there): the always-
// mounted `ProjectPrdPatchBanner` (the retired propose/review PRD-edit
// banner, left in place for a later ticket to delete) calls `useNavigation()`
// unconditionally, which throws without a `NavigationProvider`. Minimal mock
// so THIS file's dispatch coverage isn't blocked by an unrelated,
// pre-existing bug outside this ticket's surface.
vi.mock("../../../../../context/NavigationContext", () => ({
  useNavigation: () => ({ showToast: vi.fn() }),
}))

import { ProjectPrivateChat } from "../ProjectPrivateChat"

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
  persistIndividualTurnsMock.mockReset().mockResolvedValue({ user_turn_id: 1, assistant_turn_id: 2 })
})
afterEach(() => cleanup())

async function sendMessage(text: string) {
  render(React.createElement(ProjectPrivateChat, { projectId: 202 }))
  const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
  await act(async () => {
    fireEvent.change(textarea, { target: { value: text } })
  })
  await act(async () => {
    fireEvent.click(screen.getByLabelText("Send"))
  })
}

describe("ProjectPrivateChat — classify→dispatch (flag on)", () => {
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
    // Server persists both sides via prdChatEdit (§D) — the 4th arg is this
    // send's client_message_id, minted once and threaded through.
    expect(prdChatEditMock).toHaveBeenCalledWith(
      202, "tighten the problem statement", undefined, expect.any(String),
    )
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
    // No dedicated chat route for this branch — it persists via the owned
    // turn-pair route at settle time (§H/AC2).
    await waitFor(() => expect(persistIndividualTurnsMock).toHaveBeenCalledTimes(1))
    expect(persistIndividualTurnsMock).toHaveBeenCalledWith(202, {
      clientMessageId: expect.any(String),
      question: "break this into work items",
      answer: expect.stringContaining("ticket set"),
    })
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
    await waitFor(() => expect(persistIndividualTurnsMock).toHaveBeenCalledTimes(1))
    expect(persistIndividualTurnsMock).toHaveBeenCalledWith(202, {
      clientMessageId: expect.any(String),
      question: "generate a PRD for dark mode",
      answer: expect.stringContaining("Dark mode"),
    })
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
      // The send's minted client_message_id rides the /v1/ask call — the
      // server persists both sides keyed on it (§C); no client persist call.
      expect.objectContaining({ project_id: 202, client_message_id: expect.any(String) }),
    )
    expect(prdChatEditMock).not.toHaveBeenCalled()
    expect(persistIndividualTurnsMock).not.toHaveBeenCalled()
  })

  it("a list_artifacts envelope renders the clickable artifact cards from the envelope's rows, no /v1/ask", async () => {
    resolveIntentMock.mockResolvedValue({
      intent: "list_artifacts", confidence: 0.9, task: null, instruction: null,
      reason: "listing", source: "llm", prd_id: null, prd_title: null,
      list_kind: "prd", list_mode: "items",
      artifact_list: [
        {
          type: "prd", id: 501, title: "Dark mode", status: "ready",
          created_at: "2026-08-15T00:00:00Z", brief_anchored: false,
          source: {}, open: { prd_id: 501 },
        },
        {
          type: "prd", id: 502, title: "Checkout", status: "ready",
          created_at: "2026-08-14T00:00:00Z", brief_anchored: false,
          source: {}, open: { prd_id: 502 },
        },
      ],
    })

    await sendMessage("what are my PRDs?")

    await waitFor(() => expect(screen.getByTestId("artifact-list-cards")).toBeTruthy())
    expect(screen.getAllByTestId("artifact-list-card")).toHaveLength(2)
    expect(screen.getByTestId("ic-msg-agent").textContent).toContain("2 newest PRDs")
    expect(runAskGenerationMock).not.toHaveBeenCalled()
    // Prose persists via the owned turn-pair route; the cards are a live
    // affordance riding the session turn (main's persist-the-prose contract).
    await waitFor(() => expect(persistIndividualTurnsMock).toHaveBeenCalledTimes(1))
  })

  it("an ambiguous open_artifact envelope renders the candidate chips, no /v1/ask", async () => {
    resolveIntentMock.mockResolvedValue({
      intent: "open_artifact", confidence: 0.9, task: null, instruction: null,
      reason: "open", source: "llm", prd_id: null, prd_title: null,
      artifact_type: "prd", artifact_query: "checkout",
      open: {
        status: "ambiguous", artifact_type: "prd", query: "checkout", artifact: null,
        candidates: [
          {
            type: "prd", id: 501, title: "Checkout v1", status: "ready",
            prd_id: 501, brief_id: null, insight_index: null,
            brief_anchored: false, week_label: null,
          },
          {
            type: "prd", id: 502, title: "Checkout v2", status: "ready",
            prd_id: 502, brief_id: null, insight_index: null,
            brief_anchored: false, week_label: null,
          },
        ],
      },
    })

    await sendMessage("open the checkout PRD")

    await waitFor(() => expect(screen.getByTestId("open-artifact-chips")).toBeTruthy())
    expect(screen.getAllByTestId("open-artifact-chip")).toHaveLength(2)
    expect(screen.getByTestId("ic-msg-agent").textContent).toContain("more than one PRD")
    expect(runAskGenerationMock).not.toHaveBeenCalled()
    await waitFor(() => expect(persistIndividualTurnsMock).toHaveBeenCalledTimes(1))
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

  it("a clarify envelope renders the clarification + PRD options, no /v1/ask", async () => {
    resolveIntentMock.mockResolvedValue({
      intent: "clarify", confidence: 0.9, task: null, instruction: null,
      reason: "ambiguous target", source: "no_target_prd", prd_id: null, prd_title: null,
      clarification:
        "This project has more than one PRD — tell me which to edit by id: " +
        "Onboarding [id 501], Billing [id 502].",
      prd_options: [
        { id: 501, title: "Onboarding" },
        { id: 502, title: "Billing" },
      ],
    })

    await sendMessage("tighten the problem statement")

    await waitFor(() =>
      expect(screen.getByTestId("ic-msg-agent").textContent).toContain(
        "tell me which to edit by id",
      ),
    )
    expect(screen.getByTestId("ic-msg-agent").textContent).toContain("Onboarding [id 501]")
    expect(runAskGenerationMock).not.toHaveBeenCalled()
    expect(prdChatEditMock).not.toHaveBeenCalled()
  })

  it("picking a clarify option issues the edit with that prd_id (AC2, the ask→pick→apply loop)", async () => {
    resolveIntentMock.mockResolvedValue({
      intent: "clarify", confidence: 0.9, task: null, instruction: null,
      reason: "ambiguous target", source: "no_target_prd", prd_id: null, prd_title: null,
      clarification:
        "This project has more than one PRD — tell me which to edit by id: " +
        "Onboarding [id 501], Billing [id 502].",
      prd_options: [
        { id: 501, title: "Onboarding" },
        { id: 502, title: "Billing" },
      ],
    })
    prdChatEditMock.mockResolvedValue({
      edited: true, prd: { payload_md: "<p>tightened</p>" },
      sections_changed: ["Problem"], summary: "Tightened the problem statement.",
    })

    await sendMessage("tighten the problem statement")
    await waitFor(() => expect(screen.getByTestId("ic-clarify-options")).toBeTruthy())

    await act(async () => {
      fireEvent.click(screen.getByTestId("ic-clarify-option-502"))
    })

    // The SAME original instruction re-issued with the CHOSEN id attached —
    // not a re-classify, no second `resolveIntent` call.
    await waitFor(() => expect(prdChatEditMock).toHaveBeenCalledTimes(1))
    // The pick's OWN client_message_id (minted for the pick's session turn,
    // distinct from the source turn's) rides as the 4th arg.
    expect(prdChatEditMock).toHaveBeenCalledWith(
      202, "tighten the problem statement", 502, expect.any(String),
    )
    expect(resolveIntentMock).toHaveBeenCalledTimes(1)
    await waitFor(() =>
      expect(screen.getAllByTestId("ic-msg-agent").at(-1)?.textContent).toContain(
        "Tightened the problem statement.",
      ),
    )
    // Clicked once — the options are cleared off the source turn, so a
    // second click has nothing left to fire (no double-apply surface).
    expect(screen.queryByTestId("ic-clarify-options")).toBeNull()
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
