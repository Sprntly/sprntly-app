// The chat's BACKLOG write path, client side.
//
// Asked for flat: "the chat system should understand backlog and ask questions
// just like we did for PRD, Report, Ticket". The reading half is grounding (a
// backend context block, covered in backend/tests/test_backlog_chat.py); this
// is the half that CHANGES something — the dispatch guard, the immediate
// applies, the questions that park in the dock, and the pick→operation mapping
// the popup's completion runs.
//
// Every write goes through the ordinary ideation routes the Backlog screen
// itself uses, so these mock `ideationApi` and assert on the calls: the plan is
// advisory, the routes are the write path, and nothing else may become one.
import { beforeEach, describe, expect, it, vi } from "vitest"

// Hoisted with the mock factory — `vi.mock` runs before any top-level const,
// so plain module variables are not readable from inside it.
const { chatPlan, create, setStatus, reorder } = vi.hoisted(() => ({
  chatPlan: vi.fn(),
  create: vi.fn(),
  setStatus: vi.fn(),
  reorder: vi.fn(),
}))

vi.mock("../../api", () => ({
  ideationApi: { chatPlan, create, setStatus, reorder },
  slackShareApi: {},
  ticketDataApi: {},
}))

import { dispatchChatIntent, type ChatIntentExecutors } from "../dispatchChatIntent"
import type { ChatIntentEnvelope } from "../../api"
import {
  applyBacklogOp,
  backlogOpsFromAnswers,
  runBacklogAction,
  type DockQuestion,
} from "../../../components/shared/chat-shell/conversation/actions"
import type { BacklogPlanQuestion } from "../../api"

function envelope(overrides: Partial<ChatIntentEnvelope> = {}): ChatIntentEnvelope {
  return {
    intent: "answer", confidence: 0.9, task: null, instruction: null,
    artifact_kind: null, artifact_type: null, artifact_query: null,
    artifact_template_id: null, artifact_template_name: null,
    reason: "test", source: "llm", prd_id: null, prd_title: null,
    ...overrides,
  } as ChatIntentEnvelope
}

function executors(over: Partial<ChatIntentExecutors> = {}) {
  return {
    onEditPrd: vi.fn(), onGenerateTickets: vi.fn(), onGeneratePrd: vi.fn(),
    onOpenArtifact: vi.fn(), onChangeTemplate: vi.fn(),
    onChangeTicketsTemplate: vi.fn(), onCreateArtifact: vi.fn(),
    onAssignTickets: vi.fn(), onListArtifacts: vi.fn(), onEditArtifact: vi.fn(),
    onAnswer: vi.fn(),
    ...over,
  } as ChatIntentExecutors & Record<string, ReturnType<typeof vi.fn>>
}

/** The action's two surface seams: it settles a turn and may raise a dock
 *  question. Records both so a test can read what the user would see. */
function config(canAskInDock = true) {
  const settled: string[] = []
  const asked: DockQuestion[] = []
  return {
    settled,
    asked,
    cfg: {
      emitTurn: vi.fn(),
      canAskInDock,
      runActionTurn: async (_q: string, work: () => Promise<{ reply: { answer: string } }>) => {
        const patch = await work()
        settled.push(patch.reply.answer)
        return { turnId: "turn-1" }
      },
      onDockQuestion: (_turnId: string, question: DockQuestion) => { asked.push(question) },
    } as never,
  }
}

const ITEM_QUESTION: BacklogPlanQuestion = {
  header: "Which idea",
  prompt: "Which export idea did you mean?",
  fills: "item_id",
  op: "status",
  status: "done",
  title: null,
  multi: false,
  options: [
    { value: "i-1", label: "CSV export fails", description: "#1 · Bug" },
    { value: "i-2", label: "Export is slow", description: "#2 · UI" },
  ],
}

const TAG_QUESTION: BacklogPlanQuestion = {
  header: "Type",
  prompt: "What kind of idea is “Dark mode”?",
  fills: "tag",
  op: "add",
  status: null,
  title: "Dark mode",
  multi: false,
  options: [
    { value: "something_broken", label: "Bug", description: null },
    { value: "something_new", label: "New initiative", description: null },
    { value: "something_better", label: "UI", description: null },
  ],
}

beforeEach(() => {
  chatPlan.mockReset()
  create.mockReset().mockResolvedValue({ id: "new-1", title: "Dark mode" })
  setStatus.mockReset().mockResolvedValue({})
  reorder.mockReset().mockResolvedValue({ items: [], count: 0 })
})

describe("dispatchChatIntent — backlog_action", () => {
  it("routes to onBacklogAction with the instruction", () => {
    const onBacklogAction = vi.fn()
    const ex = executors({ onBacklogAction })
    const env = envelope({ intent: "backlog_action", instruction: "mark the export bug done" })

    const result = dispatchChatIntent(env, { hasEditTarget: false, editTargetPrdId: null, ticketsTarget: null }, ex)

    expect(onBacklogAction).toHaveBeenCalledWith("mark the export bug done")
    expect(ex.onAnswer).not.toHaveBeenCalled()
    expect(result).toEqual({ handled: true })
  })

  it("falls through to the ask when the surface has no backlog executor", () => {
    // A surface with no question dock cannot finish the asking half, so it
    // answers rather than half-applying a change.
    const ex = executors()
    const env = envelope({ intent: "backlog_action", instruction: "mark it done" })

    const result = dispatchChatIntent(env, { hasEditTarget: false, editTargetPrdId: null, ticketsTarget: null }, ex)

    expect(ex.onAnswer).toHaveBeenCalled()
    expect(result).toEqual({ handled: false })
  })

  it("falls through to the ask when the envelope carries no instruction", () => {
    const onBacklogAction = vi.fn()
    const ex = executors({ onBacklogAction })
    const env = envelope({ intent: "backlog_action", instruction: null })

    const result = dispatchChatIntent(env, { hasEditTarget: false, editTargetPrdId: null, ticketsTarget: null }, ex)

    expect(onBacklogAction).not.toHaveBeenCalled()
    expect(ex.onAnswer).toHaveBeenCalled()
    expect(result).toEqual({ handled: false })
  })
})

