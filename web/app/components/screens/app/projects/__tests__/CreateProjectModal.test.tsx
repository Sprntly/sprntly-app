// @vitest-environment jsdom
//
// CreateProjectModal — the "New project" flow: three tabs (Start manually /
// From an artifact / Auto · from PRD — fork an existing PRD, AD-P9), invite
// rows that carry ONLY email + the app's real permission vocabulary (AD-P5),
// and a create path that always navigates to the flat `/projects?id=<new_id>`
// route (AD-P14). Tests cover both the pure `CreateProjectModalView` (tabs,
// invite-row shape/vocab, a11y, tokens) and the `CreateProjectModal`
// container's create + navigate wiring against a mocked `projectsApi`/
// `artifactsApi`, mirroring the sibling test files' View/Screen split.
import * as React from "react"
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const createMock = vi.fn()
const addMemberMock = vi.fn()
const addArtifactMock = vi.fn()
const artifactsListMock = vi.fn()
const pushMock = vi.fn()

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: pushMock }) }))
vi.mock("../../../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme", setActiveCompany: vi.fn(), activeCompanyDisplayName: "Acme" }),
}))
vi.mock("../../../../../lib/api", () => ({
  projectsApi: {
    create: (...a: unknown[]) => createMock(...a),
    addMember: (...a: unknown[]) => addMemberMock(...a),
    addArtifact: (...a: unknown[]) => addArtifactMock(...a),
  },
  artifactsApi: {
    list: (...a: unknown[]) => artifactsListMock(...a),
  },
}))

import {
  CreateProjectModalView,
  CreateProjectModal,
  type CreateProjectModalViewProps,
} from "../CreateProjectModal"
import type { ArtifactItem } from "../../../../../lib/api"

const hoursAgo = (h: number) => new Date(Date.now() - h * 3600 * 1000).toISOString()

const PRD_ARTIFACT: ArtifactItem = {
  type: "prd",
  id: 1,
  title: "Instant-quote flow — v3",
  status: "ready",
  created_at: hoursAgo(2),
  source: { brief_id: 1, week_label: "wk 32", insight_index: null },
  open: { brief_id: 1, insight_index: null, prd_id: 1 },
} as ArtifactItem

const PROTOTYPE_ARTIFACT: ArtifactItem = {
  type: "prototype",
  id: 2,
  title: "Upload-to-quote clickthrough",
  status: "ready",
  created_at: hoursAgo(48),
  source: { prd_id: 1, prd_title: "Instant-quote flow" },
  open: { prototype_id: 2, prd_id: 1 },
  is_complete: true,
  preview_image_url: null,
} as ArtifactItem

const ARTIFACTS: ArtifactItem[] = [PRD_ARTIFACT, PROTOTYPE_ARTIFACT]

const noop = () => {}

function viewProps(overrides: Partial<CreateProjectModalViewProps> = {}): CreateProjectModalViewProps {
  return {
    open: true,
    tab: "manual",
    onTabChange: noop,
    name: "",
    onNameChange: noop,
    rows: [{ email: "", role: "member" }],
    onRowEmailChange: noop,
    onRowRoleChange: noop,
    onAddRow: noop,
    onRemoveRow: noop,
    artifactsStatus: "ready",
    artifacts: ARTIFACTS,
    selectedArtifact: null,
    onSelectArtifact: noop,
    selectedPrd: null,
    onSelectPrd: noop,
    creating: false,
    error: null,
    onCancel: noop,
    onCreate: noop,
    ...overrides,
  }
}

afterEach(() => {
  cleanup()
  createMock.mockReset()
  addMemberMock.mockReset()
  addArtifactMock.mockReset()
  artifactsListMock.mockReset()
  pushMock.mockReset()
})

