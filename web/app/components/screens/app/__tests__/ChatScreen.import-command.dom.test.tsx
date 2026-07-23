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

const { briefCurrent, importDoc, extractFile, storiesGenerate, generateFromTask, listInputQuestions } = vi.hoisted(() => ({
  briefCurrent: vi.fn(),
  importDoc: vi.fn(),
  extractFile: vi.fn(),
  storiesGenerate: vi.fn(),
  generateFromTask: vi.fn(),
  listInputQuestions: vi.fn(),
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
      generateFromTask: (...a: unknown[]) => generateFromTask(...a),
      listInputQuestions: (...a: unknown[]) => listInputQuestions(...a),
      answerInputQuestion: vi.fn(),
    },
    storiesApi: {
      getForPrd: vi.fn().mockResolvedValue({ status: "none", fresh: false, stories: [] }),
      generate: (...a: unknown[]) => storiesGenerate(...a),
    },
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
  runPrdGenerationFromIdeation: vi.fn().mockResolvedValue({ ok: false, message: "noop" }),
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
import { ContentProvider, useContent } from "../../../../context/ContentContext"
import { ChatScreen } from "../ChatScreen"

// The ContentPanel itself renders in AppShell (outside this test's tree), so
// observe which panel tab is open via the navigation context directly.
function PanelProbe() {
  const { contentPanelTab } = useNavigation()
  return React.createElement("div", { "data-testid": "panel-probe" }, contentPanelTab ?? "closed")
}

// Likewise observe the shared content state's live-preview field — the PRD
// panel (in AppShell) renders whatever lands here during generation.
function PartialProbe() {
  const { content } = useContent()
  return React.createElement("div", { "data-testid": "partial-probe" }, content.prdPartialHtml ?? "")
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
        React.createElement(PartialProbe),
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
  storiesGenerate.mockReset()
  storiesGenerate.mockResolvedValue({ job_id: 1, status: "generating" })
  extractFile.mockReset()
  extractFile.mockResolvedValue({ name: "Fraznet Enhancements.pptx", markdown: "## Slide 1\n\nFraznet MRT workflow" })
  briefCurrent.mockReset()
  briefCurrent.mockResolvedValue({ id: 7, insights: [{ title: "Enterprise expansion is stalled" }] })
  generateFromTask.mockReset()
  generateFromTask.mockResolvedValue({ prd_id: 55, status: "generating", title: "Dark mode" })
  listInputQuestions.mockReset()
  listInputQuestions.mockResolvedValue([]) // no clarifying questions by default
})
afterEach(() => { cleanup(); localStorage.clear(); protoMap.clear() })

describe("ChatScreen — 'convert this PRD into tickets' over an attached document", () => {
  it("imports the doc as a PRD and lands the panel on the Tickets tab", async () => {
    renderChat()
    const file = await attachDoc()
    await typeAndSend("Convert this PRD into tickets")

    // Uploaded the ORIGINAL file to the import endpoint for the active company…
    await waitFor(() => expect(importDoc).toHaveBeenCalledWith(file, "acme"))
    // …polled the already-kicked-off import to ready (third arg = live-preview
    // onPartial callback)…
    await waitFor(() => expect(resumePrdGeneration).toHaveBeenCalledWith(42, undefined, expect.any(Function)))
    // …kicked the ticket generation immediately (fire-and-forget; the Tickets
    // tab's poll picks the job up — no cache-read→generate round-trip first)…
    await waitFor(() => expect(storiesGenerate).toHaveBeenCalledWith(42))
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
    await waitFor(() => expect(resumePrdGeneration).toHaveBeenCalledWith(42, undefined, expect.any(Function)))
    // The panel stays on the PRD tab — the user asked for a PRD, not tickets —
    // and no ticket generation is kicked off.
    await waitFor(() => expect(panelTab()).toBe("prd"))
    expect(storiesGenerate).not.toHaveBeenCalled()
    // The doc replaces the brief-insight source; the old flow must not also run.
    expect(briefCurrent).not.toHaveBeenCalled()
    expect(runPrdGeneration).not.toHaveBeenCalled()
    expect(runAskGeneration).not.toHaveBeenCalled()
  })

  it("threads streamed partial HTML from the generation into shared content state", async () => {
    // Capture the onPartial callback ChatScreen passes to the poller and keep
    // the generation in flight, then emit a partial and observe it land in the
    // shared content state the PRD panel renders from.
    let capturedOnPartial: ((html: string) => void) | null = null
    resumePrdGeneration.mockImplementationOnce((...args: unknown[]) => {
      capturedOnPartial = args[2] as (html: string) => void
      return new Promise(() => {})
    })
    renderChat()
    await attachDoc()
    await typeAndSend("Convert this PRD into tickets")

    await waitFor(() => expect(capturedOnPartial).toBeTruthy())
    await act(async () => { capturedOnPartial!("<!doctype html><h1>Draft PRD</h1>") })
    expect(document.querySelector('[data-testid="partial-probe"]')?.textContent)
      .toContain("Draft PRD")
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

// ── Optimistic render BEFORE the network call (the reported latency bug) ─────
// The bug: submitAsk cleared the composer and the message "left", but nothing
// appeared in the chat thread until a multi-second backend call resolved —
// reading as a frozen app. These tests hold the mocked network promise UNRESOLVED
// and assert the user's turn + a loading/generating indicator are ALREADY in the
// DOM, then resolve and assert the final state.
describe("ChatScreen — optimistic render precedes the network call", () => {
  it("doc + PRD command: seeds the command turn + generating card BEFORE importDoc resolves", async () => {
    // Hold the import POST unresolved so we can observe the pre-network UI.
    let resolveImport!: (v: unknown) => void
    importDoc.mockImplementationOnce(() => new Promise((res) => { resolveImport = res as (v: unknown) => void }))

    renderChat()
    await attachDoc("Fraznet Enhancements.pptx")
    await typeAndSend("Convert this PRD into tickets")

    // The import POST is in flight (called) but NOT resolved…
    expect(importDoc).toHaveBeenCalledTimes(1)
    // …yet the user's command, the acknowledgment, and the generating PRD card
    // are ALL on screen already — the whole point of the fix.
    expect(document.body.textContent).toContain("Convert this PRD into tickets")
    expect(document.body.textContent).toContain("Importing your document as a PRD")
    expect(document.body.textContent).toContain("Generating PRD…")
    expect(document.querySelector('[data-testid="chat-insight-msg"]')).toBeTruthy()
    // The document chip rides the user's command turn.
    expect(document.body.textContent).toContain("Fraznet Enhancements.pptx")
    // The import hasn't landed, so the ready-only work (poll, tickets) hasn't run.
    expect(resumePrdGeneration).not.toHaveBeenCalled()
    expect(storiesGenerate).not.toHaveBeenCalled()

    // Now let the import resolve → it polls to ready and lands the Tickets panel.
    await act(async () => { resolveImport({ prd_id: 42, status: "generating", title: "Imported PRD" }) })
    await waitFor(() => expect(resumePrdGeneration).toHaveBeenCalledWith(42, undefined, expect.any(Function)))
    await waitFor(() => expect(storiesGenerate).toHaveBeenCalledWith(42))
    await waitFor(() => expect(panelTab()).toBe("tickets"))
    expect(runAskGeneration).not.toHaveBeenCalled()
  })

  it("plain question + attachment: shows the message turn + thinking skeleton BEFORE extractFile resolves", async () => {
    // Hold the extract POST unresolved so we can observe the pre-network UI.
    let resolveExtract!: (v: unknown) => void
    extractFile.mockImplementationOnce(() => new Promise((res) => { resolveExtract = res as (v: unknown) => void }))

    renderChat()
    await attachDoc("Fraznet Enhancements.pptx")
    await typeAndSend("What are the riskiest requirements in this deck?")

    // The extract POST is in flight (called) but NOT resolved…
    await waitFor(() => expect(extractFile).toHaveBeenCalled())
    // …yet the user's message, its doc chip, and a live thinking skeleton are
    // already rendered — the send no longer vanishes into a void.
    expect(document.body.textContent).toContain("What are the riskiest requirements in this deck?")
    expect(document.body.textContent).toContain("Fraznet Enhancements.pptx")
    expect(document.querySelector(".assistant-thinking")).toBeTruthy()
    // The ask itself hasn't been sent — extraction is still pending.
    expect(runAskGeneration).not.toHaveBeenCalled()

    // Resolve extraction → the ask fires with the extracted markdown folded in.
    await act(async () => { resolveExtract({ name: "Fraznet Enhancements.pptx", markdown: "## Slide 1\n\nFraznet MRT workflow" }) })
    await waitFor(() => expect(runAskGeneration).toHaveBeenCalled())
    const query = runAskGeneration.mock.calls[0][0] as string
    expect(query).toContain("Fraznet MRT workflow")
    expect(query).not.toContain("pptx-bytes")
  })

  it("extraction failure after the optimistic render removes the ghost turn but keeps the attachment", async () => {
    extractFile.mockRejectedValueOnce(new Error("could not parse file"))
    renderChat()
    await attachDoc("Fraznet Enhancements.pptx")
    await typeAndSend("What does this deck say?")

    await waitFor(() => expect(extractFile).toHaveBeenCalled())
    // The send is aborted — nothing reaches the ask agent…
    expect(runAskGeneration).not.toHaveBeenCalled()
    // …the optimistic turn is rolled back (no stranded "thinking" ghost): no
    // in-flight thinking skeleton lingers…
    await waitFor(() => expect(document.querySelector(".assistant-thinking")).toBeNull())
    // …and the attachment chip survives for a retry (not silently dropped).
    expect(document.body.textContent).toContain("Fraznet Enhancements.pptx")
  })
})

// ── Chronological order of the in-chat command flow ─────────────────────────
// The ordering bug: the PRD card + clarifying questions were pinned ABOVE the
// whole thread, so a "generate prd" command showed the card + questions ABOVE the
// user's own command message. The fix renders them INLINE, as the reply BELOW the
// command turn (thread[0]), so the conversation reads top-to-bottom.
describe("ChatScreen — command flow renders the PRD card + questions BELOW the command turn", () => {
  it("orders: user command turn → insight/PRD card → clarifying questions", async () => {
    // A pending clarifying question so PrdInputQuestions renders a real node to
    // position-check (it renders nothing when there are no questions).
    listInputQuestions.mockResolvedValue([
      { id: 1, prd_id: 42, ordinal: 0, tag: "need", prompt: "What is the serial-number logic?", options: [], status: "pending", answer: null },
    ])
    renderChat()
    await attachDoc("spec.pptx")
    await typeAndSend("generate a PRD from this")

    // Import completes → the PRD lands on the tab, so PrdInputQuestions mounts and
    // (with a pending question) renders.
    await waitFor(() => expect(resumePrdGeneration).toHaveBeenCalled())
    await waitFor(() => expect(panelTab()).toBe("prd"))
    const questions = await waitFor(() => {
      const el = document.querySelector('[data-testid="prd-input-questions"]')
      expect(el).toBeTruthy()
      return el as Element
    })
    expect(document.body.textContent).toContain("What is the serial-number logic?")

    const bubble = Array.from(document.querySelectorAll(".bc-user-bubble"))
      .find((n) => n.textContent?.includes("generate a PRD from this")) as Element
    const card = document.querySelector('[data-testid="chat-insight-msg"]') as Element
    expect(bubble).toBeTruthy()
    expect(card).toBeTruthy()

    // Document order: the user's command turn comes BEFORE the PRD card, which
    // comes BEFORE the clarifying questions.
    expect(bubble.compareDocumentPosition(card) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(card.compareDocumentPosition(questions) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    // The FIRST turn in the thread is the command turn, NOT the insight card.
    const firstTurn = document.querySelector(".bc-thread .bc-turn")
    expect(firstTurn?.getAttribute("data-testid")).not.toBe("chat-insight-msg")
    expect(firstTurn?.querySelector(".bc-user-bubble")?.textContent).toContain("generate a PRD from this")
  })
})

// ── Deictic edit phrasings beside an OPEN PRD tab ────────────────────────────
// "make this PRD shorter" typed next to an open PRD, with NO attachment, is a
// QUESTION about that PRD (the ask is PRD-grounded since #786) — it must NOT
// spawn a brand-new PRD via the command flow. With an attachment, "this PRD"
// names the file and the import flow still runs; non-deictic command phrasings
// ("generate a PRD for dark mode") stay commands everywhere.

// Open a PRD tab via the doc-import command, then clear the setup calls so each
// test asserts only its own dispatch. Leaves the active tab carrying prd_id 42.
async function openPrdTabViaImport() {
  await attachDoc("setup.pptx")
  await typeAndSend("generate a PRD from this")
  await waitFor(() => expect(importDoc).toHaveBeenCalledTimes(1))
  await waitFor(() => expect(panelTab()).toBe("prd"))
  // The tab's PRD has landed (resume poll resolved) before the test proceeds.
  await waitFor(() => expect(resumePrdGeneration).toHaveBeenCalled())
  runAskGeneration.mockClear()
  briefCurrent.mockClear()
}

// The PRD tab renders the in-tab composer (.bc-composer), not the landing one.
async function typeAndSendInTab(text: string) {
  const textarea = document.querySelector(".bc-composer-input") as HTMLTextAreaElement
  expect(textarea).toBeTruthy()
  await act(async () => { fireEvent.change(textarea, { target: { value: text } }) })
  const sendBtn = document.querySelector(".bc-send") as HTMLButtonElement
  expect(sendBtn).toBeTruthy()
  await act(async () => { fireEvent.click(sendBtn) })
}

describe("ChatScreen — deictic PRD phrasings beside an open PRD tab", () => {
  it.each([
    "make this PRD shorter",
    "make that PRD more concise",
    "make the current PRD two pages",
  ])("'%s' with no attachment goes to the ask agent, not a new PRD", async (phrase) => {
    renderChat()
    await openPrdTabViaImport()

    await typeAndSendInTab(phrase)

    // Answered by the (PRD-grounded) ask agent…
    await waitFor(() => expect(runAskGeneration).toHaveBeenCalled())
    expect(runAskGeneration.mock.calls[0][0]).toContain(phrase)
    // …never a brand-new PRD via either command flow.
    expect(generateFromTask).not.toHaveBeenCalled()
    expect(briefCurrent).not.toHaveBeenCalled()
    expect(importDoc).toHaveBeenCalledTimes(1) // only the setup import
  })

  it("attachment + deictic phrasing in a PRD tab STILL imports (the file is 'this PRD')", async () => {
    renderChat()
    await openPrdTabViaImport()

    // A distinct title so the second import opens its own tab (same-title
    // imports reuse the existing tab and skip the openTickets hop).
    importDoc.mockResolvedValueOnce({ prd_id: 43, status: "generating", title: "Updated spec" })
    resumePrdGeneration.mockResolvedValueOnce({
      ok: true, prd: { prd_id: 43, title: "Updated spec", metaLine: "", sections: [] },
    })
    const file = await attachDoc("updated-spec.pptx")
    await typeAndSendInTab("convert this PRD into tickets")

    // Second import: the attached document is what "this PRD" names.
    await waitFor(() => expect(importDoc).toHaveBeenCalledTimes(2))
    expect(importDoc).toHaveBeenLastCalledWith(file, "acme")
    await waitFor(() => expect(panelTab()).toBe("tickets"))
    expect(runAskGeneration).not.toHaveBeenCalled()
  })

  it("a NON-deictic task command in a PRD tab still generates a new PRD", async () => {
    renderChat()
    await openPrdTabViaImport()

    await typeAndSendInTab("generate a PRD for dark mode on mobile")

    await waitFor(() => expect(generateFromTask).toHaveBeenCalledWith("dark mode on mobile", false, undefined))
    expect(runAskGeneration).not.toHaveBeenCalled()
  })

  it("a generic 'generate a PRD' with no real conversation does NOT open the brief's top insight", async () => {
    renderChat()
    await openPrdTabViaImport()

    // The import-PRD tab's only turn is the import command itself — not a real
    // conversation — so a bare "generate a PRD" has nothing to seed from. It must
    // NOT fall back to the brief's top insight (the bug); it asks for a topic.
    const resumeCallsBefore = resumePrdGeneration.mock.calls.length
    await typeAndSendInTab("generate a PRD")
    // prdCommandFlow reaches its "ask for a topic" branch synchronously (no async
    // work before the toast), so a microtask flush settles it.
    await act(async () => { await Promise.resolve() })

    expect(briefCurrent).not.toHaveBeenCalled()        // no brief-insight fallback
    expect(generateFromTask).not.toHaveBeenCalled()    // no PRD generated
    expect(resumePrdGeneration.mock.calls.length).toBe(resumeCallsBefore) // no new PRD opened
    expect(runAskGeneration).not.toHaveBeenCalled()    // not the ask agent either
  })

  it("'make this ticket shorter' beside an open PRD tab goes to ask, not the Tickets panel", async () => {
    renderChat()
    await openPrdTabViaImport()

    await typeAndSendInTab("make this ticket shorter")

    await waitFor(() => expect(runAskGeneration).toHaveBeenCalled())
    // The panel is NOT hijacked to Tickets and no ticket generation starts.
    expect(panelTab()).toBe("prd")
    expect(storiesGenerate).not.toHaveBeenCalled()
  })

  it("'create tickets from this PRD' (no attachment) in a PRD tab still opens the Tickets panel", async () => {
    renderChat()
    await openPrdTabViaImport()

    await typeAndSendInTab("create tickets from this PRD")

    await waitFor(() => expect(panelTab()).toBe("tickets"))
    expect(runAskGeneration).not.toHaveBeenCalled()
  })
})

describe("ChatScreen — documents attached EARLIER in the thread ground a later PRD", () => {
  it("passes the extracted doc as sourceDocs when 'generate a PRD' comes later", async () => {
    renderChat()
    // Message 1: a doc + a PLAIN question — not a command, so it goes to the
    // ask agent and the extracted text is stamped onto the turn.
    await attachDoc()
    await typeAndSend("please review this deck")
    await waitFor(() => expect(extractFile).toHaveBeenCalled())
    await waitFor(() => expect(runAskGeneration).toHaveBeenCalled())

    // Message 2, same thread, NO new attachment: the command must still see
    // the earlier document (the reported bug: it was silently forgotten).
    await typeAndSendInTab("generate a PRD")

    await waitFor(() => expect(generateFromTask).toHaveBeenCalledTimes(1))
    expect(generateFromTask).toHaveBeenCalledWith(
      "please review this deck",
      false,
      [{ name: "Fraznet Enhancements.pptx", content: "## Slide 1\n\nFraznet MRT workflow" }],
    )
    // The doc grounds a chat-task PRD — it is NOT re-routed to the import flow
    // (that stays same-message-attachment only).
    expect(importDoc).not.toHaveBeenCalled()
  })

  it("a command with NO thread documents sends no sourceDocs", async () => {
    renderChat()
    await typeAndSend("our checkout drops users at the payment step")
    await waitFor(() => expect(runAskGeneration).toHaveBeenCalled())

    await typeAndSendInTab("generate a PRD")

    await waitFor(() => expect(generateFromTask).toHaveBeenCalledTimes(1))
    expect(generateFromTask).toHaveBeenCalledWith(
      "our checkout drops users at the payment step",
      false,
      undefined,
    )
  })
})