describe("runBacklogAction — applies what is unambiguous", () => {
  it("adds an idea through the ordinary create route and says what it did", async () => {
    chatPlan.mockResolvedValue({
      operations: [{ op: "add", title: "Dark mode", tag: "something_new" }],
      questions: [], note: "",
    })
    const { settled, asked, cfg } = config()

    await runBacklogAction("add dark mode to the backlog", "add dark mode", cfg)

    expect(create).toHaveBeenCalledWith("Dark mode", "something_new")
    expect(settled[0]).toContain("Dark mode")
    expect(asked).toEqual([])
  })

  it("moves an idea's status and names it in the summary", async () => {
    chatPlan.mockResolvedValue({
      operations: [{ op: "status", item_id: "i-1", status: "done", title: "CSV export fails" }],
      questions: [], note: "",
    })
    const { settled, cfg } = config()

    await runBacklogAction("mark the csv export bug done", "mark it done", cfg)

    expect(setStatus).toHaveBeenCalledWith("i-1", "done")
    expect(settled[0]).toContain("CSV export fails")
  })

  it("re-orders with the full ranking the plan validated", async () => {
    chatPlan.mockResolvedValue({
      operations: [{ op: "reorder", ordered_ids: ["i-2", "i-1"] }],
      questions: [], note: "",
    })
    const { settled, cfg } = config()

    await runBacklogAction("push revenue items up", "re-sequence", cfg)

    expect(reorder).toHaveBeenCalledWith(["i-2", "i-1"])
    expect(settled[0]).toContain("re-ordered")
  })

  it("reports a write that failed instead of claiming it landed", async () => {
    chatPlan.mockResolvedValue({
      operations: [{ op: "status", item_id: "i-1", status: "done", title: "CSV export fails" }],
      questions: [], note: "",
    })
    setStatus.mockRejectedValue(new Error("500"))
    const { settled, cfg } = config()

    await runBacklogAction("mark it done", "mark it done", cfg)

    expect(settled[0]).toContain("couldn't be saved")
  })

  it("says nothing was changed when the plan fails outright", async () => {
    chatPlan.mockRejectedValue(new Error("backend down"))
    const { settled, asked, cfg } = config()

    await runBacklogAction("do something", "do something", cfg)

    expect(create).not.toHaveBeenCalled()
    expect(setStatus).not.toHaveBeenCalled()
    expect(settled[0]).toContain("Nothing was changed")
    expect(asked).toEqual([])
  })
})

describe("runBacklogAction — raises what it could not resolve", () => {
  it("parks the open questions in the dock and applies the rest now", async () => {
    chatPlan.mockResolvedValue({
      operations: [{ op: "add", title: "Dark mode", tag: "something_new" }],
      questions: [ITEM_QUESTION], note: "",
    })
    const { settled, asked, cfg } = config()

    await runBacklogAction("do two things", "do two things", cfg)

    // The unambiguous half is written immediately — the popup is not a gate on
    // work the request already stated.
    expect(create).toHaveBeenCalled()
    expect(asked).toEqual([
      { kind: "backlog", questions: [ITEM_QUESTION], applied: ["added “Dark mode”"] },
    ])
    expect(settled[0]).toContain("one more answer")
  })

  it("on a surface that cannot ask, says so rather than leaving a dead popup", async () => {
    chatPlan.mockResolvedValue({
      operations: [], questions: [ITEM_QUESTION], note: "",
    })
    const { settled, asked, cfg } = config(false)

    await runBacklogAction("mark the export one done", "mark it done", cfg)

    expect(asked).toEqual([])
    expect(settled[0]).toContain("can't collect")
  })
})

describe("backlogOpsFromAnswers — picks become operations", () => {
  it("one item pick becomes one status move carrying its title", () => {
    expect(backlogOpsFromAnswers(ITEM_QUESTION, ["i-2"])).toEqual([
      { op: "status", item_id: "i-2", status: "done", title: "Export is slow" },
    ])
  })

  it("a multi-pick fans out to one move per idea", () => {
    // "mark these three done" is one question and three writes.
    expect(backlogOpsFromAnswers({ ...ITEM_QUESTION, multi: true }, ["i-1", "i-2"])).toHaveLength(2)
  })

  it("a type pick completes the add it was asked about", () => {
    expect(backlogOpsFromAnswers(TAG_QUESTION, ["something_broken"])).toEqual([
      { op: "add", title: "Dark mode", tag: "something_broken" },
    ])
  })

  it("an unknown type is dropped rather than sent — the API 400s it", () => {
    expect(backlogOpsFromAnswers(TAG_QUESTION, ["something_else"])).toEqual([
      { op: "add", title: "Dark mode", tag: null },
    ])
  })

  it("an item question with no status yields nothing to write", () => {
    // The plan validates this away, so reaching here means a malformed
    // question — a status move with no status must not become a PATCH.
    expect(backlogOpsFromAnswers({ ...ITEM_QUESTION, status: null }, ["i-1"])).toEqual([])
  })
})

describe("applyBacklogOp — the one write helper both halves share", () => {
  it("returns null when the route rejects, so the caller can report it", async () => {
    create.mockRejectedValue(new Error("400"))

    expect(await applyBacklogOp({ op: "add", title: "X", tag: null })).toBeNull()
  })
})
