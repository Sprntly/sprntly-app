// @vitest-environment jsdom
//
// ChatShell — the multi-party (group) config surface added in T3a. These extend
// the shell's unit coverage WITHOUT touching T1's byte-identical
// `ChatShell.unit.dom.test.tsx` or T2's `ChatShell.project-private.dom.test.tsx`
// (both re-run green unmodified). Covers: full group-row fidelity, `never-block`
// (send fires while a turn is pending, via Enter AND click), the
// `ComposerDraftApi` method facade + `onDraftApiReady` handoff, `onKeyDownCapture`
// consume semantics, the `runStatus` stayed-out arm, `frame.aboveViewport` +
// `frame.loading` skeleton-replaces-transcript, `dock.aboveComposer` placement,
// `scrollToTurn` `data-turn-id` anchors, the frozen contract-only seams, and the
// main no-op re-proof.
import * as React from "react"
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({ matches: false, media: query, onchange: null, addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false }) as unknown as MediaQueryList
}

import { ChatShell } from "../ChatShell"
import type { ChatShellHandle, ChatSurfaceDescriptor, ComposerDraftApi, ShellTurn } from "../types"
import type { ChatTranscriptTurn } from "../../ChatTranscript"
import type { AskResponse } from "../../../../lib/api"

const AGENT = "Sprntly"
const T = 1_700_000_000_000
const reply = (answer: string): AskResponse => ({ answer, key_points: [], citations: [], confidence: 1, unanswered: "" }) as AskResponse

/** A complete surface:"project_group" descriptor; overrides merge shallowly. */
function groupDescriptor(over: Partial<ChatSurfaceDescriptor> = {}): ChatSurfaceDescriptor {
  return {
    surface: "project_group",
    projectId: 7,
    testIdPrefix: "gc",
    frame: { mode: "thread", viewportClassName: "od-standalone", ...(over.frame ?? {}) },
    transcript: {
      agentName: AGENT,
      agentBadge: "AGENT",
      multiParty: true,
      timestamps: "fromTurn",
      renderUserBody: (t) => <div data-testid="gc-body">{t.content}</div>,
      renderAgentBody: (t) => <div data-testid="gc-agent-body">{t.content}</div>,
      turnHeadExtra: (t) =>
        t.author.kind === "agent" ? (
          <span data-testid="gc-state-badge">{t.invokedBy ? `invoked by ${t.invokedBy}` : "detected this was for it"}</span>
        ) : null,
      ...(over.transcript ?? {}),
    },
    composer: { busyMode: "never-block", ...(over.composer ?? {}) },
    reply: { mode: "backgrounded", ...(over.reply ?? {}) },
    send: { onSubmit: () => {}, pendingSendBubble: false, ...(over.send ?? {}) },
    ...over,
  }
}

const selfTurn: ShellTurn = { id: "1", author: { kind: "self", name: "Ada", initials: "A", avatarStyle: { background: "#eee" } }, content: "hi team", createdAt: T }
const peerTurn: ShellTurn = { id: "2", author: { kind: "peer", name: "Bo", role: "PM", initials: "B", avatarStyle: { background: "#ddd" } }, content: "hello", createdAt: T }
const agentTurn: ShellTurn = { id: "3", author: { kind: "agent", name: AGENT }, content: "on it", createdAt: T, invokedBy: "Bo", invokedByMe: false }

afterEach(() => cleanup())

