// @vitest-environment jsdom
//
// TicketDetail is the editable in-panel ticket view. It loads saved overrides
// (ticketDataApi.getData) merged over the generated story, and persists each
// edit: description/AC -> saveDescription, the pickers + assignee -> saveFields,
// attachments/comments -> their CRUD. These tests mock the api + nav context and
// assert the save wiring + the ticket-key derivation.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

const api = vi.hoisted(() => ({
  getData: vi.fn(),
  saveDescription: vi.fn(),
  saveFields: vi.fn(),
  addAttachment: vi.fn(),
  removeAttachment: vi.fn(),
  addComment: vi.fn(),
  removeComment: vi.fn(),
  summarizeComments: vi.fn(),
  teamList: vi.fn(),
}))

vi.mock("../../../lib/api", async (orig) => {
  const actual = await orig<typeof import("../../../lib/api")>()
  return {
    ...actual,
    ticketDataApi: {
      getData: api.getData,
      saveDescription: api.saveDescription,
      saveFields: api.saveFields,
      addAttachment: api.addAttachment,
      removeAttachment: api.removeAttachment,
      addComment: api.addComment,
      removeComment: api.removeComment,
      summarizeComments: api.summarizeComments,
    },
    teamApi: { list: api.teamList },
  }
})

const showToast = vi.fn()
vi.mock("../../../context/NavigationContext", async (orig) => {
  const actual = await orig<typeof import("../../../context/NavigationContext")>()
  return { ...actual, useNavigation: () => ({ showToast }) }
})

import { TicketDetail, ticketKeyFor, normalizePriority } from "../TicketDetail"

const STORY = {
  title: "Guest alert data model",
  body: "One-click guest-alert for Deal Alerts.",
  acceptance_criteria: ["Admin can enable in one click"],
  priority: "P1",
  route: null,
}
const KEY = "prd-7-guest-alert-data-model"

function noEdits() {
  return {
    description: null, acceptance_criteria: null, title: null, priority: null,
    status: null, sprint: null, assignee: null, attachments: [], comments: [],
  }
}

