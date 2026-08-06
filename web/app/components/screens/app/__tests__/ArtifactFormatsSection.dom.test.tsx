// @vitest-environment jsdom
//
// Round-trip tests for the wired "Formats we write in" section: upload → poll →
// ready, the confirm gate in front of every governing action, the three
// admin-gated controls, and the states this screen has to get right because a
// wrong answer is a wrong answer about what the company's next document will
// look like.
//
// Matchers: native DOM only — NO @testing-library/jest-dom (repo convention).
// Role mocking follows ZoomConfigSlot.dom.test.tsx's `let orgRole` pattern.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const {
  ApiErrorCls,
  listMock,
  createMock,
  uploadMock,
  activateMock,
  deactivateMock,
  removeMock,
  compileMock,
  updateMock,
  previewMock,
} = vi.hoisted(() => {
  class ApiErrorCls extends Error {
    status: number
    body: unknown
    constructor(status: number, body: unknown) {
      super(`api ${status}`)
      this.status = status
      this.body = body
    }
  }
  return {
    ApiErrorCls,
    listMock: vi.fn(),
    createMock: vi.fn(),
    uploadMock: vi.fn(),
    activateMock: vi.fn(),
    deactivateMock: vi.fn(),
    removeMock: vi.fn(),
    compileMock: vi.fn(),
    updateMock: vi.fn(),
    previewMock: vi.fn(),
  }
})

vi.mock("../../../../lib/api", () => ({
  ApiError: ApiErrorCls,
  artifactTemplatesApi: {
    list: listMock,
    create: createMock,
    upload: uploadMock,
    activate: activateMock,
    deactivate: deactivateMock,
    remove: removeMock,
    compile: compileMock,
    update: updateMock,
    preview: previewMock,
    get: vi.fn(),
  },
}))

let orgRole: string | null = "admin"
let activeWorkspace: { id: string } | null = { id: "ws-1" }
vi.mock("../../../../context/WorkspaceContext", () => ({
  useWorkspace: () => ({
    orgRole,
    activeWorkspace,
    workspace: { display_name: "Acme" },
  }),
}))

const showToast = vi.fn()
vi.mock("../../../../context/NavigationContext", () => ({
  useNavigation: () => ({ showToast }),
}))

import { ArtifactFormatsSection } from "../ArtifactFormatsSection"
import type { ArtifactTemplate } from "../../../../lib/api"

function row(over: Partial<ArtifactTemplate> = {}): ArtifactTemplate {
  return {
    id: "t1",
    name: "Acme PRD v3",
    artifact_type: "prd",
    uploader_name: "Dana Okoye",
    created_at: "2026-08-03T10:00:00Z",
    updated_at: "2026-08-03T10:00:00Z",
    compile_status: "ready",
    is_active: false,
    source_chars: 4210,
    compile_summary: null,
    compile_note_count: 0,
    ...over,
  }
}

/** The real backend state today: nothing generates from a custom format yet. */
const NONE_LIVE = { prd: false, tickets: false, impl_spec: false }
/** What the world looks like once milestone 3 lands. */
const PRD_LIVE = { prd: true, tickets: false, impl_spec: false }

function listOf(rows: ArtifactTemplate[], enabled = PRD_LIVE) {
  return { templates: rows, generation_enabled: enabled }
}

async function mount() {
  await act(async () => {
    render(React.createElement(ArtifactFormatsSection))
  })
}

/** The names of the ROWS on screen.
 *
 *  Deliberately not `getByText(name)`: the ACTIVE format's name is rendered
 *  twice by design — once in the group header's "Now using:" claim and once on
 *  the row — so a bare text query is ambiguous exactly when the row matters
 *  most. Reading `.afmt-name` asks the question the tests actually mean. */
function rowNames(): string[] {
  return Array.from(document.querySelectorAll(".afmt-name")).map(
    (el) => el.textContent ?? "",
  )
}

