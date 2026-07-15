// @vitest-environment jsdom
//
// ChatScreen — "convert this PRD into tickets" (or "generate a PRD") typed over
// an ATTACHED DOCUMENT is a COMMAND: it uploads the doc to POST /v1/prd/import
// (prdApi.importDoc — the same conversion as the Artifacts "Upload PRD" button),
// opens the imported PRD as its own tab, and for the tickets phrasing lands the
// content panel on the Tickets tab once the PRD is ready. It must NEVER hit the
// ask agent. Without a document, tickets phrasings fall through to the ask agent
// (unchanged), and PRD phrasings keep the brief-insight command flow.
import * as React from "react"
import { act, cleanup, fireEvent, render, waitFor, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

if (typeof window !== "undefined") window.scrollTo = () => {}
if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false, media: query, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    }) as unknown as MediaQueryList
}

const { briefCurrent, importDoc, extractFile } = vi.hoisted(() => ({
  briefCurrent: vi.fn(),
  importDoc: vi.fn(),
  extractFile: vi.fn(),
}))
vi.mock("../../../../lib/api", () => {
  class ApiError extends Error {
    status = 0
    body: unknown = null
  }
  return {
    ApiError,
    askApi: {
      ask: vi.fn(),
      skills: vi.fn().mockResolvedValue({ skills: [] }),
      extractFile: (...a: unknown[]) => extractFile(...a),
    },
    briefApi: { current: briefCurrent },
    prdApi: {
      importDoc: (...a: unknown[]) => importDoc(...a),
      listInputQuestions: vi.fn().mockResolvedValue([]),
      answerInputQuestion: vi.fn(),
    },
    storiesApi: { getForPrd: vi.fn().mockResolvedValue({ status: "none", fresh: false, stories: [] }) },
    conversationsApi: {
      create: vi.fn().mockResolvedValue({ id: 1 }),
      addTurn: vi.fn().mockResolvedValue({}),
      byPrd: vi.fn().mockResolvedValue({ conversation: null, turns: [] }),
    },
  }
})

const runPrdGeneration = vi.fn().mockResolvedValue({
  ok: true, prd: { prd_id: 77, title: "Generated PRD", metaLine: "", sections: [] },
})
// The import poller: POST /v1/prd/import already kicked the job off, ChatScreen
// polls it to ready via resumePrdGeneration(prdId, null).
const resumePrdGeneration = vi.fn().mockResolvedValue({
  ok: true, prd: { prd_id: 42, title: "Imported PRD", metaLine: "", sections: [] },
})
vi.mock("../../../../lib/runPrdGeneration", () => ({
  runPrdGeneration: (...args: unknown[]) => runPrdGeneration(...args),
  resumePrdGeneration: (...args: unknown[]) => resumePrdGeneration(...args),
  runPrdGenerationFromBacklog: vi.fn().mockResolvedValue({ ok: false, message: "noop" }),
  loadPrdById: vi.fn().mockResolvedValue({ ok: false, message: "noop" }),
}))

const runAskGeneration = vi.fn().mockResolvedValue({
  answer: "canned", sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "",
})
vi.mock("../../../../lib/runAskGeneration", () => ({
  runAskGeneration: (...args: unknown[]) => runAskGeneration(...args),
  resumeAskGeneration: vi.fn(),
  getPendingAsk: vi.fn().mockReturnValue(null),
}))

vi.mock("../../../../lib/usePipelineStatus", () => ({
  usePipelineStatus: () => ({ runStatus: null, isTriggering: false, showCompleted: false, triggerRun: vi.fn() }),
}))
// `?new=1` puts ChatScreen on its OWN new-chat landing (its landing composer),
// not the default brief tab (which renders <BriefChat/> with its own composer).
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams("new=1"),
}))
vi.mock("../../../../context/WorkspaceContext", () => ({
  profileDisplayName: () => "Ada Lovelace",
  useWorkspace: () => ({ loading: false, profile: null, workspace: null, refresh: async () => {} }),
}))
vi.mock("../../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme", setActiveCompany: vi.fn() }),
}))
vi.mock("../../../../lib/auth", () => ({ useAuth: () => ({ kind: "anonymous" }) }))