beforeEach(() => {
  api.getData.mockResolvedValue(noEdits())
  api.saveDescription.mockResolvedValue({ ok: true })
  api.saveFields.mockResolvedValue({ ok: true })
  api.teamList.mockResolvedValue({ members: [] })
  api.summarizeComments.mockResolvedValue({ summary: null })
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

async function renderDetail(onBack = vi.fn()) {
  await act(async () => {
    render(React.createElement(TicketDetail, { story: STORY, index: 2, prdId: 7, onBack }))
  })
  await waitFor(() => expect(api.getData).toHaveBeenCalledWith(KEY))
  return onBack
}

describe("normalizePriority", () => {
  it("maps the skill's free-form words onto canonical P0–P3 labels", () => {
    expect(normalizePriority("urgent")).toBe("P0 — Critical")
    expect(normalizePriority("high")).toBe("P1 — High")
    expect(normalizePriority("normal")).toBe("P2 — Medium")
    expect(normalizePriority("low")).toBe("P3 — Low")
  })
  it("is idempotent on already-canonical labels and bare P-codes", () => {
    for (const p of ["P0 — Critical", "P1 — High", "P2 — Medium", "P3 — Low"]) {
      expect(normalizePriority(p)).toBe(p)
    }
    expect(normalizePriority("P1")).toBe("P1 — High")
    expect(normalizePriority("p0")).toBe("P0 — Critical")
  })
  it("falls back to P2 — Medium for empty/unknown values", () => {
    expect(normalizePriority(null)).toBe("P2 — Medium")
    expect(normalizePriority("")).toBe("P2 — Medium")
    expect(normalizePriority("banana")).toBe("P2 — Medium")
  })
})

describe("ticketKeyFor", () => {
  it("prefers the content-derived id when present", () => {
    expect(ticketKeyFor(7, { ...STORY, id: "3e7b3c1fa35a" })).toBe("prd-7-3e7b3c1fa35a")
  })
  it("falls back to a title slug for sets cached before id existed", () => {
    expect(ticketKeyFor(7, STORY)).toBe(KEY)
  })
})

describe("TicketDetail", () => {
  it("renders the generated story when there are no saved overrides", async () => {
    await renderDetail()
    expect((screen.getByLabelText("Ticket title") as HTMLInputElement).value).toBe("Guest alert data model")
    expect(screen.getByText("T-3")).toBeTruthy() // id chip = index+1
  })

  it("a fields-only edit (null description) keeps the generated body, not a blank", async () => {
    // Regression: status set but description/criteria null → fall back to the
    // generated story rather than blanking the description.
    api.getData.mockResolvedValue({
      ...noEdits(), status: "In progress", description: null, acceptance_criteria: null,
    })
    await renderDetail()
    expect(screen.getByText("One-click guest-alert for Deal Alerts.")).toBeTruthy()
    // The description region itself is the editor (contenteditable), seeded
    // with that generated body (edit-what-you-see).
    const box = screen.getByRole("textbox", { name: /ticket description/i })
    expect(box.getAttribute("contenteditable")).toBe("true")
    expect(box.textContent).toContain("One-click guest-alert for Deal Alerts.")
    // AC renders as a checklist item.
    expect(screen.getByText(/Admin can enable in one click/)).toBeTruthy()
  })

  it("shows a generated free-form priority as a rail pill (URGENT/HIGH/NORMAL)", async () => {
    // The skill emits "high"; the Details rail renders it as the reference's
    // three-pill vocabulary — HIGH — not the raw word.
    await act(async () => {
      render(React.createElement(TicketDetail, { story: { ...STORY, priority: "high" }, index: 0, prdId: 7, onBack: vi.fn() }))
    })
    await waitFor(() => expect(api.getData).toHaveBeenCalled())
    expect(screen.getByText("HIGH")).toBeTruthy()
    expect(screen.queryByText("high")).toBeNull()
  })

  it("saved overrides win over the generated story", async () => {
    api.getData.mockResolvedValue({ ...noEdits(), title: "Edited title", priority: "P0 — Critical" })
    await renderDetail()
    expect((screen.getByLabelText("Ticket title") as HTMLInputElement).value).toBe("Edited title")
  })

  it("editing the description persists via saveDescription", async () => {
    await renderDetail()
    // PRD-style: the styled text is contenteditable in place; blur auto-saves
    // (no editor swap, no Save button).
    const box = screen.getByRole("textbox", { name: /ticket description/i })
    await act(async () => {
      box.textContent = "New description"
      fireEvent.input(box)
      fireEvent.blur(box)
    })
    expect(api.saveDescription).toHaveBeenCalledWith(KEY, "New description", ["Admin can enable in one click"])
    // The edited text replaces the display.
    expect(screen.getByText("New description")).toBeTruthy()
  })

  it("blurring an untouched description does not save", async () => {
    await renderDetail()
    // Round trip: rendered DOM serializes back to exactly the displayed text.
    await act(async () => { fireEvent.blur(screen.getByRole("textbox", { name: /ticket description/i })) })
    expect(api.saveDescription).not.toHaveBeenCalled()
  })

  it("clicking an acceptance criterion edits it in place and blur auto-saves", async () => {
    await renderDetail()
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Edit acceptance criterion 1" })) })
    const input = screen.getByLabelText("Edit acceptance criterion")
    await act(async () => {
      fireEvent.change(input, { target: { value: "Admin can disable in one click" } })
      fireEvent.blur(input)
    })
    expect(api.saveDescription).toHaveBeenCalledWith(
      KEY, "One-click guest-alert for Deal Alerts.", ["Admin can disable in one click"],
    )
    expect(screen.getByText(/Admin can disable in one click/)).toBeTruthy()
  })

  it("emptying an acceptance criterion removes it", async () => {
    await renderDetail()
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Edit acceptance criterion 1" })) })
    const input = screen.getByLabelText("Edit acceptance criterion")
    await act(async () => {
      fireEvent.change(input, { target: { value: "" } })
      fireEvent.blur(input)
    })
    expect(api.saveDescription).toHaveBeenCalledWith(KEY, "One-click guest-alert for Deal Alerts.", [])
  })

  it("Escape cancels an in-place criterion edit without saving", async () => {
    await renderDetail()
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Edit acceptance criterion 1" })) })
    const input = screen.getByLabelText("Edit acceptance criterion")
    await act(async () => {
      fireEvent.change(input, { target: { value: "half-typed edit" } })
      fireEvent.keyDown(input, { key: "Escape" })
      fireEvent.blur(input)
    })
    expect(api.saveDescription).not.toHaveBeenCalled()
    expect(screen.getByText(/Admin can enable in one click/)).toBeTruthy()
  })

  it("adding a child issue persists via saveFields on blur", async () => {
    await renderDetail()
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Add child issue" })) })
    const input = screen.getByLabelText("Add child issue")
    await act(async () => {
      fireEvent.change(input, { target: { value: "Write the migration" } })
      fireEvent.blur(input)
    })
    expect(api.saveFields).toHaveBeenCalledWith(KEY, { subtasks: ["Write the migration"] })
    expect(screen.getByText("Write the migration")).toBeTruthy()
  })

  it("changing the status picker persists via saveFields", async () => {
    await renderDetail()
    // The rail's status button opens the picker; picking a status persists it.
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: /Backlog/ })) })
    await act(async () => { fireEvent.click(screen.getByText("Done")) })
    expect(api.saveFields).toHaveBeenCalledWith(KEY, { status: "Done" })
  })

  it("reassigning persists the picked team member via saveFields", async () => {
    api.teamList.mockResolvedValue({ members: [
      { user_id: "u-1", display_name: "Neville Crawley", email: "neville@slickdeals.net", role: "Product", avatar_url: null },
    ] })
    await renderDetail()
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: /reassign/i })) })
    await waitFor(() => expect(screen.getByText("Neville Crawley")).toBeTruthy())
    await act(async () => { fireEvent.click(screen.getByText("Neville Crawley")) })
    expect(api.saveFields).toHaveBeenCalledWith(KEY, {
      assignee: { user_id: "u-1", display_name: "Neville Crawley", email: "neville@slickdeals.net", role: "Product", avatar_url: null },
    })
  })

  it("Send shows Sending… and locks until the comment request settles", async () => {
    // Deferred promise: hold the request open to observe the in-flight state.
    let resolveAdd: (c: unknown) => void = () => {}
    api.addComment.mockReturnValue(new Promise((res) => { resolveAdd = res }))
    await renderDetail()

    await act(async () => {
      fireEvent.change(screen.getByPlaceholderText("Ask about this ticket…"), {
        target: { value: "First!" },
      })
      fireEvent.click(screen.getByRole("button", { name: /send/i }))
    })
    // In flight: label swaps, button + input lock, re-clicks can't double-post.
    const btn = screen.getByRole("button", { name: /sending…/i }) as HTMLButtonElement
    expect(btn.disabled).toBe(true)
    fireEvent.click(btn)
    expect(api.addComment).toHaveBeenCalledTimes(1)

    await act(async () => {
      resolveAdd({ id: 9, author: "Ada", body: "First!", time: "2026-07-11 12:00:00+00" })
    })
    // Settled: back to Send, input cleared for the next comment.
    expect(screen.getByRole("button", { name: /^send$/i })).toBeTruthy()
    expect((screen.getByPlaceholderText("Ask about this ticket…") as HTMLInputElement).value).toBe("")
  })

  it("posting a comment persists via addComment and shows the server-resolved author", async () => {
    // The backend attributes the comment to the signed-in user and echoes it
    // back; the client sends only the body.
    api.addComment.mockResolvedValue({ id: 1, author: "Ada Lovelace", body: "Looks good", time: "2026-07-09 18:00:00+00" })
    await renderDetail()
    const ta = screen.getByPlaceholderText("Ask about this ticket…")
    await act(async () => {
      fireEvent.change(ta, { target: { value: "Looks good" } })
      fireEvent.click(screen.getByRole("button", { name: /send/i }))
    })
    expect(api.addComment).toHaveBeenCalledWith(KEY, "Looks good")
    expect(screen.getByText("Ada Lovelace")).toBeTruthy()
  })

  it("shows the AI summary block once there are 2+ comments", async () => {
    api.getData.mockResolvedValue({
      ...noEdits(),
      comments: [
        { id: 1, author: "Sam", body: "Ship behind a flag?", time: "t1" },
        { id: 2, author: "Lee", body: "Yes, step 3 still open.", time: "t2" },
      ],
    })
    api.summarizeComments.mockResolvedValue({ summary: "Team aligned to ship behind a flag." })
    await renderDetail()
    await waitFor(() => expect(api.summarizeComments).toHaveBeenCalledWith(KEY))
    await waitFor(() => expect(screen.getByText("Team aligned to ship behind a flag.")).toBeTruthy())
  })

  it("Accept & propagate applies the proposed criterion to the ticket's AC", async () => {
    api.getData.mockResolvedValue({
      ...noEdits(),
      comments: [
        { id: 1, author: "Priya", body: "Competitor facts rot — add a freshness rule.", time: "t1" },
        { id: 2, author: "Sam", body: "Agreed, 30-day staleness.", time: "t2" },
      ],
    })
    api.summarizeComments.mockResolvedValue({
      summary: "Agreed to add a 30-day staleness rule.",
      proposed_criterion: "[failure] Given the card is older than 30 days, When opened, Then a staleness banner appears.",
    })
    api.addComment.mockResolvedValue({ id: 3, author: "Sprntly", body: "propagated", time: "t3" })
    await renderDetail()
    await waitFor(() => expect(screen.getByText(/Proposed acceptance criterion/)).toBeTruthy())

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /accept & propagate/i }))
    })
    // The proposed criterion is appended to the ticket's AC and persisted.
    expect(api.saveDescription).toHaveBeenCalledWith(
      KEY,
      "One-click guest-alert for Deal Alerts.",
      [
        "Admin can enable in one click",
        "[failure] Given the card is older than 30 days, When opened, Then a staleness banner appears.",
      ],
    )
  })

  it("does not summarize with fewer than 2 comments", async () => {
    api.getData.mockResolvedValue({
      ...noEdits(),
      comments: [{ id: 1, author: "Sam", body: "Only one.", time: "t1" }],
    })
    await renderDetail()
    expect(api.summarizeComments).not.toHaveBeenCalled()
  })

  it("Back invokes onBack", async () => {
    const onBack = await renderDetail()
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: /all tickets/i })) })
    expect(onBack).toHaveBeenCalled()
  })
})