beforeEach(() => {
  orgRole = "admin"
  activeWorkspace = { id: "ws-1" }
  listMock.mockResolvedValue(listOf([]))
  previewMock.mockResolvedValue({
    id: "t1",
    name: "Acme PRD v3",
    artifact_type: "prd",
    compile_status: "ready",
    compile_notes: [],
    format: "html",
    body: "<h1>{{title}}</h1>",
    section_map: { sections: [], unmapped_house: [], extra_sections: [] },
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  vi.useRealTimers()
})

describe("first load", () => {
  it("renders every visible group off one list call", async () => {
    await mount()
    await waitFor(() => expect(listMock).toHaveBeenCalledTimes(1))
    // Queried as HEADINGS, not by bare text: each type name now appears twice —
    // once as a tab and once as its group heading — and a bare getByText would
    // be ambiguous. The heading is the one that proves the group rendered.
    expect(screen.getByRole("heading", { name: "PRD" })).toBeTruthy()
    expect(screen.getByRole("heading", { name: "Tickets" })).toBeTruthy()
    // Engineering spec is withheld from the UI for now (HIDDEN_TYPES) — the
    // backend still accepts, compiles and generates from those formats.
    expect(screen.queryByRole("heading", { name: "Engineering spec" })).toBeNull()
  })

  it("defaults to All, and a type tab narrows to that group alone", async () => {
    await mount()
    await waitFor(() => expect(listMock).toHaveBeenCalledTimes(1))

    // "All" is the default so the three-way relationship is visible first.
    expect(
      screen.getByRole("tab", { name: /^All/ }).getAttribute("aria-selected"),
    ).toBe("true")

    fireEvent.click(screen.getByRole("tab", { name: /^Tickets/ }))
    expect(screen.getByRole("heading", { name: "Tickets" })).toBeTruthy()
    expect(screen.queryByRole("heading", { name: "PRD" })).toBeNull()
    expect(screen.queryByRole("heading", { name: "Engineering spec" })).toBeNull()

    // Filtering is client-side over one fetch — switching tabs must not refetch.
    expect(listMock).toHaveBeenCalledTimes(1)
  })

  it("renders the tickets 'not wired yet' note with the whole library empty", async () => {
    listMock.mockResolvedValue(listOf([], PRD_LIVE))
    await mount()
    await waitFor(() => expect(listMock).toHaveBeenCalled())
    expect(
      screen.getByText(/Sprntly doesn't write tickets from a custom format yet/),
    ).toBeTruthy()
    // PRD is live in this fixture, so it must NOT carry the note.
    expect(screen.queryByText(/Sprntly doesn't write PRDs/)).toBeNull()
  })

  it("all-false generation_enabled notes every group, PRD included", async () => {
    listMock.mockResolvedValue(listOf([], NONE_LIVE))
    await mount()
    await waitFor(() => expect(listMock).toHaveBeenCalled())
    expect(screen.getByText(/Sprntly doesn't write PRDs from a custom format yet/)).toBeTruthy()
  })

  it("a MISSING generation_enabled degrades to prd-only rather than to nothing", async () => {
    listMock.mockResolvedValue({ templates: [] })
    await mount()
    await waitFor(() => expect(listMock).toHaveBeenCalled())
    expect(screen.queryByText(/Sprntly doesn't write PRDs/)).toBeNull()
    expect(screen.getByText(/Sprntly doesn't write tickets/)).toBeTruthy()
  })
})

describe("list error", () => {
  it("shows the inline error and a Try again that refetches", async () => {
    listMock.mockRejectedValueOnce(new Error("network down"))
    await mount()
    await waitFor(() =>
      expect(screen.getByText(/We couldn't load your document formats/)).toBeTruthy(),
    )
    listMock.mockResolvedValue(listOf([row()]))
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Try again" }))
    })
    await waitFor(() => expect(listMock).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.getByText("Acme PRD v3")).toBeTruthy())
  })
})

describe("upload → compile → ready", () => {
  // REAL timers on purpose. `pollUntil` sleeps through `sleepUntilNextPoll`,
  // which also listens for `visibilitychange`; driving that with vitest's fake
  // timers fights Testing Library's own act/waitFor scheduling and made this
  // test lie about which flush the row appeared on. One real 3s interval is
  // cheap and the assertion is then honest about the shipped poller.
  it("adds the row at pending, polls the LIST, flips it to Ready and announces once", async () => {
    listMock.mockResolvedValue(listOf([]))
    let container: HTMLElement
    await act(async () => {
      container = render(React.createElement(ArtifactFormatsSection)).container
    })

    createMock.mockResolvedValue(row({ compile_status: "pending" }))
    // The poll's FIRST read fires the moment the row is inserted, and the
    // server is authoritative — so the list has to agree that the row exists,
    // exactly as the real backend does (the insert commits before the 201).
    listMock.mockResolvedValue(listOf([row({ compile_status: "pending" })]))

    // Open the modal from the PRD group's own button, so the type is
    // pre-selected.
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Add a PRD format" }))
    })
    await act(async () => {
      fireEvent.change(
        screen.getByLabelText("Paste your format as Markdown"),
        { target: { value: "# Product requirements" } },
      )
      fireEvent.change(screen.getByLabelText(/Name it/), {
        target: { value: "Acme PRD v3" },
      })
    })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Add format" }))
    })

    // Pasted markdown goes as JSON, never multipart.
    await waitFor(() =>
      expect(createMock).toHaveBeenCalledWith({
        name: "Acme PRD v3",
        artifact_type: "prd",
        source_md: "# Product requirements",
      }),
    )
    expect(uploadMock).not.toHaveBeenCalled()
    // The modal closes immediately and the user watches the ROW, never a
    // spinner standing in for the list.
    await waitFor(() => expect(screen.getByText("Queued")).toBeTruthy())
    expect(screen.queryByRole("dialog")).toBeNull()
    expect(showToast).toHaveBeenCalledWith(
      "Format added",
      expect.stringMatching(
        /We're checking .Acme PRD v3. against what a Sprntly PRD needs/,
      ),
    )

    // The poll reads the LIST — one request covers every compiling row.
    const callsBefore = listMock.mock.calls.length
    listMock.mockResolvedValue(listOf([row({ compile_status: "ready" })]))
    await waitFor(() => expect(screen.getByText("Ready")).toBeTruthy(), {
      timeout: 10_000,
    })
    expect(listMock.mock.calls.length).toBeGreaterThan(callsBefore)

    // (b) of the three ways they learn: ONE section-level live region.
    const live = container!.querySelector(".sr-only[role='status']")
    expect(live?.textContent).toBe("Acme PRD v3 is ready to activate.")
    // (c) the toast, because they may have navigated away by now.
    expect(showToast).toHaveBeenCalledWith(
      "Acme PRD v3 is ready",
      expect.stringMatching(/Preview it to see what Sprntly will produce/),
    )
  }, 20_000)
})

describe("activate", () => {
  beforeEach(() => {
    listMock.mockResolvedValue(listOf([row()], PRD_LIVE))
  })

  it("is confirm-gated — no API call until the confirm is accepted", async () => {
    await mount()
    await waitFor(() => expect(screen.getByText("Acme PRD v3")).toBeTruthy())
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Use this format" }))
    })
    // The dialog is up and nothing has been sent.
    expect(screen.getByText(/Write every PRD in .Acme PRD v3.\?/)).toBeTruthy()
    expect(activateMock).not.toHaveBeenCalled()

    // Cancelling still sends nothing.
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Cancel" }))
    })
    expect(activateMock).not.toHaveBeenCalled()
  })

  it("names the blast radius in four clauses and only then activates", async () => {
    activateMock.mockResolvedValue(row({ is_active: true }))
    await mount()
    await waitFor(() => expect(screen.getByText("Acme PRD v3")).toBeTruthy())
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Use this format" }))
    })
    const body = screen.getByText(/From now on, every PRD Sprntly writes for Acme/)
    expect(body.textContent).toMatch(/in every workspace, for everyone on the team/)
    expect(body.textContent).toMatch(/keep the format they were written in/)
    expect(body.textContent).toMatch(/switch back at any time/)

    await act(async () => {
      // The dialog's confirm, not the row's button of the same name.
      const dialog = screen.getByRole("dialog")
      fireEvent.click(within(dialog).getByRole("button", { name: "Use this format" }))
    })
    await waitFor(() => expect(activateMock).toHaveBeenCalledWith("t1"))
    expect(showToast).toHaveBeenCalledWith(
      "Acme PRD v3 is now your PRD format",
      expect.stringMatching(/Documents already written are unchanged/),
    )
  })

  it("names the format it replaces when one is already active", async () => {
    listMock.mockResolvedValue(
      listOf(
        [
          row({ id: "old", name: "Acme PRD v2", is_active: true }),
          row({ id: "new", name: "Acme PRD v3" }),
        ],
        PRD_LIVE,
      ),
    )
    await mount()
    await waitFor(() => expect(screen.getByText("Acme PRD v3")).toBeTruthy())
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Use this format" }))
    })
    expect(
      screen.getByText(/This replaces .Acme PRD v2., which stays in your library\./),
    ).toBeTruthy()
  })

  it("translates the 409 apiErrorMessage cannot read, and never prints the raw note", async () => {
    activateMock.mockRejectedValue(
      new ApiErrorCls(409, {
        detail: {
          message: "This format isn't ready.",
          code: "not_ready",
          notes: [{ code: "missing_evidence_list", message: "no `ul.ev`" }],
        },
      }),
    )
    await mount()
    await waitFor(() => expect(screen.getByText("Acme PRD v3")).toBeTruthy())
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Use this format" }))
    })
    await act(async () => {
      const dialog = screen.getByRole("dialog")
      fireEvent.click(within(dialog).getByRole("button", { name: "Use this format" }))
    })
    await waitFor(() => expect(showToast).toHaveBeenCalled())
    const [title, sub] = showToast.mock.calls[showToast.mock.calls.length - 1]
    expect(title).toBe("This format isn't ready yet")
    expect(sub).toMatch(/bulleted evidence list/)
    expect(sub).not.toContain("ul.ev")
    expect(sub).not.toMatch(/Request failed/)
  })

  it("a 404 on activate drops the row and never says 'access'", async () => {
    activateMock.mockRejectedValue(new ApiErrorCls(404, { detail: "Format not found." }))
    await mount()
    await waitFor(() => expect(screen.getByText("Acme PRD v3")).toBeTruthy())
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Use this format" }))
    })
    await act(async () => {
      const dialog = screen.getByRole("dialog")
      fireEvent.click(within(dialog).getByRole("button", { name: "Use this format" }))
    })
    await waitFor(() => expect(screen.queryByText("Acme PRD v3")).toBeNull())
    const [title, sub] = showToast.mock.calls[showToast.mock.calls.length - 1]
    expect(title).toBe("That format isn't here anymore.")
    expect(`${title} ${sub}`.toLowerCase()).not.toContain("access")
  })
})