const { protoMap } = vi.hoisted(() => ({ protoMap: new Map<number, unknown>() }))
vi.mock("../../../design-agent/useBriefPrototypeMap", () => ({
  useBriefPrototypeMap: () => ({ entriesByInsight: protoMap, loading: false, error: false, refetch: vi.fn() }),
}))

import { NavigationProvider, useNavigation } from "../../../../context/NavigationContext"
import { ContentProvider } from "../../../../context/ContentContext"
import { ChatScreen } from "../ChatScreen"

// The ContentPanel itself renders in AppShell (outside this test's tree), so
// observe which panel tab is open via the navigation context directly.
function PanelProbe() {
  const { contentPanelTab } = useNavigation()
  return React.createElement("div", { "data-testid": "panel-probe" }, contentPanelTab ?? "closed")
}

function renderChat() {
  return render(
    React.createElement(
      NavigationProvider,
      null,
      React.createElement(
        ContentProvider,
        null,
        React.createElement(ChatScreen),
        React.createElement(PanelProbe),
      ),
    ),
  )
}

function panelTab(): string {
  return document.querySelector('[data-testid="panel-probe"]')?.textContent ?? ""
}

async function attachDoc(name = "Fraznet Enhancements.pptx"): Promise<File> {
  const input = document.querySelector('input[type="file"]') as HTMLInputElement
  expect(input).toBeTruthy()
  const file = new File(["pptx-bytes"], name, {
    type: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  })
  await act(async () => { fireEvent.change(input, { target: { files: [file] } }) })
  return file
}

async function typeAndSend(text: string) {
  const textarea = document.querySelector(".chat-home-composer-input") as HTMLTextAreaElement
  expect(textarea).toBeTruthy()
  await act(async () => { fireEvent.change(textarea, { target: { value: text } }) })
  const sendBtn = within(document.querySelector(".chat-home-composer") as HTMLElement).getByLabelText("Send")
  await act(async () => { fireEvent.click(sendBtn) })
}

beforeEach(() => {
  localStorage.clear()
  protoMap.clear()
  runAskGeneration.mockClear()
  runPrdGeneration.mockClear()
  resumePrdGeneration.mockClear()
  importDoc.mockReset()
  importDoc.mockResolvedValue({ prd_id: 42, status: "generating", title: "Imported PRD" })
  extractFile.mockReset()
  extractFile.mockResolvedValue({ name: "Fraznet Enhancements.pptx", markdown: "## Slide 1\n\nFraznet MRT workflow" })
  briefCurrent.mockReset()
  briefCurrent.mockResolvedValue({ id: 7, insights: [{ title: "Enterprise expansion is stalled" }] })
})
afterEach(() => { cleanup(); localStorage.clear(); protoMap.clear() })

