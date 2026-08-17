// @vitest-environment jsdom
//
// The shared `ChatComposerController` unit suite. Covers the pure
// send-command core (`buildSendCommand`: uuid mint, scope stamp, pinned-skill
// splice, attachment extract/upload → `AttachmentRef[]`, best-effort upload) and
// the project-surface controller hook's feature state (file-select append,
// submit-clears, gated-surface member omission). The real mention/drag-drop/
// streaming/real-LLM behaviours are the ship-gate + browser lanes, not this
// jsdom suite.
import * as React from "react"
import { act, cleanup, render, renderHook, screen, waitFor, fireEvent } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

vi.mock("../../../lib/api", () => ({
  askApi: {
    extractFile: vi.fn(),
    skills: vi.fn().mockResolvedValue({ skills: [] }),
  },
  attachmentsApi: {
    upload: vi.fn(),
  },
}))

import { askApi, attachmentsApi } from "../../../lib/api"
import {
  buildSendCommand,
  resolveAttachmentRefs,
  spliceSkill,
  useChatComposerController,
} from "../chatComposerController"
import type { SendCommand } from "../chat-shell/types"

const extractFileMock = askApi.extractFile as unknown as Mock
const uploadMock = attachmentsApi.upload as unknown as Mock
const skillsMock = askApi.skills as unknown as Mock

const scope = { surface: "project_private", projectId: 7 } as const
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

beforeEach(() => {
  extractFileMock.mockReset()
  uploadMock.mockReset()
  skillsMock.mockReset()
  skillsMock.mockResolvedValue({ skills: [] })
  extractFileMock.mockResolvedValue({ name: "deck.pdf", markdown: "PARSED_MARKDOWN" })
  uploadMock.mockResolvedValue({ key: "K1", name: "deck.pdf", mime: "application/pdf", size: 123 })
})
afterEach(() => cleanup())

// ── Creation / send-command construction ─────────────────────────────────────

describe("buildSendCommand — construction", () => {
  it("test_build_send_command_mints_unique_client_message_id (AC2)", async () => {
    const a = await buildSendCommand({ text: "hello there", scope })
    const b = await buildSendCommand({ text: "hello there", scope })
    expect(a.clientMessageId).toMatch(UUID_RE)
    expect(b.clientMessageId).toMatch(UUID_RE)
    expect(a.clientMessageId).not.toBe(b.clientMessageId)
  })

  it("test_build_send_command_stamps_scope (AC2)", async () => {
    const cmd = await buildSendCommand({ text: "hello there", scope: { surface: "project_group", projectId: 42 } })
    expect(cmd.scope).toEqual({ surface: "project_group", projectId: 42 })
  })

  it("test_build_send_command_splices_pinned_skill (AC2)", async () => {
    const withSkill = await buildSendCommand({
      text: "compare us to Acme",
      pinnedSkill: { id: "s1", label: "Compete", trigger: "/compete" },
      scope,
    })
    // Byte-for-byte the ChatScreen:5755 rule.
    expect(withSkill.riddenText).toBe("/compete compare us to Acme")
    expect(spliceSkill({ trigger: "/compete" }, "compare us to Acme")).toBe("/compete compare us to Acme")

    const without = await buildSendCommand({ text: "compare us to Acme", scope })
    expect(without.riddenText).toBe("compare us to Acme")
    expect(spliceSkill(null, "compare us to Acme")).toBe("compare us to Acme")
  })
})

// ── Attachments ──────────────────────────────────────────────────────────────

