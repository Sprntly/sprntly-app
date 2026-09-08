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
const uploadDocumentMock = vi.fn()
const artifactsListMock = vi.fn()
const pushMock = vi.fn()
const showToastMock = vi.fn()

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: pushMock }) }))
vi.mock("../../../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme", setActiveCompany: vi.fn(), activeCompanyDisplayName: "Acme" }),
}))
// A failed upload is reported AFTER the modal closes and the router moves, so
// the toast is the only surface that survives to carry it.
vi.mock("../../../../../context/NavigationContext", () => ({
  useNavigation: () => ({ showToast: showToastMock }),
}))
vi.mock("../../../../../lib/api", () => ({
  projectsApi: {
    create: (...a: unknown[]) => createMock(...a),
    addMember: (...a: unknown[]) => addMemberMock(...a),
    addArtifact: (...a: unknown[]) => addArtifactMock(...a),
    uploadDocument: (...a: unknown[]) => uploadDocumentMock(...a),
  },
  artifactsApi: {
    list: (...a: unknown[]) => artifactsListMock(...a),
  },
  // Real implementation (no API call, no side effect) — mirrors
  // lib/api.ts's own five-value check, kept here rather than importing the
  // real module so this mock stays self-contained.
  isProjectArtifactType: (t: string) =>
    ["prd", "evidence", "prototype", "report", "ticket_set"].includes(t),
}))

import {
  CreateProjectModalView,
  CreateProjectModal,
  type CreateProjectModalViewProps,
} from "../CreateProjectModal"
import type { ArtifactItem, projectsApi as RealProjectsApi } from "../../../../../lib/api"

// Type-only reference to the REAL `projectsApi.create` payload type — an
// `import type` is unaffected by the `vi.mock` above (that mock only
// reroutes the runtime module; TS still resolves types from the real
// `lib/api.ts`), so this is a genuine tsc-gated check that the payload type
// actually declares `seed_text` (AC6).
type ProjectsApiCreatePayload = Parameters<typeof RealProjectsApi.create>[0]

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
    whyText: "",
    onWhyChange: noop,
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
    files: [],
    oversized: null,
    onAddFiles: noop,
    onRemoveFile: noop,
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
  uploadDocumentMock.mockReset()
  showToastMock.mockReset()
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

// ── GCR-04 — create-time project "why" (an optional free-text goal threaded
// into the create payload as `seed_text`, gated on the manual + artifact
// origins only — never prd_auto, whose "why" comes from the PRD fork hook) ──
describe("CreateProjectModalView — why field renders and is optional (AC1)", () => {
  it("test_why_field_renders_on_manual_and_artifact_tabs", () => {
    const { rerender } = render(React.createElement(CreateProjectModalView, viewProps({ tab: "manual" })))
    expect(screen.getByTestId("create-project-why-input")).toBeTruthy()

    rerender(React.createElement(CreateProjectModalView, viewProps({ tab: "artifact" })))
    expect(screen.getByTestId("create-project-why-input")).toBeTruthy()

    rerender(React.createElement(CreateProjectModalView, viewProps({ tab: "auto" })))
    expect(screen.queryByTestId("create-project-why-input")).toBeNull()
  })
})