describe("ChatShell group config", () => {
  it("test_shell_multiParty_renders_full_group_row_fidelity (AC2)", () => {
    const { container } = render(<ChatShell descriptor={groupDescriptor()} turns={[selfTurn, peerTurn, agentTurn]} />)

    // Per-kind wrapper testids.
    const me = screen.getByTestId("gc-msg-me")
    const other = screen.getByTestId("gc-msg-other")
    const agent = screen.getByTestId("gc-msg-agent")

    // Sides: the peer turn is start-aligned (its own layout), me is not.
    expect(other.querySelector('[class*="otherRow"]')).toBeTruthy()

    // Speaker names.
    expect(me.textContent).toContain("You")
    expect(other.textContent).toContain("Bo")

    // Role chip + human timestamp on the peer turn.
    const expectedTime = new Date(T).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })
    expect(other.textContent).toContain("PM")
    expect(other.textContent).toContain(expectedTime)

    // Avatar monogram.
    expect(me.querySelector(".bc-avatar")?.textContent).toBe("A")
    expect(other.querySelector(".bc-avatar")?.textContent).toBe("B")

    // Agent badge + invoked-by badge.
    expect(agent.querySelector(".bc-agent-badge")?.textContent).toContain("AGENT")
    expect(screen.getByTestId("gc-state-badge").textContent).toBe("invoked by Bo")

    // Every mapped turn carries a stable data-turn-id.
    expect(container.querySelectorAll("[data-turn-id]").length).toBe(3)
  })

  it("test_group_other_row_no_undefined_class (AC7)", () => {
    render(<ChatShell descriptor={groupDescriptor()} turns={[selfTurn, peerTurn, agentTurn]} />)
    // Vite's CSS-module test transform hashes ANY property access (even an
    // undefined export), so a jsdom className check alone can't catch a
    // missing rule — assert directly against the CSS source, the same shape
    // production bundling actually enforces.
    const other = screen.getByTestId("gc-msg-other")
    expect(other.className.split(/\s+/)).not.toContain("undefined")
    const shellCssSrc = readFileSync(join(__dirname, "../ChatShell.module.css"), "utf8")
    expect(shellCssSrc).toMatch(/\.gcMsgOther\s*\{/)
  })

  it("test_shell_never_block_sends_with_pending_turn_via_enter_and_click (AC3)", () => {
    const onSubmit = vi.fn()
    // A PENDING turn is fixtured — this makes the test non-vacuous: with a
    // block-derived-from-busy bug the send would be swallowed. `never-block`
    // must let it through on BOTH paths.
    const pending: ShellTurn = { id: "9", author: { kind: "agent", name: AGENT }, content: "", pending: true, createdAt: T }
    const { container } = render(
      <ChatShell descriptor={groupDescriptor({ send: { onSubmit, pendingSendBubble: false } })} turns={[selfTurn, pending]} />,
    )
    const textarea = container.querySelector("textarea")!
    // Send (not a dead Stop) is shown because never-block keeps busy=false.
    expect(screen.queryByLabelText("Stop generating")).toBeNull()
    expect(screen.getByLabelText("Send")).toBeTruthy()

    // Enter path.
    fireEvent.input(textarea, { target: { value: "message via enter" } })
    fireEvent.keyDown(textarea, { key: "Enter" })
    // Click path.
    fireEvent.input(textarea, { target: { value: "message via click" } })
    fireEvent.click(screen.getByLabelText("Send"))

    expect(onSubmit).toHaveBeenCalledTimes(2)
    expect(onSubmit).toHaveBeenNthCalledWith(1, "message via enter")
    expect(onSubmit).toHaveBeenNthCalledWith(2, "message via click")
  })

  it("test_shell_draft_api_facade_reads_live_draft_no_clobber (AC4)", () => {
    let api: ComposerDraftApi | null = null
    const { container } = render(
      <ChatShell
        descriptor={groupDescriptor()}
        turns={[selfTurn]}
        onDraftApiReady={(a) => {
          api = a
        }}
      />,
    )
    // Handed out once on mount.
    expect(api).toBeTruthy()
    const draftApi = api as unknown as ComposerDraftApi
    expect(typeof draftApi.getValue).toBe("function")
    expect(typeof draftApi.setValue).toBe("function")

    const textarea = container.querySelector("textarea")!
    // getValue reflects the LIVE draft (reads the textarea), not a frozen snapshot.
    fireEvent.input(textarea, { target: { value: "typed live" } })
    expect(draftApi.getValue()).toBe("typed live")

    // A failure-restore CAS: since the user typed text, getValue() is non-empty,
    // so a `if (getValue()==="") setValue(...)` restore would DECLINE — no clobber.
    expect(draftApi.getValue()).not.toBe("")

    // setValue writes the draft (chip insertion path).
    fireEvent.input(textarea, { target: { value: "" } })
    act(() => draftApi.setValue("@Sprntly ", 9))
    expect((container.querySelector("textarea") as HTMLTextAreaElement).value).toBe("@Sprntly ")
  })

  it("test_shell_onKeyDownCapture_enter_selects_escape_closes_no_stop (AC9)", () => {
    const onSubmit = vi.fn()
    const onStop = vi.fn()
    // The picker consumes Enter + Escape (returns true) → shell neither submits
    // nor stops. escToStop is OFF for group (no Stop UI), so Escape only routes
    // through the capture.
    const onKeyDownCapture = (e: KeyboardEvent) => {
      if (e.key === "Enter" || e.key === "Escape") {
        e.preventDefault?.()
        return true
      }
      return false
    }
    const { container } = render(
      <ChatShell
        descriptor={groupDescriptor({
          composer: { busyMode: "never-block", escToStop: false, stop: { enabled: false, onStop }, onKeyDownCapture },
          send: { onSubmit, pendingSendBubble: false },
        })}
        turns={[selfTurn]}
      />,
    )
    const textarea = container.querySelector("textarea")!
    fireEvent.input(textarea, { target: { value: "consumed message" } })
    fireEvent.keyDown(textarea, { key: "Enter" })
    // Consumed → NOT submitted.
    expect(onSubmit).not.toHaveBeenCalled()
    fireEvent.keyDown(textarea, { key: "Escape" })
    expect(onStop).not.toHaveBeenCalled()

    // A control: a capture that declines lets Enter submit.
    cleanup()
    const onSubmit2 = vi.fn()
    const { container: c2 } = render(
      <ChatShell
        descriptor={groupDescriptor({
          composer: { busyMode: "never-block", escToStop: false, onKeyDownCapture: () => false },
          send: { onSubmit: onSubmit2, pendingSendBubble: false },
        })}
        turns={[selfTurn]}
      />,
    )
    const ta2 = c2.querySelector("textarea")!
    fireEvent.input(ta2, { target: { value: "not consumed" } })
    fireEvent.keyDown(ta2, { key: "Enter" })
    expect(onSubmit2).toHaveBeenCalledWith("not consumed")
  })

  it("test_shell_runStatus_arms_stayed_out_vs_active_vs_null (AC5)", () => {
    const runStatus = (status: string | null) =>
      status === null ? <div data-testid="stayed-out">no reply yet</div> : <div data-testid="active">{status}</div>

    // Last turn has no runStatus → null arm (stayed out).
    const { rerender } = render(
      <ChatShell descriptor={groupDescriptor({ reply: { mode: "backgrounded", runStatus } })} turns={[selfTurn]} />,
    )
    expect(screen.getByTestId("stayed-out")).toBeTruthy()
    expect(screen.queryByTestId("active")).toBeNull()

    // Last turn carries a real status → active arm.
    rerender(
      <ChatShell
        descriptor={groupDescriptor({ reply: { mode: "backgrounded", runStatus } })}
        turns={[{ ...selfTurn, runStatus: "running" }]}
      />,
    )
    expect(screen.getByTestId("active").textContent).toBe("running")
  })

  it("test_shell_aboveViewport_roster_and_loading_replaces_transcript_autoscroll_ok (AC5)", () => {
    const { rerender, container } = render(
      <ChatShell
        descriptor={groupDescriptor({
          frame: { mode: "thread", aboveViewport: <div data-testid="gc-presence">roster</div>, loading: true, loadingNode: <div data-testid="gc-loading">loading…</div> },
        })}
        turns={[selfTurn, peerTurn]}
      />,
    )
    // Roster renders ABOVE the viewport (before the scroll region in the DOM).
    const roster = screen.getByTestId("gc-presence")
    const main = container.querySelector("main")!
    expect(main.firstChild).toBe(roster)
    // Skeleton REPLACES the transcript — no turn rows while loading.
    expect(screen.getByTestId("gc-loading")).toBeTruthy()
    expect(screen.queryByTestId("gc-msg-me")).toBeNull()

    // Loading done → transcript renders.
    rerender(
      <ChatShell
        descriptor={groupDescriptor({ frame: { mode: "thread", aboveViewport: <div data-testid="gc-presence">roster</div>, loading: false } })}
        turns={[selfTurn, peerTurn]}
      />,
    )
    expect(screen.getByTestId("gc-msg-me")).toBeTruthy()
  })

  it("test_shell_dock_aboveComposer_below_viewport_above_composer (AC6)", () => {
    const { container } = render(
      <ChatShell
        descriptor={groupDescriptor({ dock: { aboveComposer: <div data-testid="gc-dock-extra">typing…</div> } })}
        turns={[selfTurn]}
      />,
    )
    const dockExtra = screen.getByTestId("gc-dock-extra")
    const dock = container.querySelector(".bc-dock")!
    // Rendered in the dock div, OUTSIDE the scroll viewport (so a typing
    // indicator never scrolls out of view).
    expect(dock.contains(dockExtra)).toBe(true)
    const viewport = container.querySelector(".od-standalone")
    expect(viewport?.contains(dockExtra) ?? false).toBe(false)
  })

  it("test_shell_scrollToTurn_resolves_data_turn_id_and_noops_when_missing (AC7)", () => {
    const scrollSpy = vi.fn()
    ;(Element.prototype as unknown as { scrollIntoView: () => void }).scrollIntoView = scrollSpy
    const ref = React.createRef<ChatShellHandle>()
    const { container } = render(
      <ChatShell ref={ref} descriptor={groupDescriptor()} turns={[selfTurn, peerTurn, agentTurn]} />,
    )
    // Every mapped turn has a data-turn-id.
    expect(container.querySelector('[data-turn-id="2"]')).toBeTruthy()
    // Resolves a present id.
    ref.current!.scrollToTurn("2")
    expect(scrollSpy).toHaveBeenCalledTimes(1)
    // No-op-safe for a missing / not-yet-loaded id (never throws).
    expect(() => ref.current!.scrollToTurn("does-not-exist")).not.toThrow()
    expect(scrollSpy).toHaveBeenCalledTimes(1)
  })

  it("test_shell_frozen_seams_inert_unset_fixture_when_set (AC10)", () => {
    // onDraftApiReady + runStatus are WIRED (fixture renders/fires when set).
    let apiReady = false
    render(
      <ChatShell
        descriptor={groupDescriptor({
          reply: { mode: "backgrounded", runStatus: () => <div data-testid="wired-runstatus">x</div> },
          // turnBeforeNode / turnActions are contract-only (wired to nothing) —
          // setting them must NOT render (the freeze that keeps a later wave
          // from reopening the shell).
          transcript: {
            agentName: AGENT,
            multiParty: true,
            timestamps: "fromTurn",
            renderUserBody: (t) => <div>{t.content}</div>,
            turnBeforeNode: () => <div data-testid="frozen-before">before</div>,
            turnActions: () => <div data-testid="frozen-actions">actions</div>,
          },
        })}
        turns={[{ ...selfTurn, parentRef: { turnId: "0", authorName: "x", snippet: "s" }, runId: "r1", runStatus: null }]}
        onDraftApiReady={() => {
          apiReady = true
        }}
      />,
    )
    expect(apiReady).toBe(true)
    expect(screen.getByTestId("wired-runstatus")).toBeTruthy()
    // Contract-only seams stay inert even when set + a parentRef/runId turn.
    expect(screen.queryByTestId("frozen-before")).toBeNull()
    expect(screen.queryByTestId("frozen-actions")).toBeNull()
  })

  it("test_shell_main_path_byte_unchanged_with_group_config_added (AC12)", () => {
    const mainTurn: ChatTranscriptTurn = {
      turnId: "t1",
      user: { name: "Ada", initials: "A", query: "hi" },
      agentName: AGENT,
      agentBadge: "Product Coworker",
      isLast: true,
      reply: reply("hello there"),
    }
    const mainDescriptor = (over: Partial<ChatSurfaceDescriptor> = {}): ChatSurfaceDescriptor => ({
      surface: "main",
      frame: { mode: "thread", viewportClassName: "od-center-scroll" },
      transcript: { agentName: AGENT, agentBadge: "Product Coworker", timestamps: "none", ...(over.transcript ?? {}) },
      composer: { busyMode: "block-while-asking" },
      reply: { mode: "streamed" },
      send: { onSubmit: () => {}, pendingSendBubble: true },
      ...over,
    })
    const without = render(<ChatShell descriptor={mainDescriptor()} turns={[mainTurn]} composerNode={<div data-testid="c" />} />)
    const a = without.container.querySelector("main.od-center")!.outerHTML
    cleanup()
    const withStray = render(
      <ChatShell
        descriptor={mainDescriptor({ projectId: 42, testIdPrefix: "gc", transcript: { agentName: AGENT, agentBadge: "Product Coworker", timestamps: "none", multiParty: true } })}
        turns={[mainTurn]}
        composerNode={<div data-testid="c" />}
        onDraftApiReady={() => {
          throw new Error("main must never construct the draft API")
        }}
      />,
    )
    const b = withStray.container.querySelector("main.od-center")!.outerHTML
    expect(b).toBe(a)
    // No group artifacts leak onto main.
    expect(withStray.container.querySelector('[data-turn-id]')).toBeNull()
    expect(withStray.container.querySelector('[data-testid="gc-msg-me"]')).toBeNull()
  })
})