// ── the ticket skill's canonical (structured) rendering ──────────────────────
const STRUCTURED = {
  id: "abc123",
  ticket_type: "build" as const,
  title: "Productboard Displacement Battle Card",
  body: "As an AE, I want a battle card, so that I can run the play.",
  what: "Create a one-page battle card for the displacement play.",
  why_now: "Productboard's June layoffs opened a window that closes at renewal.",
  user_story: "As an AE, I want one card with the pain hooks, so that I can run the play.",
  scope: ["Who to target", "The two pain hooks", "The price-counter script"],
  out_of_scope: "Pricing changes (that's T-5).",
  prd_section: "Part A §5 R2",
  ears_ids: ["E2"],
  signals: ["win/loss notes"],
  acceptance_criteria: [
    "Given a role-change, When the SDR opens the card, Then it provides a talk track.",
    "[failure] Given the card is older than 30 days, When opened, Then a staleness banner appears.",
  ],
  ac_inherited: true,
  subtasks: ["Pull win/loss notes", "[P] Draft objection table"],
  blocked_by: ["T-1 — Competitive Positioning One-Pager"],
  blocks: ["T-5 — Outbound Sequence"],
  story_points: 3,
  labels: ["sales-enablement"],
  data_gaps: [],
  priority: "urgent",
  route: "agent-ready",
}