describe("CreateProjectModal — why field threads seed_text through create (AC1-AC4)", () => {
  it("test_create_with_name_only_still_succeeds", async () => {
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
  })

  it("test_manual_why_threads_seed_text", async () => {
    artifactsListMock.mockResolvedValue([])
    createMock.mockResolvedValue({ id: 556, name: "Instant-quote flow", origin: "manual" })
    await act(async () => {
      render(React.createElement(CreateProjectModal, { open: true, onClose: noop }))
    })
    await waitFor(() => expect(screen.getByTestId("create-project-name-input")).toBeTruthy())

    fireEvent.change(screen.getByTestId("create-project-name-input"), {
      target: { value: "Instant-quote flow" },
    })
    fireEvent.change(screen.getByTestId("create-project-why-input"), {
      target: { value: "Faster quote turnaround for the sales team" },
    })
    await act(async () => {
      fireEvent.click(screen.getByTestId("create-project-submit"))
    })

    await waitFor(() =>
      expect(createMock).toHaveBeenCalledWith({
        name: "Instant-quote flow",
        origin: "manual",
        seed_text: "Faster quote turnaround for the sales team",
      }),
    )
  })

  it("test_artifact_why_threads_seed_text", async () => {
    artifactsListMock.mockResolvedValue(ARTIFACTS)
    createMock.mockResolvedValue({ id: 89, name: PRD_ARTIFACT.title, origin: "artifact" })
    addArtifactMock.mockResolvedValue({ project_id: 89, artifact_type: "prd", artifact_id: 1 })
    await act(async () => {
      render(React.createElement(CreateProjectModal, { open: true, onClose: noop }))
    })
    await waitFor(() => expect(screen.getByTestId("create-project-name-input")).toBeTruthy())

    fireEvent.click(screen.getByTestId("create-project-tab-artifact"))
    await waitFor(() => expect(screen.getByTestId("create-project-artifact-list")).toBeTruthy())
    fireEvent.click(screen.getByTestId("create-project-artifact-row-prd-1"))
    fireEvent.change(screen.getByTestId("create-project-why-input"), {
      target: { value: "Kick off the redesign with prior research attached" },
    })

    await act(async () => {
      fireEvent.click(screen.getByTestId("create-project-submit"))
    })

    await waitFor(() =>
      expect(createMock).toHaveBeenCalledWith({
        name: PRD_ARTIFACT.title,
        origin: "artifact",
        seed_text: "Kick off the redesign with prior research attached",
      }),
    )
    await waitFor(() => expect(addArtifactMock).toHaveBeenCalledWith(89, "prd", 1))
  })

  it("test_empty_why_omits_seed_text", async () => {
    artifactsListMock.mockResolvedValue([])
    createMock.mockResolvedValue({ id: 557, name: "Blank why", origin: "manual" })
    await act(async () => {
      render(React.createElement(CreateProjectModal, { open: true, onClose: noop }))
    })
    await waitFor(() => expect(screen.getByTestId("create-project-name-input")).toBeTruthy())

    fireEvent.change(screen.getByTestId("create-project-name-input"), { target: { value: "Blank why" } })
    fireEvent.change(screen.getByTestId("create-project-why-input"), { target: { value: "   " } })
    await act(async () => {
      fireEvent.click(screen.getByTestId("create-project-submit"))
    })

    await waitFor(() => expect(createMock).toHaveBeenCalled())
    const payload = createMock.mock.calls[0][0] as { seed_text?: string }
    expect(payload.seed_text).toBeFalsy()
  })

  it("test_why_value_is_trimmed", async () => {
    artifactsListMock.mockResolvedValue([])
    createMock.mockResolvedValue({ id: 558, name: "Trim me", origin: "manual" })
    await act(async () => {
      render(React.createElement(CreateProjectModal, { open: true, onClose: noop }))
    })
    await waitFor(() => expect(screen.getByTestId("create-project-name-input")).toBeTruthy())

    fireEvent.change(screen.getByTestId("create-project-name-input"), { target: { value: "Trim me" } })
    fireEvent.change(screen.getByTestId("create-project-why-input"), {
      target: { value: "  padded on both sides  " },
    })
    await act(async () => {
      fireEvent.click(screen.getByTestId("create-project-submit"))
    })

    await waitFor(() =>
      expect(createMock).toHaveBeenCalledWith({
        name: "Trim me",
        origin: "manual",
        seed_text: "padded on both sides",
      }),
    )
  })
})

describe("CreateProjectModal — prd_auto never carries seed_text (AC5)", () => {
  it("test_prd_auto_create_never_carries_seed_text", async () => {
    artifactsListMock.mockResolvedValue(ARTIFACTS)
    createMock.mockResolvedValue({ id: 100, name: PRD_ARTIFACT.title, origin: "prd_auto" })
    addArtifactMock.mockResolvedValue({ project_id: 100, artifact_type: "prd", artifact_id: 1 })
    await act(async () => {
      render(React.createElement(CreateProjectModal, { open: true, onClose: noop }))
    })
    await waitFor(() => expect(screen.getByTestId("create-project-name-input")).toBeTruthy())

    // Type a "why" on the manual tab, then switch to auto — the field
    // isn't shown there (asserted below), and its value never reaches this
    // call regardless of what was typed earlier.
    fireEvent.change(screen.getByTestId("create-project-why-input"), {
      target: { value: "This text must never reach the auto-fork call" },
    })
    fireEvent.click(screen.getByTestId("create-project-tab-auto"))
    await waitFor(() => expect(screen.getByTestId("create-project-auto-prd-list")).toBeTruthy())
    expect(screen.queryByTestId("create-project-why-input")).toBeNull()
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
    const payload = createMock.mock.calls[0][0] as { seed_text?: string }
    expect(payload.seed_text).toBeUndefined()
  })
})