describe("deactivate", () => {
  it("offers the built-in on the active row, behind its own confirm", async () => {
    listMock.mockResolvedValue(listOf([row({ is_active: true })], PRD_LIVE))
    deactivateMock.mockResolvedValue(row({ is_active: false }))
    await mount()
    await waitFor(() => expect(rowNames()).toEqual(["Acme PRD v3"]))
    await act(async () => {
      fireEvent.click(
        screen.getByRole("button", { name: "Use Sprntly's built-in format instead" }),
      )
    })
    expect(screen.getByText(/Go back to Sprntly's built-in PRD format\?/)).toBeTruthy()
    expect(deactivateMock).not.toHaveBeenCalled()
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Use the built-in format" }))
    })
    await waitFor(() => expect(deactivateMock).toHaveBeenCalledWith("t1"))
  })
})

describe("delete", () => {
  it("a non-active delete says it is company-wide and undoable-never", async () => {
    listMock.mockResolvedValue(listOf([row()], PRD_LIVE))
    removeMock.mockResolvedValue({
      deleted: true,
      id: "t1",
      artifact_type: "prd",
      fell_back_to_builtin: false,
    })
    await mount()
    await waitFor(() => expect(screen.getByText("Acme PRD v3")).toBeTruthy())
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^Delete / }))
    })
    expect(screen.getByText(/Delete .Acme PRD v3.\?/)).toBeTruthy()
    expect(screen.getByText(/It's removed for everyone at Acme\./)).toBeTruthy()
    expect(screen.queryByText(/go back to Sprntly's built-in/)).toBeNull()
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Delete format" }))
    })
    await waitFor(() => expect(removeMock).toHaveBeenCalledWith("t1"))
    await waitFor(() => expect(screen.queryByText("Acme PRD v3")).toBeNull())
  })

  it("deleting the ACTIVE one names the built-in fallback in the confirm body", async () => {
    listMock.mockResolvedValue(listOf([row({ is_active: true })], PRD_LIVE))
    removeMock.mockResolvedValue({
      deleted: true,
      id: "t1",
      artifact_type: "prd",
      fell_back_to_builtin: true,
    })
    await mount()
    await waitFor(() => expect(rowNames()).toEqual(["Acme PRD v3"]))
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^Delete / }))
    })
    expect(
      screen.getByText(/Delete .Acme PRD v3. — the format you're using\?/),
    ).toBeTruthy()
    expect(
      screen.getByText(/new PRDs go back to Sprntly's built-in format/),
    ).toBeTruthy()
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Delete format" }))
    })
    await waitFor(() => expect(removeMock).toHaveBeenCalledWith("t1"))
    expect(showToast).toHaveBeenCalledWith(
      "Format deleted",
      "PRDs are back to Sprntly's built-in format.",
    )
  })

  it("keeps the row when the delete fails, and surfaces the reason", async () => {
    listMock.mockResolvedValue(listOf([row()], PRD_LIVE))
    removeMock.mockRejectedValue(new Error("nope"))
    await mount()
    await waitFor(() => expect(screen.getByText("Acme PRD v3")).toBeTruthy())
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^Delete / }))
    })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Delete format" }))
    })
    await waitFor(() => expect(removeMock).toHaveBeenCalled())
    expect(screen.getByText("Acme PRD v3")).toBeTruthy()
    expect(showToast).toHaveBeenCalledWith("That didn't work", "nope")
  })
})

