// @vitest-environment jsdom
//
// ProjectIndividualChat — private-ask token streaming + Stop.
//
// `runAskGeneration` on this surface is now called with an `onPartial`
// handler (mirroring the main chat's own ask-path streaming block): a delta
// arriving mid-generation renders into the in-flight turn instead of sitting
// behind a bare wait state until the poll resolves. Stop is unchanged client-
// side (it already existed) — this file proves the signal it sends
// (`isStopped`) reaches `runAskGeneration`, which is what lets it reach the
// backend's cancellation round-trip; that round-trip itself is a live-backend
// proof, out of reach of a mocked unit test.
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

import { ProjectIndividualChat } from "../ProjectIndividualChat"

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

describe("ProjectIndividualChat — private streaming", () => {
  it("renders streamed partials into the in-flight turn before the final reply", async () => {
    let capturedOpts: AskOpts = {}
    runAskGenerationMock.mockImplementation((_q, _company, _tabId, opts: AskOpts) => {
      capturedOpts = opts
      return new Promise(() => {}) // never resolves in this test — asserting the pre-final state
    })
    render(React.createElement(ProjectIndividualChat, { projectId: 202 }))
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

describe("ProjectIndividualChat — Stop wiring reaches runAskGeneration", () => {
  it("passes isStopped through to runAskGeneration and it flips true after Stop is clicked", async () => {
    let capturedOpts: AskOpts = {}
    runAskGenerationMock.mockImplementation((_q, _company, _tabId, opts: AskOpts) => {
      capturedOpts = opts
      return new Promise(() => {})
    })
    render(React.createElement(ProjectIndividualChat, { projectId: 202 }))
    await send("stop me mid-answer")

    expect(typeof capturedOpts.isStopped).toBe("function")
    expect(capturedOpts.isStopped!()).toBe(false)

    await act(async () => {
      fireEvent.click(screen.getByLabelText("Stop generating"))
    })
    // The client-side signal `runAskGeneration` polls to decide whether to
    // keep polling / to request server-side cancellation — this is the half
    // of the round-trip a mocked unit test can prove; the backend actually
    // honouring it (`is_cancelled`) is the ship-gate's live-backend proof.
    expect(capturedOpts.isStopped!()).toBe(true)
  })
})
