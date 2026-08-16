// @vitest-environment jsdom
//
// The un-stubbed project composer (`composer.features`) + the FE agent run-status
// consume (the honest failure UI). These extend the shell suite WITHOUT touching
// the byte-identical `ChatScreen.shell-golden.dom.test.tsx` or the frozen
// `ChatShell.unit`/`ChatShell.module-graph` gates. The shell reads the additive
// `composer.features` bag; when it is absent the composer is byte-identically
// inert (the ledgered opt-out). The run-status node renders the host's
// `reply.runStatus` closure off the last turn's frozen `ShellTurn.runStatus`.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({ matches: false, media: query, onchange: null, addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false }) as unknown as MediaQueryList
}

import { ChatShell } from "../ChatShell"
import { renderRunStatus, type ChatComposerFeatures } from "../../chatComposerController"
import type { ChatSurfaceDescriptor, ShellTurn } from "../types"
import type { AgentRunStatus } from "../types"

const AGENT = "Sprntly"

function projectDescriptor(over: Partial<ChatSurfaceDescriptor> = {}): ChatSurfaceDescriptor {
  return {
    surface: "project_private",
    projectId: 7,
    testIdPrefix: "ic",
    frame: { mode: "thread", viewportClassName: "od-standalone", ...(over.frame ?? {}) },
    transcript: { agentName: AGENT, ...(over.transcript ?? {}) },
    composer: { busyMode: "block-while-asking", ...(over.composer ?? {}) },
    reply: { mode: "streamed", ...(over.reply ?? {}) },
    send: { onSubmit: () => {}, pendingSendBubble: true, ...(over.send ?? {}) },
    ...over,
  }
}

/** A full features bag with spies; overrides merge shallowly. */
function features(over: Partial<ChatComposerFeatures> = {}): ChatComposerFeatures {
  return {
    pinnedSkill: null,
    onRemoveSkill: vi.fn(),
    attachments: [],
    onFileSelect: vi.fn(),
    onRemoveAttachment: vi.fn(),
    menuOpen: false,
    menuActiveIndex: 0,
    onToggleMenu: vi.fn(),
    onMenuActive: vi.fn(),
    onMenuSelect: vi.fn(),
    onCloseMenu: vi.fn(),
    ...over,
  }
}

afterEach(() => cleanup())

// ── Composer wiring (project surface) ────────────────────────────────────────

describe("ChatShell composer features", () => {
  it("test_shell_wires_file_select_and_renders_chip (AC5)", () => {
    // A controlled features bag: onFileSelect appends, so the shell re-renders a
    // chip from the updated `features.attachments`.
    function Harness() {
      const [atts, setAtts] = React.useState<{ name: string }[]>([])
      const f = features({
        attachments: atts,
        onFileSelect: () => setAtts((p) => [...p, { name: "report.txt" }]),
      })
      return <ChatShell descriptor={projectDescriptor({ composer: { busyMode: "block-while-asking", features: f } })} turns={[]} />
    }
    const { container } = render(<Harness />)
    expect(screen.queryByTestId("attachment-chip")).toBeNull()
    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement
    act(() => { fireEvent.change(fileInput, { target: { files: [] } }) })
    const chip = screen.getByTestId("attachment-chip")
    expect(chip.textContent).toContain("report.txt")
  })

  it("test_shell_renders_pinned_skill_chip (AC5)", () => {
    const onRemoveSkill = vi.fn()
    const f = features({ pinnedSkill: { id: "s1", label: "Compete", trigger: "/compete" }, onRemoveSkill })
    render(<ChatShell descriptor={projectDescriptor({ composer: { busyMode: "block-while-asking", features: f } })} turns={[]} />)
    const chip = screen.getByTestId("skill-chip")
    expect(chip.textContent).toContain("Compete")
    fireEvent.click(screen.getByLabelText("Remove the Compete skill"))
    expect(onRemoveSkill).toHaveBeenCalledTimes(1)
  })

  it("test_shell_plus_menu_toggles_when_features_present (AC5)", () => {
    const onToggleMenu = vi.fn()
    // menuOpen=true so the shell renders the menu from `features.menuOpen`.
    const f = features({ menuOpen: true, onToggleMenu })
    render(<ChatShell descriptor={projectDescriptor({ composer: { busyMode: "block-while-asking", features: f } })} turns={[]} />)
    // The `+` fires the wired handler.
    fireEvent.click(screen.getByLabelText("Add attachment or skill"))
    expect(onToggleMenu).toHaveBeenCalledTimes(1)
    // menuOpen drives the open menu.
    expect(screen.getByRole("menu")).toBeTruthy()
    expect(screen.getByText("Browse skills")).toBeTruthy()
  })

  it("test_shell_composer_inert_when_features_absent (AC5)", () => {
    const onSubmit = vi.fn()
    // No `features` bag → today's inert defaults.
    const { container } = render(<ChatShell descriptor={projectDescriptor({ send: { onSubmit } })} turns={[]} />)
    expect(screen.queryByTestId("attachment-chip")).toBeNull()
    expect(screen.queryByTestId("skill-chip")).toBeNull()
    // The `+` is inert (no menu opens, no throw).
    fireEvent.click(screen.getByLabelText("Add attachment or skill"))
    expect(screen.queryByRole("menu")).toBeNull()
  })
})

