// @vitest-environment jsdom
//
// ProjectIndividualChat — the private "My chat with Sprntly" thread. AD-P13
// reuse (source scan, no bespoke primitives, no chat-monolith import),
// project-scoped `/v1/ask` send + poll via the SHARED ask library, the
// cross-chat INSIGHT marker, and mount-time resume of a pending job.
import * as React from "react"
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

// AskReplyBody's typing-animation hook reads prefers-reduced-motion on mount;
// jsdom has no matchMedia.
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

const runAskGenerationMock = vi.fn()
const resumeAskGenerationMock = vi.fn()
const getPendingAskMock = vi.fn(() => null as { id: string } | null)
const individualChatMock = vi.fn()
// New on this ticket: the component now loads persisted history on mount
// (`ProjectIndividualChat.history.dom.test.tsx` covers that surface in
// depth) — every test in THIS file must still mock it too, otherwise mount
// fires a real, unmocked `fetch` in jsdom. Defaults to an empty history so
// the pre-existing session-flow assertions below are unaffected.
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

import { ProjectIndividualChat } from "../ProjectIndividualChat"
import { AskStoppedError, AskTimeoutError } from "../../../../../lib/runAskGeneration"

const reply = (answer: string) => ({ answer, key_points: [], citations: [], confidence: 1, unanswered: "" })

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

describe("ProjectIndividualChat — AD-P13 reuse (source scan)", () => {
  it("imports the shared primitives + the extracted composer, and defines no bespoke implementation", () => {
    const src = readFileSync(join(__dirname, "../ProjectIndividualChat.tsx"), "utf8")
    expect(src).toContain('from "../../../shared/AskReplyBody"')
    expect(src).toContain('from "react-markdown"')
    expect(src).toContain('from "remark-gfm"')
    expect(src).toContain('from "../../../shared/AssistantThinkingSkeleton"')
    expect(src).toContain('from "../../../shared/AssistantWaitState"')
    expect(src).toContain('from "../../../shared/OpenArtifactChips"')
    expect(src).toContain('from "../../../shared/ChatComposer"')
    expect(src).not.toMatch(/function\s+AskReplyBody/)
    expect(src).not.toMatch(/function\s+OpenArtifactChips/)
    expect(src).not.toMatch(/function\s+ChatComposer/)
  })

  it("does not import or reference the chat monolith container", () => {
    const src = readFileSync(join(__dirname, "../ProjectIndividualChat.tsx"), "utf8")
    expect(src).not.toContain("ChatScreen")
  })

  it("hits /v1/ask with project_id via the shared ask library, not a bespoke fetch", () => {
    const src = readFileSync(join(__dirname, "../ProjectIndividualChat.tsx"), "utf8")
    expect(src).toContain('from "../../../../lib/runAskGeneration"')
    expect(src).toContain("project_id")
    expect(src).not.toMatch(/fetch\(/)
  })
})

describe("ProjectIndividualChat — component-scoped CSS is tokens only", () => {
  it("resolves every color to a globals.css custom property — no new palette", () => {
    const css = readFileSync(join(__dirname, "../ProjectIndividualChat.module.css"), "utf8")
    const found = css.match(/#[0-9A-Fa-f]{3,8}/g) ?? []
    expect(found).toEqual([])
  })
})

describe("ProjectIndividualChat — composer", () => {
  it("carries the individual-chat placeholder", async () => {
    render(React.createElement(ProjectIndividualChat, { projectId: 101 }))
    const ta = document.querySelector(".cx-input") as HTMLTextAreaElement
    expect(ta.placeholder).toBe("Message Sprntly…")
  })

  it("shows an empty-state hint before any turn", () => {
    render(React.createElement(ProjectIndividualChat, { projectId: 101 }))
    expect(screen.getByTestId("individual-chat-empty")).toBeTruthy()
  })
})

describe("ProjectIndividualChat — send + poll + render", () => {
  it("posts via runAskGeneration with project_id, shows a pending wait, then renders the answer via AskReplyBody", async () => {
    let resolveAsk: (r: unknown) => void = () => {}
    runAskGenerationMock.mockReturnValue(
      new Promise((resolve) => {
        resolveAsk = resolve
      }),
    )
    render(React.createElement(ProjectIndividualChat, { projectId: 202 }))

    const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "what did the team decide on pricing?" } })
    })
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Send"))
    })

    expect(individualChatMock).toHaveBeenCalledWith(202)
    expect(runAskGenerationMock).toHaveBeenCalledWith(
      "what did the team decide on pricing?",
      "acme",
      "project-individual-202",
      expect.objectContaining({ project_id: 202, conversation_id: 9001 }),
    )
    expect(screen.getByTestId("ic-msg-you").textContent).toContain("what did the team decide on pricing?")
    expect(screen.getByTestId("ic-msg-pending")).toBeTruthy()

    await act(async () => {
      resolveAsk(reply("Flat $49/mo, decided last week."))
    })
    await waitFor(() => expect(screen.getByTestId("ic-msg-agent")).toBeTruthy())
    expect(screen.getByTestId("ic-msg-agent").textContent).toContain("Flat $49/mo, decided last week.")
    expect(screen.queryByTestId("ic-msg-pending")).toBeNull()
  })

  it("get-or-creates the individual conversation ONCE and reuses it across sends on the same mount", async () => {
    runAskGenerationMock.mockResolvedValue(reply("ok"))
    render(React.createElement(ProjectIndividualChat, { projectId: 202 }))
    const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement

    await act(async () => {
      fireEvent.change(textarea, { target: { value: "first message here" } })
    })
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Send"))
    })
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "second message here" } })
    })
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Send"))
    })

    expect(individualChatMock).toHaveBeenCalledTimes(1)
    expect(runAskGenerationMock).toHaveBeenCalledTimes(2)
    for (const call of runAskGenerationMock.mock.calls) {
      expect(call[3]).toEqual(expect.objectContaining({ conversation_id: 9001 }))
    }
  })

  it("a failed get-or-create degrades to an unbound ask rather than blocking the send", async () => {
    individualChatMock.mockRejectedValue(new Error("network blip"))
    runAskGenerationMock.mockResolvedValue(reply("still answers"))
    render(React.createElement(ProjectIndividualChat, { projectId: 202 }))
    const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "ask anyway please" } })
    })
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Send"))
    })

    expect(runAskGenerationMock).toHaveBeenCalledWith(
      "ask anyway please",
      "acme",
      "project-individual-202",
      expect.objectContaining({ project_id: 202, conversation_id: undefined }),
    )
    await waitFor(() => expect(screen.getByTestId("ic-msg-agent")).toBeTruthy())
  })

  it("Stop marks the turn stopped and does not render an error", async () => {
    let rejectAsk: (e: unknown) => void = () => {}
    runAskGenerationMock.mockReturnValue(
      new Promise((_resolve, reject) => {
        rejectAsk = reject
      }),
    )
    render(React.createElement(ProjectIndividualChat, { projectId: 202 }))
    const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "a question worth stopping" } })
    })
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Send"))
    })
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Stop generating"))
    })
    await act(async () => {
      rejectAsk(new AskStoppedError("stopped"))
    })
    await waitFor(() => expect(screen.getByTestId("ic-msg-stopped")).toBeTruthy())
    expect(screen.queryByTestId("ic-msg-error")).toBeNull()
  })

  it("a timeout renders the honest 'still running' message, not a generic failure", async () => {
    let rejectAsk: (e: unknown) => void = () => {}
    runAskGenerationMock.mockReturnValue(
      new Promise((_resolve, reject) => {
        rejectAsk = reject
      }),
    )
    render(React.createElement(ProjectIndividualChat, { projectId: 202 }))
    const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "a slow one" } })
    })
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Send"))
    })
    await act(async () => {
      rejectAsk(new AskTimeoutError("timed out"))
    })
    await waitFor(() => expect(screen.getByTestId("ic-msg-error").textContent).toContain("still running"))
  })
})