describe("TicketDetail — structured (canonical) ticket", () => {
  it("renders the five-section description, inherited AC with tags, and links", async () => {
    await act(async () => {
      render(React.createElement(TicketDetail, { story: STRUCTURED, index: 1, prdId: 42, onBack: vi.fn() }))
    })
    await waitFor(() => expect(api.getData).toHaveBeenCalledWith("prd-42-abc123"))
    // Five-section description labels.
    for (const label of ["What", "Why now", "User story", "Out of scope"]) {
      expect(screen.getByText(label)).toBeTruthy()
    }
    // Inherited-AC note + failure tag, and a read-only count in the heading.
    expect(screen.getByText(/Inherited from the PRD/)).toBeTruthy()
    expect(screen.getByText("[failure]")).toBeTruthy()
    expect(screen.getByText("Acceptance criteria — 2")).toBeTruthy()
    // Child + linked issues + rail provenance (heading now carries a count).
    expect(screen.getByText(/Child issues — 2/)).toBeTruthy()
    expect(screen.getByText(/is blocked by/)).toBeTruthy()
    // Provenance shows in both the grounding footer and the rail.
    expect(screen.getAllByText("Part A §5 R2").length).toBeGreaterThanOrEqual(1)
  })

  it("blurring the untouched five-section description saves no override (round trip)", async () => {
    await act(async () => {
      render(React.createElement(TicketDetail, { story: STRUCTURED, index: 1, prdId: 42, onBack: vi.fn() }))
    })
    await waitFor(() => expect(api.getData).toHaveBeenCalled())
    await act(async () => { fireEvent.blur(screen.getByRole("textbox", { name: /ticket description/i })) })
    expect(api.saveDescription).not.toHaveBeenCalled()
  })

  it("editing a structured section in place persists the serialized labeled text", async () => {
    await act(async () => {
      render(React.createElement(TicketDetail, { story: STRUCTURED, index: 1, prdId: 42, onBack: vi.fn() }))
    })
    await waitFor(() => expect(api.getData).toHaveBeenCalled())
    const box = screen.getByRole("textbox", { name: /ticket description/i })
    // Simulate an in-place edit of the "What" paragraph.
    const what = box.querySelector("p.tkv2-dtx") as HTMLElement
    await act(async () => {
      what.textContent = "A sharper one-page battle card."
      fireEvent.input(box)
      fireEvent.blur(box)
    })
    const [, savedText] = api.saveDescription.mock.calls[0]
    // The edited section is in the override, in labeled-text form, with the
    // untouched sections intact.
    expect(savedText).toContain("What\nA sharper one-page battle card.")
    expect(savedText).toContain("The ticket must cover\n- Who to target")
    expect(savedText).toContain("Out of scope\nPricing changes (that's T-5).")
  })

  it("flags generated (non-inherited) criteria", async () => {
    await act(async () => {
      render(React.createElement(TicketDetail, {
        story: { ...STRUCTURED, ac_inherited: false }, index: 1, prdId: 42, onBack: vi.fn(),
      }))
    })
    await waitFor(() => expect(api.getData).toHaveBeenCalled())
    expect(screen.getByText(/GENERATED/)).toBeTruthy()
  })
})