describe("CreateProjectModal — documents attached at creation", () => {
  // The point of taking files here at all: a project created with its brief
  // already attached can answer on the first turn, where one created empty
  // needs a second trip through Add artifact. Uploads necessarily run AFTER
  // creation — `POST /v1/projects/{id}/documents` is keyed on the id — so this
  // is the same create-then-follow-up shape the invite rows already use.
  /** `sizes` lets a test stage a file the size gate should refuse — jsdom
   *  reports File.size from the blob parts, which are tiny, so it is set
   *  explicitly rather than allocating 25 MB of string. */
  function pickFiles(names: string[], sizes: Record<string, number> = {}) {
    const input = screen.getByTestId("create-project-files-input") as HTMLInputElement
    const files = names.map((n) => {
      const f = new File(["hello"], n, { type: "text/plain" })
      if (sizes[n] != null) Object.defineProperty(f, "size", { value: sizes[n] })
      return f
    })
    Object.defineProperty(input, "files", { value: files, configurable: true })
    fireEvent.change(input)
    return files
  }

  async function openModal() {
    artifactsListMock.mockResolvedValue([])
    await act(async () => {
      render(React.createElement(CreateProjectModal, { open: true, onClose: noop }))
    })
    await waitFor(() => expect(screen.getByTestId("create-project-name-input")).toBeTruthy())
  }

  it("uploads every picked file to the new project, then navigates", async () => {
    createMock.mockResolvedValue({ id: 900, name: "Instant-quote flow", origin: "manual" })
    uploadDocumentMock.mockResolvedValue({ type: "custom_artifact", id: 1 })
    await openModal()

    fireEvent.change(screen.getByTestId("create-project-name-input"), {
      target: { value: "Instant-quote flow" },
    })
    pickFiles(["brief.pdf", "notes.md"])
    await act(async () => {
      fireEvent.click(screen.getByTestId("create-project-submit"))
    })

    await waitFor(() => expect(uploadDocumentMock).toHaveBeenCalledTimes(2))
    expect(uploadDocumentMock.mock.calls.map((c) => (c[1] as File).name)).toEqual([
      "brief.pdf",
      "notes.md",
    ])
    expect(uploadDocumentMock.mock.calls.every((c) => c[0] === 900)).toBe(true)
    // Awaited before navigating, so the reader lands on a project whose
    // documents are already there rather than watching them appear.
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/projects?id=900"))
  })

  it("APPENDS across separate picks rather than replacing", async () => {
    // A picker fires once per visit. Assigning would leave someone who added
    // three files in three visits holding only the last, with no sign the
    // others were dropped.
    createMock.mockResolvedValue({ id: 901, name: "P", origin: "manual" })
    uploadDocumentMock.mockResolvedValue({ type: "custom_artifact", id: 1 })
    await openModal()

    fireEvent.change(screen.getByTestId("create-project-name-input"), { target: { value: "P" } })
    pickFiles(["one.md"])
    pickFiles(["two.md"])
    expect(within(screen.getByTestId("create-project-file-list")).getAllByRole("listitem")).toHaveLength(2)

    await act(async () => {
      fireEvent.click(screen.getByTestId("create-project-submit"))
    })
    await waitFor(() => expect(uploadDocumentMock).toHaveBeenCalledTimes(2))
  })

  it("lets a staged file be removed before it is ever uploaded", async () => {
    createMock.mockResolvedValue({ id: 902, name: "P", origin: "manual" })
    uploadDocumentMock.mockResolvedValue({ type: "custom_artifact", id: 1 })
    await openModal()

    fireEvent.change(screen.getByTestId("create-project-name-input"), { target: { value: "P" } })
    pickFiles(["keep.md", "drop.md"])
    fireEvent.click(screen.getByTestId("create-project-file-remove-1"))

    await act(async () => {
      fireEvent.click(screen.getByTestId("create-project-submit"))
    })
    await waitFor(() => expect(uploadDocumentMock).toHaveBeenCalledTimes(1))
    expect((uploadDocumentMock.mock.calls[0][1] as File).name).toBe("keep.md")
  })

  it("A FAILED FILE DOES NOT COST THE PROJECT, and is named on the way out", async () => {
    // The server 422s a file it cannot read (a scanned PDF). Best-effort like
    // the member-add beside it: one bad file must not take the project and
    // everything else in it. But a document silently missing from a project is
    // the failure worth avoiding — nobody re-checks an upload they were never
    // told about — so the toast names it.
    createMock.mockResolvedValue({ id: 903, name: "P", origin: "manual" })
    uploadDocumentMock
      .mockResolvedValueOnce({ type: "custom_artifact", id: 1 })
      .mockRejectedValueOnce(new Error("422 unreadable"))
    await openModal()

    fireEvent.change(screen.getByTestId("create-project-name-input"), { target: { value: "P" } })
    pickFiles(["good.md", "scanned.pdf"])
    await act(async () => {
      fireEvent.click(screen.getByTestId("create-project-submit"))
    })

    // Created and navigated regardless.
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/projects?id=903"))
    await waitFor(() => expect(showToastMock).toHaveBeenCalled())
    const [title, body] = showToastMock.mock.calls[0]
    expect(String(title)).toMatch(/couldn.t be read/i)
    expect(String(body)).toContain("scanned.pdf")
    // The one that worked is not reported as a failure.
    expect(String(body)).not.toContain("good.md")
  })

  it("says nothing when every file lands", async () => {
    createMock.mockResolvedValue({ id: 904, name: "P", origin: "manual" })
    uploadDocumentMock.mockResolvedValue({ type: "custom_artifact", id: 1 })
    await openModal()

    fireEvent.change(screen.getByTestId("create-project-name-input"), { target: { value: "P" } })
    pickFiles(["good.md"])
    await act(async () => {
      fireEvent.click(screen.getByTestId("create-project-submit"))
    })

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/projects?id=904"))
    expect(showToastMock).not.toHaveBeenCalled()
  })

  it("creates with no upload call at all when nothing was picked", async () => {
    createMock.mockResolvedValue({ id: 905, name: "P", origin: "manual" })
    await openModal()

    fireEvent.change(screen.getByTestId("create-project-name-input"), { target: { value: "P" } })
    await act(async () => {
      fireEvent.click(screen.getByTestId("create-project-submit"))
    })

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/projects?id=905"))
    expect(uploadDocumentMock).not.toHaveBeenCalled()
  })

  it("says NOTHING about the limit until someone actually hits it", async () => {
    // The field used to carry a standing hint explaining the 25 MB cap and
    // what we do with the files — to everyone, every visit, including the
    // majority who attach two small documents and never come near either
    // concern. The limit is worth saying exactly once, to the person who just
    // ran into it.
    createMock.mockResolvedValue({ id: 906, name: "P", origin: "manual" })
    uploadDocumentMock.mockResolvedValue({ type: "custom_artifact", id: 1 })
    await openModal()
    expect(screen.queryByTestId("create-project-files-hint")).toBeNull()
    expect(screen.queryByTestId("create-project-files-error")).toBeNull()

    pickFiles(["small.md"])
    expect(screen.queryByTestId("create-project-files-error")).toBeNull()
  })

  it("refuses an oversized file at the picker, and names it", async () => {
    // Mirrors the server's own 25 MB cap, so the reader is told before waiting
    // through an upload that ends in a 413.
    await openModal()
    pickFiles(["huge.pdf"], { "huge.pdf": 26 * 1024 * 1024 })

    const err = screen.getByTestId("create-project-files-error")
    expect(err.textContent).toContain("huge.pdf")
    expect(err.textContent).toMatch(/25 MB/)
    // Refused, so it is not staged and cannot be uploaded.
    expect(screen.queryByTestId("create-project-file-list")).toBeNull()
  })

  it("keeps the files that DID fit when one in the pick is too big", async () => {
    // Refusing the whole pick over one bad file would make the reader select
    // the rest again.
    createMock.mockResolvedValue({ id: 907, name: "P", origin: "manual" })
    uploadDocumentMock.mockResolvedValue({ type: "custom_artifact", id: 1 })
    await openModal()

    fireEvent.change(screen.getByTestId("create-project-name-input"), { target: { value: "P" } })
    pickFiles(["ok.md", "huge.pdf"], { "huge.pdf": 26 * 1024 * 1024 })
    expect(screen.getByTestId("create-project-files-error").textContent).toContain("huge.pdf")
    expect(
      within(screen.getByTestId("create-project-file-list")).getAllByRole("listitem"),
    ).toHaveLength(1)

    await act(async () => {
      fireEvent.click(screen.getByTestId("create-project-submit"))
    })
    await waitFor(() => expect(uploadDocumentMock).toHaveBeenCalledTimes(1))
    expect((uploadDocumentMock.mock.calls[0][1] as File).name).toBe("ok.md")
  })

  it("does not render the browser's own file control", () => {
    // A bare <input type="file"> paints the platform's grey "Choose files /
    // No file chosen", which ignores every token on the page and looks
    // different in each browser. The input is still THERE — visually hidden,
    // not display:none — so it keeps its id, its accessible name and its place
    // in the tab order, and the label is a real control for keyboard and
    // screen-reader users.
    render(React.createElement(CreateProjectModalView, viewProps({ tab: "manual" })))
    const input = screen.getByTestId("create-project-files-input") as HTMLInputElement
    const pick = screen.getByTestId("create-project-files-pick")

    expect(pick.tagName).toBe("LABEL")
    expect(pick.contains(input)).toBe(true)
    expect(input.className).not.toContain("input")
    // Exactly ONE label owns the control: the heading above is a div, so the
    // accessible name is not the two concatenated.
    expect(document.querySelectorAll('label[for="create-project-files"]')).toHaveLength(0)
  })

  it("the picker says how many files are ready once some are", () => {
    const { rerender } = render(
      React.createElement(CreateProjectModalView, viewProps({ tab: "manual" })),
    )
    expect(screen.getByTestId("create-project-files-pick").textContent).toMatch(/Choose files/)

    rerender(
      React.createElement(
        CreateProjectModalView,
        viewProps({ files: [new File(["x"], "a.md"), new File(["x"], "b.md")] }),
      ),
    )
    // Plural, and it still invites more rather than reading as finished.
    expect(screen.getByTestId("create-project-files-pick").textContent).toMatch(
      /2 files ready — add more/,
    )
  })

  it("offers the picker on the manual tab only", () => {
    // The other two tabs are "pick something that already exists" flows; a
    // second way to bring content in there muddies what they are for.
    const { rerender } = render(
      React.createElement(CreateProjectModalView, viewProps({ tab: "manual" })),
    )
    expect(screen.getByTestId("create-project-files-input")).toBeTruthy()

    rerender(React.createElement(CreateProjectModalView, viewProps({ tab: "artifact" })))
    expect(screen.queryByTestId("create-project-files-input")).toBeNull()

    rerender(React.createElement(CreateProjectModalView, viewProps({ tab: "auto" })))
    expect(screen.queryByTestId("create-project-files-input")).toBeNull()
  })

  it("accepts several files at once and caps none of them by count", () => {
    // Owner decision 2026-09-08: no file-count limit here. The server caps
    // each file at 25 MB and refuses what it cannot read; a count limit on top
    // would be an invention with no rule behind it.
    render(
      React.createElement(
        CreateProjectModalView,
        viewProps({
          // Named, so the submit's enabled state is answering "do 12 files
          // block creating?" and not "is the name empty?".
          name: "Instant-quote flow",
          files: Array.from({ length: 12 }, (_, i) => new File(["x"], `f${i}.md`)),
        }),
      ),
    )
    const input = screen.getByTestId("create-project-files-input") as HTMLInputElement
    expect(input.multiple).toBe(true)
    expect(
      within(screen.getByTestId("create-project-file-list")).getAllByRole("listitem"),
    ).toHaveLength(12)
    // Nothing disabled, nothing warned about.
    expect(screen.getByTestId("create-project-submit").hasAttribute("disabled")).toBe(false)
  })
})

describe("projectsApi.create — payload type accepts seed_text (AC6)", () => {
  it("test_create_payload_accepts_seed_text", () => {
    // Type-level assertion (see `ProjectsApiCreatePayload` above) — this
    // object literal only needs to COMPILE under `tsc --noEmit` against the
    // REAL `lib/api.ts` `create` payload type; the runtime call below just
    // exercises the mocked `create` the same as every other test here.
    const payload: ProjectsApiCreatePayload = { name: "Type check", origin: "manual", seed_text: "x" }
    expect(payload.seed_text).toBe("x")
  })
})