describe("role gating — three actions, not one", () => {
  it("a non-admin sees the denial text and NO activate button", async () => {
    orgRole = "member"
    listMock.mockResolvedValue(listOf([row()], PRD_LIVE))
    await mount()
    await waitFor(() => expect(screen.getByText("Acme PRD v3")).toBeTruthy())
    expect(
      screen.getByText("Only an admin can change your team's format."),
    ).toBeTruthy()
    expect(screen.queryByRole("button", { name: "Use this format" })).toBeNull()
  })

  it("a non-admin keeps Delete on a NON-active row", async () => {
    orgRole = "member"
    listMock.mockResolvedValue(listOf([row()], PRD_LIVE))
    await mount()
    await waitFor(() => expect(screen.getByText("Acme PRD v3")).toBeTruthy())
    expect(screen.getByRole("button", { name: /^Delete / })).toBeTruthy()
    expect(
      screen.queryByText("Only an admin can delete the format your team is using."),
    ).toBeNull()
  })

  it("a non-admin gets the delete-specific line on the ACTIVE row", async () => {
    orgRole = "member"
    listMock.mockResolvedValue(listOf([row({ is_active: true })], PRD_LIVE))
    await mount()
    await waitFor(() => expect(rowNames()).toEqual(["Acme PRD v3"]))
    expect(
      screen.getByText("Only an admin can delete the format your team is using."),
    ).toBeTruthy()
    expect(screen.queryByRole("button", { name: /^Delete / })).toBeNull()
    // Information is never role-gated — only the action. The PRD group header
    // still tells a member exactly what is in use.
    const prdGroup = document
      .getElementById("afmt-group-prd")!
      .closest("section")!
    expect(within(prdGroup as HTMLElement).getByText(/Now using:/).textContent).toContain(
      "Acme PRD v3",
    )
  })

  it("orgRole null shows NEITHER denial string", async () => {
    orgRole = null
    listMock.mockResolvedValue(
      listOf(
        [row({ id: "a", name: "Plain one" }), row({ id: "b", name: "Active one", is_active: true })],
        PRD_LIVE,
      ),
    )
    await mount()
    // Active first, then the rest — the order the hook sorts into.
    await waitFor(() => expect(rowNames()).toEqual(["Active one", "Plain one"]))
    expect(screen.queryByText(/Only an admin can/)).toBeNull()
    const busy = document.querySelectorAll("[aria-busy='true'][disabled]")
    expect(busy.length).toBeGreaterThan(0)
  })
})