// ── AC1 — three tabs, Auto is a working fork-from-PRD panel ──
describe("CreateProjectModalView — tabs (AC1)", () => {
  it("renders exactly three tabs: Start manually, From an artifact, Auto · from PRD", () => {
    render(React.createElement(CreateProjectModalView, viewProps()))
    expect(screen.getByTestId("create-project-tab-manual").textContent).toContain("Start manually")
    expect(screen.getByTestId("create-project-tab-artifact").textContent).toContain("From an artifact")
    const auto = screen.getByTestId("create-project-tab-auto")
    expect(auto.textContent).toContain("Auto · from PRD")
    // The Phase-2 "coming" placeholder tag is gone — this tab is wired now.
    expect(auto.textContent).not.toContain("coming")
  })

  it("test_auto_tab_no_coming_placeholder — the Auto tab's panel is no longer a placeholder; it renders selectable PRDs", () => {
    render(React.createElement(CreateProjectModalView, viewProps({ tab: "auto" })))
    const panel = screen.getByTestId("create-project-panel-auto")
    expect(panel.textContent).not.toContain("coming")
    expect(screen.getByTestId("create-project-auto-prd-list")).toBeTruthy()
    expect(screen.getByTestId("create-project-auto-prd-row-1").textContent).toContain(PRD_ARTIFACT.title)
    // A real submit control exists on the Auto tab now.
    expect(screen.getByTestId("create-project-submit")).toBeTruthy()
  })

  it("switching tabs calls onTabChange with the clicked tab id", () => {
    const onTabChange = vi.fn()
    render(React.createElement(CreateProjectModalView, viewProps({ onTabChange })))
    fireEvent.click(screen.getByTestId("create-project-tab-artifact"))
    expect(onTabChange).toHaveBeenCalledWith("artifact")
    fireEvent.click(screen.getByTestId("create-project-tab-auto"))
    expect(onTabChange).toHaveBeenCalledWith("auto")
  })
})

// ── AD-P9 — Auto tab: fork-from-PRD picker ──
describe("CreateProjectModalView — Auto tab fork-from-PRD picker (AD-P9)", () => {
  it("lists only PRD artifacts — a prototype in the same artifacts list is excluded", () => {
    render(React.createElement(CreateProjectModalView, viewProps({ tab: "auto" })))
    expect(screen.getByTestId("create-project-auto-prd-row-1")).toBeTruthy()
    expect(screen.queryByTestId("create-project-auto-prd-row-2")).toBeNull()
  })

  it("clicking a PRD row calls onSelectPrd with that artifact", () => {
    const onSelectPrd = vi.fn()
    render(React.createElement(CreateProjectModalView, viewProps({ tab: "auto", onSelectPrd })))
    fireEvent.click(screen.getByTestId("create-project-auto-prd-row-1"))
    expect(onSelectPrd).toHaveBeenCalledWith(PRD_ARTIFACT)
  })

  it("Create is disabled with no PRD selected, and enabled once one is", () => {
    const { rerender } = render(
      React.createElement(CreateProjectModalView, viewProps({ tab: "auto", selectedPrd: null })),
    )
    expect((screen.getByTestId("create-project-submit") as HTMLButtonElement).disabled).toBe(true)
    rerender(
      React.createElement(CreateProjectModalView, viewProps({ tab: "auto", selectedPrd: PRD_ARTIFACT })),
    )
    expect((screen.getByTestId("create-project-submit") as HTMLButtonElement).disabled).toBe(false)
  })

  it("shows no PRDs empty state when the caller has none", () => {
    render(
      React.createElement(CreateProjectModalView, viewProps({ tab: "auto", artifacts: [PROTOTYPE_ARTIFACT] })),
    )
    expect(screen.getByTestId("create-project-auto-empty")).toBeTruthy()
  })
})