// ── Run-status consume (the honest failure UI) ───────────────────────────────

/** Drive the shell's `reply.runStatus` off the last turn's frozen runStatus. */
function renderWithRunStatus(status: AgentRunStatus | null, retryRun?: (t: ShellTurn | null) => void) {
  const turn: ShellTurn = { id: "t1", author: { kind: "self", name: "Ada" }, content: "do it", runStatus: status ?? undefined }
  const descriptor = projectDescriptor({
    surface: "project_group",
    testIdPrefix: "gc",
    transcript: { agentName: AGENT, multiParty: true, renderUserBody: (t) => <div>{t.content}</div> },
    composer: { busyMode: "never-block" },
    reply: {
      mode: "backgrounded",
      runStatus: (s, t) => renderRunStatus({ status: s, turn: t, prefix: "gc", retryRun }),
    },
  })
  return render(<ChatShell descriptor={descriptor} turns={[turn]} />)
}

describe("ChatShell run-status consume", () => {
  it("test_run_status_failed_renders_error_and_retry (AC8)", () => {
    const retryRun = vi.fn()
    renderWithRunStatus("failed", retryRun)
    expect(screen.getByTestId("gc-run-failed")).toBeTruthy()
    fireEvent.click(screen.getByTestId("gc-run-retry"))
    expect(retryRun).toHaveBeenCalledTimes(1)
  })

  it("test_run_status_failed_no_retry_without_handler (AC8)", () => {
    renderWithRunStatus("failed", undefined)
    expect(screen.getByTestId("gc-run-failed")).toBeTruthy()
    expect(screen.queryByTestId("gc-run-retry")).toBeNull()
  })

  it("test_run_status_running_renders_pending (AC8)", () => {
    renderWithRunStatus("running")
    expect(screen.getByTestId("gc-run-pending")).toBeTruthy()
    expect(screen.queryByTestId("gc-run-failed")).toBeNull()
  })

  it("test_run_status_declined_renders_quiet_stayout (AC8/AC9)", () => {
    renderWithRunStatus("declined")
    // The quiet, honest replacement — distinct from `failed`; the alarming
    // `gc-stayed-out` pill is gone.
    expect(screen.getByTestId("gc-stayed-out-quiet")).toBeTruthy()
    expect(screen.queryByTestId("gc-run-failed")).toBeNull()
    expect(screen.queryByTestId("gc-stayed-out")).toBeNull()
  })

  it("test_run_status_done_and_null_render_nothing (AC8)", () => {
    for (const status of ["done", null] as const) {
      cleanup()
      renderWithRunStatus(status)
      expect(screen.queryByTestId("gc-run-failed")).toBeNull()
      expect(screen.queryByTestId("gc-run-pending")).toBeNull()
      expect(screen.queryByTestId("gc-stayed-out-quiet")).toBeNull()
    }
  })
})

// ── Isolation / guard ────────────────────────────────────────────────────────

describe("ChatShell composer-features isolation", () => {
  it("test_module_graph_gate_still_green (AC5) — controller lives in shared/, not the gated chat-shell/ leaf", async () => {
    // The controller module is imported by the HOSTS, never by the shell itself
    // (the shell reads the `composer.features` prop bag). Assert ChatShell.tsx's
    // own source imports no controller.
    const { readFileSync } = await import("node:fs")
    const { fileURLToPath } = await import("node:url")
    const { dirname, join } = await import("node:path")
    const src = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "..", "ChatShell.tsx"), "utf8")
    expect(src).not.toMatch(/chatComposerController/)
  })
})
