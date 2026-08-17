// @vitest-environment jsdom
//
// ProjectPrivateChat — private-ask token streaming + Stop.
//
// `runAskGeneration` on this surface is now called with an `onPartial`
// handler (mirroring the main chat's own ask-path streaming block): a delta
// arriving mid-generation renders into the in-flight turn instead of sitting
// behind a bare wait state until the poll resolves.
//
// Stop does two things, both proven here: it flips the LOCAL `isStopped`
// signal `runAskGeneration` polls, AND it actively resolves the pending ask
// id and calls the cancel endpoint so the backend worker aborts and any late
// answer is discarded server-side — a local-only stop would leave the
// backend call running (and billing) to completion after the UI goes quiet.
import * as React from "react"
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }) as unknown as MediaQueryList
}

type AskOpts = {
  isStopped?: () => boolean
  onPartial?: (text: string) => void
  onStreamDrop?: () => void
}

const runAskGenerationMock = vi.fn()
const resumeAskGenerationMock = vi.fn()
const getPendingAskMock = vi.fn(() => null as { id: string } | null)
const individualChatMock = vi.fn()
const individualTurnsMock = vi.fn()
const askCancelMock = vi.fn()

vi.mock("../../../../../lib/runAskGeneration", async () => {
  const actual = await vi.importActual<typeof import("../../../../../lib/runAskGeneration")>(
    "../../../../../lib/runAskGeneration",
  )
  return {
    ...actual,
    runAskGeneration: (...a: unknown[]) => runAskGenerationMock(...a),
    resumeAskGeneration: (...a: unknown[]) => resumeAskGenerationMock(...a),
    getPendingAsk: (...a: unknown[]) => getPendingAskMock(...a),
  }
})

vi.mock("../../../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../../../lib/api")>("../../../../../lib/api")
  return {
    ...actual,
    askApi: {
      ...actual.askApi,
      cancel: (...a: unknown[]) => askCancelMock(...a),
    },
    projectsApi: {
      ...actual.projectsApi,
      individualChat: (...a: unknown[]) => individualChatMock(...a),
      individualTurns: (...a: unknown[]) => individualTurnsMock(...a),
    },
  }
})

vi.mock("../../../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme", setActiveCompany: vi.fn(), activeCompanyDisplayName: "Acme" }),
}))
vi.mock("../../../../../context/WorkspaceContext", () => ({
  useWorkspace: () => ({
    loading: false, profile: null,
    workspace: { feature_flags: { chat_intent_envelope: false } },
    refresh: async () => {},
  }),
}))
vi.mock("../../../../../lib/auth", () => ({
  useAuth: () => ({ kind: "authed" as const, user: { id: "u1" } }),
}))

import { ProjectPrivateChat } from "../ProjectPrivateChat"

const individualChatRecord = (id: number, projectId: number) => ({
  id,
  project_id: projectId,
  user_id: "u1",
  kind: "individual" as const,
  created_at: "2026-08-11T00:00:00Z",
  updated_at: "2026-08-11T00:00:00Z",
})

beforeEach(() => {
  runAskGenerationMock.mockReset()
  resumeAskGenerationMock.mockReset()
  getPendingAskMock.mockReset()
  getPendingAskMock.mockReturnValue(null)
  individualChatMock.mockReset()
  individualChatMock.mockImplementation((id: number) => Promise.resolve(individualChatRecord(9001, id)))
  individualTurnsMock.mockReset()
  individualTurnsMock.mockResolvedValue([])
  askCancelMock.mockReset()
  askCancelMock.mockResolvedValue({ ask_id: 0, status: "cancelled" })
})
afterEach(() => cleanup())

async function send(question: string) {
  const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
  await act(async () => {
    fireEvent.change(textarea, { target: { value: question } })
  })
  await act(async () => {
    fireEvent.click(screen.getByLabelText("Send"))
  })
}