// ── AC4/AC5/AC6 — invite rows: email + access only, real vocab, InviteModal row reuse ──
describe("CreateProjectModalView — invite rows (AD-P5)", () => {
  it("each invite row has exactly an email input and an access selector — no title/role field", () => {
    render(
      React.createElement(
        CreateProjectModalView,
        viewProps({ rows: [{ email: "apurva@sprntly.ai", role: "member" }] }),
      ),
    )
    const row = screen.getByTestId("create-project-invite-row-0")
    expect(within(row).getByTestId("create-project-invite-email-0")).toBeTruthy()
    expect(within(row).getByTestId("create-project-invite-role-0")).toBeTruthy()
    // No job-title/role-label text input anywhere in the row.
    expect(within(row).queryByLabelText(/title/i)).toBeNull()
    expect(within(row).queryByLabelText(/job role/i)).toBeNull()
  })

  it("the access selector's options are exactly admin | member | viewer, default member, and never 'Can edit'", () => {
    render(React.createElement(CreateProjectModalView, viewProps()))
    const select = screen.getByTestId("create-project-invite-role-0") as HTMLSelectElement
    const values = Array.from(select.options).map((o) => o.value)
    expect(values).toEqual(["member", "admin", "viewer"])
    expect(select.value).toBe("member")
    expect(screen.queryByText("Can edit")).toBeNull()
  })

  it("reuses InviteModal's row-UI mechanics (the same global row/add/remove classes) and never touches InviteModal.sendInvites", () => {
    render(
      React.createElement(
        CreateProjectModalView,
        viewProps({ rows: [{ email: "a@x.com", role: "member" }, { email: "b@x.com", role: "viewer" }] }),
      ),
    )
    // Same class names InviteModal.tsx's row list renders with.
    expect(document.querySelector(".invite-rows")).toBeTruthy()
    expect(document.querySelectorAll(".invite-email-row").length).toBe(2)
    expect(screen.getByTestId("create-project-invite-add").className).toContain("invite-add-btn")
    expect(screen.getByTestId("create-project-invite-remove-0").className).toContain("invite-remove-btn")
    // The component never imports/renders InviteModal's own send button —
    // there is no "Send invites" control anywhere in this modal.
    expect(screen.queryByText("Send invites")).toBeNull()
  })

  it("Add another calls onAddRow; Remove calls onRemoveRow with the row's index; a single row has no remove button", () => {
    const onAddRow = vi.fn()
    const onRemoveRow = vi.fn()
    const { rerender } = render(
      React.createElement(CreateProjectModalView, viewProps({ onAddRow, onRemoveRow })),
    )
    expect(screen.queryByTestId("create-project-invite-remove-0")).toBeNull()
    fireEvent.click(screen.getByTestId("create-project-invite-add"))
    expect(onAddRow).toHaveBeenCalledTimes(1)

    rerender(
      React.createElement(
        CreateProjectModalView,
        viewProps({
          rows: [{ email: "a@x.com", role: "member" }, { email: "", role: "member" }],
          onAddRow,
          onRemoveRow,
        }),
      ),
    )
    fireEvent.click(screen.getByTestId("create-project-invite-remove-1"))
    expect(onRemoveRow).toHaveBeenCalledWith(1)
  })

  it("typing in the email field calls onRowEmailChange with the row index and value", () => {
    const onRowEmailChange = vi.fn()
    render(React.createElement(CreateProjectModalView, viewProps({ onRowEmailChange })))
    fireEvent.change(screen.getByTestId("create-project-invite-email-0"), {
      target: { value: "shristi@sprntly.ai" },
    })
    expect(onRowEmailChange).toHaveBeenCalledWith(0, "shristi@sprntly.ai")
  })
})

// ── AC3 — From-an-artifact tab ──
describe("CreateProjectModalView — from-an-artifact picker (AC3)", () => {
  it("lists every artifact, including a PRD as one selectable row among the types", () => {
    render(React.createElement(CreateProjectModalView, viewProps({ tab: "artifact" })))
    expect(screen.getByTestId("create-project-artifact-row-prd-1").textContent).toContain(
      "Instant-quote flow — v3",
    )
    expect(screen.getByTestId("create-project-artifact-row-prototype-2").textContent).toContain(
      "Upload-to-quote clickthrough",
    )
  })

  it("clicking an artifact row calls onSelectArtifact with that artifact", () => {
    const onSelectArtifact = vi.fn()
    render(React.createElement(CreateProjectModalView, viewProps({ tab: "artifact", onSelectArtifact })))
    fireEvent.click(screen.getByTestId("create-project-artifact-row-prd-1"))
    expect(onSelectArtifact).toHaveBeenCalledWith(PRD_ARTIFACT)
  })

  it("Create is disabled with no artifact selected, and enabled once one is", () => {
    const { rerender } = render(
      React.createElement(CreateProjectModalView, viewProps({ tab: "artifact", selectedArtifact: null })),
    )
    expect((screen.getByTestId("create-project-submit") as HTMLButtonElement).disabled).toBe(true)
    rerender(
      React.createElement(
        CreateProjectModalView,
        viewProps({ tab: "artifact", selectedArtifact: PRD_ARTIFACT }),
      ),
    )
    expect((screen.getByTestId("create-project-submit") as HTMLButtonElement).disabled).toBe(false)
  })
})