describe("workspace change", () => {
  it("clears the previous company's rows rather than leaving them on screen", async () => {
    listMock.mockResolvedValue(listOf([row({ name: "Company A format" })], PRD_LIVE))
    let rerender: (ui: React.ReactElement) => void
    await act(async () => {
      rerender = render(React.createElement(ArtifactFormatsSection)).rerender
    })
    await waitFor(() => expect(screen.getByText("Company A format")).toBeTruthy())

    // Switch workspaces AND make the new fetch fail: if the rows had not been
    // cleared first, company A's format would still be on screen.
    activeWorkspace = { id: "ws-2" }
    listMock.mockRejectedValue(new Error("down"))
    await act(async () => {
      rerender!(React.createElement(ArtifactFormatsSection))
    })
    await waitFor(() => expect(screen.queryByText("Company A format")).toBeNull())
    expect(listMock).toHaveBeenCalledTimes(2)
  })
})

describe("recompile", () => {
  it("Check again re-queues the check and re-reads the list", async () => {
    listMock.mockResolvedValue(
      listOf(
        [
          row({
            compile_status: "needs_review",
            compile_summary: "no `ul.ev`",
            compile_note_count: 2,
          }),
        ],
        PRD_LIVE,
      ),
    )
    compileMock.mockResolvedValue({ id: "t1", compile_status: "pending" })
    await mount()
    await waitFor(() => expect(screen.getByText("Needs a look")).toBeTruthy())
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Check again" }))
    })
    await waitFor(() => expect(compileMock).toHaveBeenCalledWith("t1"))
    await waitFor(() => expect(listMock.mock.calls.length).toBeGreaterThan(1))
  })
})