describe("ProjectPrivateChat — private streaming", () => {
  it("renders streamed partials into the in-flight turn before the final reply", async () => {
    let capturedOpts: AskOpts = {}
    runAskGenerationMock.mockImplementation((_q, _company, _tabId, opts: AskOpts) => {
      capturedOpts = opts
      return new Promise(() => {}) // never resolves in this test — asserting the pre-final state
    })
    render(React.createElement(ProjectPrivateChat, { projectId: 202 }))
    await send("what's the rollout plan?")

    expect(typeof capturedOpts.onPartial).toBe("function")
    // Nothing streamed yet — the plain wait state, no streaming testid.
    expect(screen.getByTestId("ic-msg-pending")).toBeTruthy()
    expect(screen.queryByTestId("ic-msg-streaming")).toBeNull()

    await act(async () => {
      capturedOpts.onPartial!("The rollout starts")
    })
    expect(screen.queryByTestId("ic-msg-pending")).toBeNull()
    const streaming = screen.getByTestId("ic-msg-streaming")
    expect(streaming.textContent).toContain("The rollout starts")

    // A second delta REPLACES the rendered text (assigned, not appended) —
    // matches `onPartial`'s documented cumulative-markdown contract.
    await act(async () => {
      capturedOpts.onPartial!("The rollout starts next Monday.")
    })
    expect(screen.getByTestId("ic-msg-streaming").textContent).toContain("The rollout starts next Monday.")
  })

  it("does not add group token streaming — ProjectGroupChat's send path takes no onPartial/onStreamDrop", () => {
    const src = readFileSync(join(__dirname, "../ProjectGroupChat.tsx"), "utf8")
    expect(src).not.toContain("onPartial")
    expect(src).not.toContain("onStreamDrop")
  })
})

describe("ProjectPrivateChat — settled reply citations never render as raw source cards", () => {
  it("renders NO citation cards when the ask reply carries citations (raw retrieval-source keys are storage identifiers, not user-facing names); the answer still renders", async () => {
    runAskGenerationMock.mockResolvedValue({
      answer: "The rollout starts next Monday.",
      key_points: ["rollout Monday"],
      citations: [
        { source: "slack_channels", evidence: "…" },
        { source: "communication/incident", evidence: "…" },
      ],
      confidence: 1,
      unanswered: "",
    })
    render(React.createElement(ProjectPrivateChat, { projectId: 202 }))
    await send("when does the rollout start?")

    const agent = await screen.findByTestId("ic-msg-agent")
    // The reply settled (full answer on screen) — the citation assertions
    // below aren't vacuously green off a pending/streaming state.
    expect(agent.textContent).toContain("The rollout starts next Monday.")
    expect(document.querySelector(".ai-bar-reply-cite-src")).toBeNull()
    expect(document.querySelector(".ai-bar-reply-cites")).toBeNull()
    for (const raw of ["slack_channels", "communication/incident"]) {
      expect(screen.queryByText(raw)).toBeNull()
    }
  })
})

describe("ProjectPrivateChat — Stop actively cancels the backend job", () => {
  it("flips the local isStopped signal AND calls the cancel endpoint with the resolved ask id", async () => {
    let capturedOpts: AskOpts = {}
    runAskGenerationMock.mockImplementation((_q, _company, _tabId, opts: AskOpts) => {
      capturedOpts = opts
      return new Promise(() => {})
    })
    // No pending job at MOUNT (nothing to resume) — `getPendingAsk` only
    // starts resolving the ask this send kicks off once it's set below,
    // right before Stop, mirroring the REAL `runAskGeneration` persisting
    // the job id only after the send starts (mocked wholesale here, so its
    // side effect doesn't happen — this is the equivalent client-visible
    // state at the moment the user hits Stop).
    render(React.createElement(ProjectPrivateChat, { projectId: 202 }))
    await send("stop me mid-answer")

    expect(typeof capturedOpts.isStopped).toBe("function")
    expect(capturedOpts.isStopped!()).toBe(false)
    expect(askCancelMock).not.toHaveBeenCalled()

    // The job the backend is now running for this thread's tab — Stop must
    // resolve THIS id and cancel it, not just flip a local flag.
    getPendingAskMock.mockReturnValue({ id: "777" })
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Stop generating"))
    })

    // 1) The local signal `runAskGeneration` polls to stop rendering/polling.
    expect(capturedOpts.isStopped!()).toBe(true)
    // 2) The ACTIVE backend cancel — without this, the local flag alone
    // silences the UI while the backend LLM call keeps running (and billing)
    // to completion. Must be the actual pending job's id, not a guess.
    expect(askCancelMock).toHaveBeenCalledWith(777)
  })

  it("does nothing to the backend if there is no pending job for this tab (nothing to cancel)", async () => {
    let capturedOpts: AskOpts = {}
    runAskGenerationMock.mockImplementation((_q, _company, _tabId, opts: AskOpts) => {
      capturedOpts = opts
      return new Promise(() => {})
    })
    getPendingAskMock.mockReturnValue(null)

    render(React.createElement(ProjectPrivateChat, { projectId: 202 }))
    await send("stop me mid-answer")

    await act(async () => {
      fireEvent.click(screen.getByLabelText("Stop generating"))
    })

    expect(capturedOpts.isStopped!()).toBe(true)
    expect(askCancelMock).not.toHaveBeenCalled()
  })
})