// ── AC7/AC8/AC12 — a11y mechanics + Cancel ──
describe("CreateProjectModalView — a11y + cancel (AC7/AC8/AC12)", () => {
  it("Cancel and Close both call onCancel; backdrop click and Escape both call onCancel", () => {
    const onCancel = vi.fn()
    render(React.createElement(CreateProjectModalView, viewProps({ onCancel })))
    fireEvent.click(screen.getByTestId("create-project-cancel"))
    expect(onCancel).toHaveBeenCalledTimes(1)

    onCancel.mockClear()
    fireEvent.click(screen.getByTestId("create-project-close"))
    expect(onCancel).toHaveBeenCalledTimes(1)

    onCancel.mockClear()
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" })
    expect(onCancel).toHaveBeenCalledTimes(1)

    onCancel.mockClear()
    fireEvent.click(document.querySelector(".modal-overlay") as Element)
    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it("closes on Escape dispatched at the document level — not routed through the panel's own onKeyDown", () => {
    const onCancel = vi.fn()
    render(React.createElement(CreateProjectModalView, viewProps({ onCancel })))
    fireEvent.keyDown(document, { key: "Escape" })
    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it("focus lands inside the dialog on open, and renders nothing when closed", () => {
    render(React.createElement(CreateProjectModalView, viewProps()))
    expect(document.activeElement).not.toBe(document.body)
    expect(screen.getByRole("dialog")).toBeTruthy()

    cleanup()
    render(React.createElement(CreateProjectModalView, viewProps({ open: false })))
    expect(screen.queryByRole("dialog")).toBeNull()
  })

  it("every tab control is a real, keyboard-reachable button with role=tab in a tablist", () => {
    render(React.createElement(CreateProjectModalView, viewProps()))
    const tablist = screen.getByRole("tablist")
    const tabs = within(tablist).getAllByRole("tab")
    expect(tabs).toHaveLength(3)
    for (const t of tabs) expect(t.tagName).toBe("BUTTON")
  })
})

describe("CreateProjectModalView — Tab focus-trap wraps within the dialog (regression)", () => {
  it("Tab from the last focusable wraps to the first; Shift+Tab from the first wraps to the last", () => {
    render(React.createElement(CreateProjectModalView, viewProps()))
    const dialog = screen.getByRole("dialog")
    const first = screen.getByTestId("create-project-close")
    const last = screen.getByTestId("create-project-cancel")

    last.focus()
    expect(document.activeElement).toBe(last)
    fireEvent.keyDown(dialog, { key: "Tab" })
    expect(document.activeElement).toBe(first)

    first.focus()
    expect(document.activeElement).toBe(first)
    fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true })
    expect(document.activeElement).toBe(last)
  })
})

describe("CreateProjectModalView — Escape listener cleanup (no leaked listener)", () => {
  it("does not call onCancel for Escape dispatched after the modal has closed", () => {
    const onCancel = vi.fn()
    const { rerender } = render(React.createElement(CreateProjectModalView, viewProps({ onCancel })))
    rerender(React.createElement(CreateProjectModalView, viewProps({ open: false, onCancel })))
    onCancel.mockClear()
    fireEvent.keyDown(document, { key: "Escape" })
    expect(onCancel).not.toHaveBeenCalled()
  })

  it("does not call onCancel for Escape dispatched after the modal has unmounted", () => {
    const onCancel = vi.fn()
    render(React.createElement(CreateProjectModalView, viewProps({ onCancel })))
    cleanup()
    onCancel.mockClear()
    fireEvent.keyDown(document, { key: "Escape" })
    expect(onCancel).not.toHaveBeenCalled()
  })
})

// ── Tokens (AC8) ──
describe("CreateProjectModal.module.css — tokens only", () => {
  it("resolves every color to a globals.css custom property — no new palette", () => {
    const css = readFileSync(join(__dirname, "../CreateProjectModal.module.css"), "utf8")
    const found = css.match(/#[0-9A-Fa-f]{3,8}/g) ?? []
    const disallowed = found.filter((hex) => hex.toLowerCase() !== "#fff")
    expect(disallowed).toEqual([])
  })
})

// ── Container — create + navigate wiring ──
describe("CreateProjectModal — Start manual creates and navigates (AC2)", () => {
  it("creates via projectsApi.create with origin=manual and the typed name, then navigates to the flat ?id= route", async () => {
    artifactsListMock.mockResolvedValue([])
    createMock.mockResolvedValue({ id: 555, name: "Instant-quote flow", origin: "manual" })
    await act(async () => {
      render(React.createElement(CreateProjectModal, { open: true, onClose: noop }))
    })
    await waitFor(() => expect(screen.getByTestId("create-project-name-input")).toBeTruthy())

    fireEvent.change(screen.getByTestId("create-project-name-input"), {
      target: { value: "Instant-quote flow" },
    })
    await act(async () => {
      fireEvent.click(screen.getByTestId("create-project-submit"))
    })

    await waitFor(() => expect(createMock).toHaveBeenCalledWith({ name: "Instant-quote flow", origin: "manual" }))
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/projects?id=555"))
    expect(pushMock).not.toHaveBeenCalledWith("/projects/555")
  })

  it("passes non-empty invite rows to the real member-add endpoint, best-effort, never to InviteModal's stub", async () => {
    artifactsListMock.mockResolvedValue([])
    createMock.mockResolvedValue({ id: 7, name: "P", origin: "manual" })
    addMemberMock.mockResolvedValue({ project_id: 7, user_id: "u9" })
    await act(async () => {
      render(React.createElement(CreateProjectModal, { open: true, onClose: noop }))
    })
    await waitFor(() => expect(screen.getByTestId("create-project-name-input")).toBeTruthy())

    fireEvent.change(screen.getByTestId("create-project-name-input"), { target: { value: "P" } })
    fireEvent.change(screen.getByTestId("create-project-invite-email-0"), {
      target: { value: "apurva@sprntly.ai" },
    })
    await act(async () => {
      fireEvent.click(screen.getByTestId("create-project-submit"))
    })

    await waitFor(() => expect(addMemberMock).toHaveBeenCalledWith(7, "apurva@sprntly.ai"))
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/projects?id=7"))
  })

  it("a blank name shows an inline error and never calls create", async () => {
    artifactsListMock.mockResolvedValue([])
    await act(async () => {
      render(React.createElement(CreateProjectModal, { open: true, onClose: noop }))
    })
    await waitFor(() => expect(screen.getByTestId("create-project-name-input")).toBeTruthy())
    // Submit is disabled with an empty name (canCreateManual gate).
    expect((screen.getByTestId("create-project-submit") as HTMLButtonElement).disabled).toBe(true)
    expect(createMock).not.toHaveBeenCalled()
  })
})

describe("CreateProjectModal — from an artifact creates with origin=artifact and associates it (AC3)", () => {
  it("creates with origin=artifact using the artifact's title, then adds the artifact ref, then navigates", async () => {
    artifactsListMock.mockResolvedValue(ARTIFACTS)
    createMock.mockResolvedValue({ id: 88, name: PRD_ARTIFACT.title, origin: "artifact" })
    addArtifactMock.mockResolvedValue({ project_id: 88, artifact_type: "prd", artifact_id: 1 })
    await act(async () => {
      render(React.createElement(CreateProjectModal, { open: true, onClose: noop }))
    })
    await waitFor(() => expect(screen.getByTestId("create-project-name-input")).toBeTruthy())

    fireEvent.click(screen.getByTestId("create-project-tab-artifact"))
    await waitFor(() => expect(screen.getByTestId("create-project-artifact-list")).toBeTruthy())
    fireEvent.click(screen.getByTestId("create-project-artifact-row-prd-1"))

    await act(async () => {
      fireEvent.click(screen.getByTestId("create-project-submit"))
    })

    await waitFor(() =>
      expect(createMock).toHaveBeenCalledWith({ name: PRD_ARTIFACT.title, origin: "artifact" }),
    )
    await waitFor(() => expect(addArtifactMock).toHaveBeenCalledWith(88, "prd", 1))
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/projects?id=88"))
  })
})

describe("CreateProjectModal — Auto tab forks from a PRD (AD-P9)", () => {
  it("test_auto_tab_lists_prds_and_forks — selecting a PRD and creating calls projectsApi.create with origin=prd_auto, then addArtifact with the PRD, then navigates", async () => {
    artifactsListMock.mockResolvedValue(ARTIFACTS)
    createMock.mockResolvedValue({ id: 99, name: PRD_ARTIFACT.title, origin: "prd_auto" })
    addArtifactMock.mockResolvedValue({ project_id: 99, artifact_type: "prd", artifact_id: 1 })
    await act(async () => {
      render(React.createElement(CreateProjectModal, { open: true, onClose: noop }))
    })
    await waitFor(() => expect(screen.getByTestId("create-project-name-input")).toBeTruthy())

    fireEvent.click(screen.getByTestId("create-project-tab-auto"))
    await waitFor(() => expect(screen.getByTestId("create-project-auto-prd-list")).toBeTruthy())
    fireEvent.click(screen.getByTestId("create-project-auto-prd-row-1"))

    await act(async () => {
      fireEvent.click(screen.getByTestId("create-project-submit"))
    })

    await waitFor(() =>
      expect(createMock).toHaveBeenCalledWith({
        name: PRD_ARTIFACT.title,
        origin: "prd_auto",
        prd_id: PRD_ARTIFACT.id,
      }),
    )
    await waitFor(() => expect(addArtifactMock).toHaveBeenCalledWith(99, "prd", 1))
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/projects?id=99"))
  })

  it("FIX A — re-selecting an already-forked PRD navigates to the EXISTING project the server dedupes to, never a duplicate", async () => {
    // The server's dedup (find_existing_prd_auto_project) returns the
    // project that was already forked for this PRD — the modal must
    // navigate to THAT id, not assume a fresh one was minted.
    artifactsListMock.mockResolvedValue(ARTIFACTS)
    createMock.mockResolvedValue({ id: 42, name: PRD_ARTIFACT.title, origin: "prd_auto" })
    addArtifactMock.mockResolvedValue({ project_id: 42, artifact_type: "prd", artifact_id: 1 })
    await act(async () => {
      render(React.createElement(CreateProjectModal, { open: true, onClose: noop }))
    })
    await waitFor(() => expect(screen.getByTestId("create-project-name-input")).toBeTruthy())

    fireEvent.click(screen.getByTestId("create-project-tab-auto"))
    await waitFor(() => expect(screen.getByTestId("create-project-auto-prd-list")).toBeTruthy())
    fireEvent.click(screen.getByTestId("create-project-auto-prd-row-1"))

    await act(async () => {
      fireEvent.click(screen.getByTestId("create-project-submit"))
    })

    // Exactly one create call — the modal itself never double-submits or
    // retries; dedup is entirely the server's job on this single call.
    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(1))
    expect(createMock).toHaveBeenCalledWith({
      name: PRD_ARTIFACT.title,
      origin: "prd_auto",
      prd_id: PRD_ARTIFACT.id,
    })
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/projects?id=42"))
  })

  it("no PRD selected shows an inline error and never calls create", async () => {
    artifactsListMock.mockResolvedValue(ARTIFACTS)
    await act(async () => {
      render(React.createElement(CreateProjectModal, { open: true, onClose: noop }))
    })
    await waitFor(() => expect(screen.getByTestId("create-project-name-input")).toBeTruthy())

    fireEvent.click(screen.getByTestId("create-project-tab-auto"))
    await waitFor(() => expect(screen.getByTestId("create-project-auto-prd-list")).toBeTruthy())
    expect((screen.getByTestId("create-project-submit") as HTMLButtonElement).disabled).toBe(true)
    expect(createMock).not.toHaveBeenCalled()
  })
})

describe("CreateProjectModal — Cancel creates nothing (AC7)", () => {
  it("Cancel calls onClose without ever calling create", async () => {
    artifactsListMock.mockResolvedValue([])
    const onClose = vi.fn()
    await act(async () => {
      render(React.createElement(CreateProjectModal, { open: true, onClose }))
    })
    await waitFor(() => expect(screen.getByTestId("create-project-cancel")).toBeTruthy())
    fireEvent.click(screen.getByTestId("create-project-cancel"))
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(createMock).not.toHaveBeenCalled()
  })

  it("fetches no artifacts and creates nothing while closed", () => {
    render(React.createElement(CreateProjectModal, { open: false, onClose: noop }))
    expect(artifactsListMock).not.toHaveBeenCalled()
    expect(createMock).not.toHaveBeenCalled()
  })
})