describe("buildSendCommand — attachments", () => {
  it("test_build_send_command_inlines_text_attachment (AC3)", async () => {
    const cmd = await buildSendCommand({
      text: "q",
      attachments: [{ name: "notes.txt", content: "inline text body" }],
      scope,
    })
    expect(extractFileMock).not.toHaveBeenCalled()
    expect(cmd.attachments?.[0].content).toBe("inline text body")
  })

  it("test_build_send_command_extracts_document_once (AC3)", async () => {
    const file = new File(["binary"], "deck.pdf", { type: "application/pdf" })
    const cmd = await buildSendCommand({
      text: "q",
      attachments: [{ name: "deck.pdf", content: "", file }],
      scope,
    })
    expect(extractFileMock).toHaveBeenCalledTimes(1)
    expect(cmd.attachments?.[0].content).toBe("PARSED_MARKDOWN")
  })

  it("test_build_send_command_uploads_and_sets_key (AC3)", async () => {
    const f1 = new File(["a"], "a.pdf", { type: "application/pdf" })
    const f2 = new File(["b"], "b.pdf", { type: "application/pdf" })
    uploadMock.mockImplementation(async (file: File) => ({ key: `K-${file.name}`, name: file.name, mime: "application/pdf", size: 1 }))
    const cmd = await buildSendCommand({
      text: "q",
      attachments: [
        { name: "a.pdf", content: "", file: f1 },
        { name: "b.pdf", content: "", file: f2 },
      ],
      scope,
    })
    expect(uploadMock).toHaveBeenCalledTimes(2)
    // Order preserved.
    expect(cmd.attachments?.map((a) => a.name)).toEqual(["a.pdf", "b.pdf"])
    expect(cmd.attachments?.[0].key).toBe("K-a.pdf")
    expect(cmd.attachments?.[1].key).toBe("K-b.pdf")
    expect(cmd.attachments?.[0].mime).toBe("application/pdf")
    expect(cmd.attachments?.[0].size).toBe(1)
  })

  it("test_build_send_command_upload_failure_is_best_effort (AC4)", async () => {
    uploadMock.mockRejectedValue(new Error("no storage backend"))
    const file = new File(["x"], "d.pdf", { type: "application/pdf" })
    const refs = await resolveAttachmentRefs([{ name: "d.pdf", content: "", file }])
    expect(refs[0].key).toBeNull()
    // Text preserved (extraction still ran), no throw.
    expect(refs[0].content).toBe("PARSED_MARKDOWN")
  })
})

// ── Controller state ─────────────────────────────────────────────────────────

describe("useChatComposerController — state", () => {
  it("test_controller_file_select_appends_attachment (AC5)", async () => {
    const { result } = renderHook(() =>
      useChatComposerController({ scope, onCommand: vi.fn(), attachmentsEnabled: true, skillsEnabled: true }),
    )
    const file = new File(["hello file body"], "report.txt", { type: "text/plain" })
    act(() => {
      result.current.features!.onFileSelect({
        target: { files: [file], value: "" },
      } as unknown as React.ChangeEvent<HTMLInputElement>)
    })
    await waitFor(() => expect(result.current.features!.attachments).toHaveLength(1))
    expect(result.current.features!.attachments[0].name).toBe("report.txt")
  })

  it("test_controller_submit_clears_pinned_and_attachments (AC5/AC6)", async () => {
    const onCommand = vi.fn()
    skillsMock.mockResolvedValue({
      skills: [{ id: "s1", label: "Compete", trigger: "/compete", description: "x", category: "Custom" }],
    })
    const { result } = renderHook(() =>
      useChatComposerController({ scope, onCommand, attachmentsEnabled: true, skillsEnabled: true }),
    )

    // Add a (text) attachment.
    const file = new File(["body"], "a.txt", { type: "text/plain" })
    act(() => {
      result.current.features!.onFileSelect({ target: { files: [file], value: "" } } as unknown as React.ChangeEvent<HTMLInputElement>)
    })
    await waitFor(() => expect(result.current.features!.attachments).toHaveLength(1))

    // Pin a skill through the real palette: open it, then click the option.
    await waitFor(() => expect(skillsMock).toHaveBeenCalled())
    act(() => result.current.features!.onMenuSelect(1))
    const { container } = render(<>{result.current.slashMenu}</>)
    const option = container.querySelector<HTMLButtonElement>(".chat-slash-item")
    expect(option).toBeTruthy()
    act(() => { fireEvent.mouseDown(option!) })
    await waitFor(() => expect(result.current.features!.pinnedSkill).not.toBeNull())

    // Submit.
    act(() => result.current.submit("write the brief"))
    await waitFor(() => expect(onCommand).toHaveBeenCalledTimes(1))
    const cmd = onCommand.mock.calls[0][0] as SendCommand
    expect(cmd.text).toBe("write the brief")
    expect(cmd.pinnedSkill?.trigger).toBe("/compete")
    expect(cmd.attachments).toHaveLength(1)

    // State cleared after send.
    expect(result.current.features!.pinnedSkill).toBeNull()
    expect(result.current.features!.attachments).toHaveLength(0)
  })

  it("test_controller_gated_surface_omits_members (AC7)", () => {
    const { result } = renderHook(() =>
      useChatComposerController({ scope: { surface: "project_group", projectId: 7 }, onCommand: vi.fn(), attachmentsEnabled: false, skillsEnabled: false }),
    )
    // A fully gated surface (the group chat, no live attachments/skills yet)
    // exposes NO features bag — no
    // attachment/skill members, so the shell keeps its inert defaults.
    expect(result.current.features).toBeUndefined()
  })
})