describe("ChatScreen — 'convert this PRD into tickets' over an attached document", () => {
  it("imports the doc as a PRD and lands the panel on the Tickets tab", async () => {
    renderChat()
    const file = await attachDoc()
    await typeAndSend("Convert this PRD into tickets")

    // Uploaded the ORIGINAL file to the import endpoint for the active company…
    await waitFor(() => expect(importDoc).toHaveBeenCalledWith(file, "acme"))
    // …polled the already-kicked-off import to ready…
    await waitFor(() => expect(resumePrdGeneration).toHaveBeenCalledWith(42, undefined))
    // …and switched the content panel to the Tickets tab once the PRD landed.
    await waitFor(() => expect(panelTab()).toBe("tickets"))
    // Never a question for the ask agent, never the brief-insight PRD flow.
    expect(runAskGeneration).not.toHaveBeenCalled()
    expect(runPrdGeneration).not.toHaveBeenCalled()
    expect(briefCurrent).not.toHaveBeenCalled()
  })

  it.each(["spec.pdf", "spec.docx", "spec.pptx"])(
    "imports %s — every document format takes the same doc → PRD → tickets path",
    async (name) => {
      renderChat()
      const file = await attachDoc(name)
      await typeAndSend("convert this PRD into tickets")

      await waitFor(() => expect(importDoc).toHaveBeenCalledWith(file, "acme"))
      await waitFor(() => expect(panelTab()).toBe("tickets"))
      expect(runAskGeneration).not.toHaveBeenCalled()
    },
  )

  it("'create tickets from this PRD' matches the tickets rule, not the PRD rule", async () => {
    renderChat()
    const file = await attachDoc()
    await typeAndSend("create tickets from this PRD")

    // Ordering matters: the phrasing matches BOTH command regexes, but the user
    // asked for tickets — it must import + open tickets, not run the brief flow.
    await waitFor(() => expect(importDoc).toHaveBeenCalledWith(file, "acme"))
    await waitFor(() => expect(panelTab()).toBe("tickets"))
    expect(briefCurrent).not.toHaveBeenCalled()
    expect(runAskGeneration).not.toHaveBeenCalled()
  })

  it("'generate a PRD' with a document imports it WITHOUT auto-opening tickets", async () => {
    renderChat()
    const file = await attachDoc()
    await typeAndSend("generate a PRD from this")

    await waitFor(() => expect(importDoc).toHaveBeenCalledWith(file, "acme"))
    await waitFor(() => expect(resumePrdGeneration).toHaveBeenCalledWith(42, undefined))
    // The panel stays on the PRD tab — the user asked for a PRD, not tickets.
    await waitFor(() => expect(panelTab()).toBe("prd"))
    // The doc replaces the brief-insight source; the old flow must not also run.
    expect(briefCurrent).not.toHaveBeenCalled()
    expect(runPrdGeneration).not.toHaveBeenCalled()
    expect(runAskGeneration).not.toHaveBeenCalled()
  })

  it("a tickets phrasing with NO document falls through to the ask agent", async () => {
    renderChat()
    await typeAndSend("How should I create tickets for a migration project?")

    await waitFor(() => expect(runAskGeneration).toHaveBeenCalled())
    expect(importDoc).not.toHaveBeenCalled()
    expect(resumePrdGeneration).not.toHaveBeenCalled()
  })

  it("seeds the new tab's thread with the user's command + an acknowledgment", async () => {
    renderChat()
    await attachDoc()
    await typeAndSend("Convert this PRD into tickets")

    // The command is visible as a normal user turn in the chat (not an empty
    // thread next to a spinning panel)…
    await waitFor(() => expect(document.body.textContent).toContain("Convert this PRD into tickets"))
    // …answered by an acknowledgment that says what's happening.
    await waitFor(() => expect(document.body.textContent).toContain("Importing your document as a PRD"))
    // No duplicate action row under the reply — the PRD card at the top of the
    // thread already hosts the actions.
    expect(document.querySelector(".bc-turn:not(.bc-turn--insight) .bc-actions")).toBeNull()
  })

  it("shows the PRD card (panel re-opener) while the import is still generating", async () => {
    // A never-resolving poll keeps the tab in its 'generating' state.
    resumePrdGeneration.mockReturnValueOnce(new Promise(() => {}))
    renderChat()
    await attachDoc()
    await typeAndSend("Convert this PRD into tickets")

    // The insight/PRD card renders DURING generation, with its action button in
    // the generating state — this is what lets the user reopen the panel.
    await waitFor(() => expect(document.querySelector('[data-testid="chat-insight-msg"]')).toBeTruthy())
    await waitFor(() => expect(document.body.textContent).toContain("Generating PRD…"))
  })

  it("shows the attached file as a chip on the LANDING composer (not just a toast)", async () => {
    renderChat()
    await attachDoc("Fraznet Enhancements.pptx")

    // The chip is the persistent evidence the attach worked — the toast alone
    // disappears in seconds, which read as "the upload didn't work".
    const chip = document.querySelector('[data-testid="attachment-chip"]')
    expect(chip).toBeTruthy()
    expect(chip!.textContent).toContain("Fraznet Enhancements.pptx")

    // The × removes it again.
    const remove = chip!.querySelector("button") as HTMLButtonElement
    await act(async () => { fireEvent.click(remove) })
    expect(document.querySelector('[data-testid="attachment-chip"]')).toBeNull()
  })

  it("a normal ask with a document inlines the EXTRACTED markdown, never the raw bytes", async () => {
    renderChat()
    await attachDoc()
    await typeAndSend("Why did enterprise churn spike last month?")

    await waitFor(() => expect(runAskGeneration).toHaveBeenCalled())
    // The document rides along as server-extracted markdown (it used to be
    // silently dropped here); the raw binary payload must never be inlined —
    // that's what blew the ask cap before extraction existed.
    const askedQuery = runAskGeneration.mock.calls[0][0] as string
    expect(askedQuery).toContain("Why did enterprise churn spike last month?")
    expect(askedQuery).toContain("[Attached files]")
    expect(askedQuery).toContain("Fraznet MRT workflow")
    expect(askedQuery).not.toContain("pptx-bytes")
    expect(importDoc).not.toHaveBeenCalled()
  })
})