// The highest-consequence state on this surface: the format the whole company
// writes in is being re-checked. Settled 2026-08-06 —`resolve_prd_template`
// gates on `compiled != ''`, so the last good skeleton keeps serving and the
// reassuring copy is also the honest copy.
describe("the active format being re-checked", () => {
  it("says the previous version is still serving, and stops saying so once the check lands", async () => {
    listMock.mockResolvedValue(
      listOf([row({ is_active: true, compile_status: "compiling" })], PRD_LIVE),
    )
    await mount()
    await waitFor(() => expect(rowNames()).toEqual(["Acme PRD v3"]))

    expect(
      screen.getByText(
        /Still writing PRDs in the version you had — we'll switch to your edit once it checks out\./,
      ),
    ).toBeTruthy()
    // Both signals stay on the row; the line explains the pair rather than
    // hiding either half of it.
    expect(screen.getByText("Active — in use now")).toBeTruthy()
    expect(screen.getByText("Checking…")).toBeTruthy()

    // The PRD group must not claim the built-in has taken over.
    const prdGroup = document
      .getElementById("afmt-group-prd")!
      .closest("section") as HTMLElement
    expect(within(prdGroup).queryByText(/Now using: Sprntly's built-in/)).toBeNull()
    expect(within(prdGroup).getByText(/Now using:/).textContent).toContain(
      "Acme PRD v3",
    )

    // The poll lands: the reassurance has done its job and goes away.
    listMock.mockResolvedValue(
      listOf([row({ is_active: true, compile_status: "ready" })], PRD_LIVE),
    )
    await waitFor(() => expect(screen.getByText("Ready")).toBeTruthy(), {
      timeout: 10_000,
    })
    expect(
      screen.queryByText(/Still writing PRDs in the version you had/),
    ).toBeNull()
  }, 20_000)
})

describe("rename", () => {
  it("PATCHes only the name and swaps the row in place", async () => {
    listMock.mockResolvedValue(listOf([row()], PRD_LIVE))
    updateMock.mockResolvedValue(row({ name: "Acme PRD v4" }))
    await mount()
    await waitFor(() => expect(screen.getByText("Acme PRD v3")).toBeTruthy())
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^Rename / }))
    })
    await act(async () => {
      fireEvent.change(screen.getByLabelText(/Rename Acme PRD v3/), {
        target: { value: "Acme PRD v4" },
      })
    })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Save" }))
    })
    await waitFor(() =>
      expect(updateMock).toHaveBeenCalledWith("t1", { name: "Acme PRD v4" }),
    )
    await waitFor(() => expect(screen.getByText("Acme PRD v4")).toBeTruthy())
  })
})