// ── Typed-`/` palette (AC16) ─────────────────────────────────────────────────

describe("useChatComposerController — typed-slash palette (AC16)", () => {
  const compSkill = { id: "s1", label: "Competitive intel", trigger: "/competitive", description: "compare us", category: "Custom" }
  const draftSkill = { id: "s2", label: "Draft report", trigger: "/draft", description: "write a report", category: "Custom" }

  it("test_typed_slash_opens_and_filters_palette", async () => {
    skillsMock.mockResolvedValue({ skills: [compSkill, draftSkill] })
    const { result } = renderHook(() =>
      useChatComposerController({ scope, onCommand: vi.fn(), attachmentsEnabled: true, skillsEnabled: true }),
    )
    await waitFor(() => expect(skillsMock).toHaveBeenCalled())
    // A leading `/` opens the palette AND filters it by the text after `/`.
    act(() => result.current.onInput("/comp"))
    const { container } = render(<>{result.current.slashMenu}</>)
    const items = container.querySelectorAll(".chat-slash-item")
    expect(items).toHaveLength(1)
    expect(container.textContent).toContain("Competitive intel")
    expect(container.textContent).not.toContain("Draft report")
  })

  it("test_non_slash_input_closes_palette", async () => {
    skillsMock.mockResolvedValue({ skills: [compSkill] })
    const { result } = renderHook(() =>
      useChatComposerController({ scope, onCommand: vi.fn(), attachmentsEnabled: true, skillsEnabled: true }),
    )
    await waitFor(() => expect(skillsMock).toHaveBeenCalled())
    act(() => result.current.onInput("/comp"))
    expect(result.current.slashMenu).not.toBeNull()
    // Deleting back below the leading `/` closes it.
    act(() => result.current.onInput("hello"))
    expect(result.current.slashMenu).toBeNull()
  })

  it("test_plus_menu_browse_skills_still_opens_same_palette", async () => {
    skillsMock.mockResolvedValue({ skills: [compSkill, draftSkill] })
    const { result } = renderHook(() =>
      useChatComposerController({ scope, onCommand: vi.fn(), attachmentsEnabled: true, skillsEnabled: true }),
    )
    await waitFor(() => expect(skillsMock).toHaveBeenCalled())
    // The `+`-menu Browse-skills path (index 1) opens the SAME palette,
    // unfiltered — no second palette or slash-detection path exists.
    act(() => result.current.features!.onMenuSelect(1))
    const { container } = render(<>{result.current.slashMenu}</>)
    expect(container.querySelectorAll(".chat-slash-item")).toHaveLength(2)
  })

  it("test_typed_slash_no_op_when_skills_disabled", () => {
    const { result } = renderHook(() =>
      useChatComposerController({ scope, onCommand: vi.fn(), attachmentsEnabled: true, skillsEnabled: false }),
    )
    // A skills-disabled surface's onInput is a harmless no-op (no palette).
    act(() => result.current.onInput("/comp"))
    expect(result.current.slashMenu).toBeNull()
  })
})