describe("ChatScreen — import phrasings and non-command sends over an attached document", () => {
  it("'Import this document as a PRD' is an import COMMAND (the phrasing that used to fall through)", async () => {
    renderChat()
    const file = await attachDoc()
    await typeAndSend("Import this document as a PRD")

    await waitFor(() => expect(importDoc).toHaveBeenCalledWith(file, "acme"))
    await waitFor(() => expect(panelTab()).toBe("prd"))
    // It must never go to the ask agent — that path answers "no document was
    // attached" because the ask payload is text-only.
    expect(runAskGeneration).not.toHaveBeenCalled()
    expect(extractFile).not.toHaveBeenCalled()
    expect(briefCurrent).not.toHaveBeenCalled()
  })

  it("'convert this document to a PRD' matches the PRD rule, not tickets", async () => {
    renderChat()
    const file = await attachDoc()
    await typeAndSend("convert this document to a PRD")

    await waitFor(() => expect(importDoc).toHaveBeenCalledWith(file, "acme"))
    await waitFor(() => expect(panelTab()).toBe("prd"))
    expect(runAskGeneration).not.toHaveBeenCalled()
  })

  it("a plain question with a document extracts it server-side and inlines it as ask context", async () => {
    renderChat()
    const file = await attachDoc()
    await typeAndSend("What are the riskiest requirements in this deck?")

    // The doc is parsed via POST /v1/ask/extract-file (not silently dropped)…
    await waitFor(() => expect(extractFile).toHaveBeenCalledWith(file))
    // …and its markdown rides along to the ask agent as [Attached files] context.
    await waitFor(() => expect(runAskGeneration).toHaveBeenCalled())
    const query = runAskGeneration.mock.calls[0][0] as string
    expect(query).toContain("What are the riskiest requirements in this deck?")
    expect(query).toContain("[Attached files]")
    expect(query).toContain("Fraznet Enhancements.pptx")
    expect(query).toContain("Fraznet MRT workflow")
    // A plain question is NOT an import — no PRD row is created.
    expect(importDoc).not.toHaveBeenCalled()
  })

  it("keeps the attachment and does not send when extraction fails", async () => {
    extractFile.mockRejectedValueOnce(new Error("could not parse file"))
    renderChat()
    await attachDoc()
    await typeAndSend("What does this deck say?")

    await waitFor(() => expect(extractFile).toHaveBeenCalled())
    // The send is aborted — nothing reaches the ask agent…
    expect(runAskGeneration).not.toHaveBeenCalled()
    // …and the attachment chip is still there for a retry (not silently lost).
    await waitFor(() => expect(document.body.textContent).toContain("Fraznet Enhancements.pptx"))
  })
})