describe("ProjectIndividualChat — resume on mount", () => {
  it("resumes a pending job via the shared resumeAskGeneration and renders the answer once it lands", async () => {
    getPendingAskMock.mockReturnValue({ id: "555" })
    resumeAskGenerationMock.mockResolvedValue(reply("resumed answer"))
    render(React.createElement(ProjectIndividualChat, { projectId: 202 }))
    expect(screen.getByTestId("ic-resuming")).toBeTruthy()
    await waitFor(() => expect(screen.getByTestId("ic-msg-agent")).toBeTruthy())
    expect(resumeAskGenerationMock).toHaveBeenCalledWith(555, "acme", "project-individual-202")
    expect(screen.getByTestId("ic-msg-agent").textContent).toContain("resumed answer")
  })

  it("no pending job — no resuming state, straight to the empty hint", () => {
    render(React.createElement(ProjectIndividualChat, { projectId: 202 }))
    expect(screen.queryByTestId("ic-resuming")).toBeNull()
    expect(screen.getByTestId("individual-chat-empty")).toBeTruthy()
  })
})

describe("ProjectIndividualChat — cross-chat INSIGHT turn (design-spec AC7/AC11)", () => {
  it("renders the INSIGHT note with the existing bc-turn--insight treatment when supplied", () => {
    render(
      React.createElement(ProjectIndividualChat, {
        projectId: 202,
        insightNote: { by: "Shristi", text: "the pricing model changed" },
      }),
    )
    const note = screen.getByTestId("cross-chat-insight")
    expect(note.className).toContain("bc-turn--insight")
    expect(note.textContent).toContain("Shristi")
    expect(note.textContent).toContain("the pricing model changed")
  })

  it("renders no INSIGHT note when none is supplied (the default)", () => {
    render(React.createElement(ProjectIndividualChat, { projectId: 202 }))
    expect(screen.queryByTestId("cross-chat-insight")).toBeNull()
  })

  it("FIX B — source_kind='group' renders 'noted this in the group chat'", () => {
    render(
      React.createElement(ProjectIndividualChat, {
        projectId: 202,
        insightNote: { by: "Shristi", text: "the pricing model changed", source_kind: "group" },
      }),
    )
    const note = screen.getByTestId("cross-chat-insight")
    expect(note.textContent).toContain("noted this in the group chat")
    expect(note.textContent).not.toContain("noted this in a chat with Sprntly")
  })

  it("FIX B — source_kind='individual' renders 'noted this in a chat with Sprntly', never the group-chat label", () => {
    // Ground: source_conversation_id is set for individual-chat promotions
    // too — the previous hardcoded 'in the group chat' copy mislabeled
    // this case. This pins the fix.
    render(
      React.createElement(ProjectIndividualChat, {
        projectId: 202,
        insightNote: { by: "David", text: "flat pricing, not tiered", source_kind: "individual" },
      }),
    )
    const note = screen.getByTestId("cross-chat-insight")
    expect(note.textContent).toContain("noted this in a chat with Sprntly")
    expect(note.textContent).not.toContain("noted this in the group chat")
  })

  it("source_kind omitted/unresolved renders a kind-neutral note, no guessed location", () => {
    render(
      React.createElement(ProjectIndividualChat, {
        projectId: 202,
        insightNote: { by: "Sprntly", text: "no source kind resolved" },
      }),
    )
    const note = screen.getByTestId("cross-chat-insight")
    expect(note.textContent).not.toContain("group chat")
    expect(note.textContent).not.toContain("chat with Sprntly")
  })
})
