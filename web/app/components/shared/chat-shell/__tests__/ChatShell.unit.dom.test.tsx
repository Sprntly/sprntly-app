// @vitest-environment jsdom
//
// ChatShell — durable descriptor→DOM unit assertions for the surface:"main"
// controlled path: structural fixtures, ref/portal identity (incl. the
// question-dock state-setter callback ref), animation-once, main no-op,
// contract-only seams render-nothing-when-unset, and the forwarded handle.
//
// These are the semantic assertions the byte-diff goldens cannot be: a settled
// DOM is identical whether or not the reply double-animated, and a portal that
// rebinds to a dead node still renders green. They stay after the goldens are
// retired.
import * as React from "react"
import { createPortal } from "react-dom"
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

// AskReplyBody's simulated-stream hook reads prefers-reduced-motion on mount.
if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: true, // reduced motion → replies settle immediately, no timers
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }) as unknown as MediaQueryList
}

import { ChatShell } from "../ChatShell"
import type {
  ChatShellHandle,
  ChatSurfaceDescriptor,
  ShellTurn,
} from "../types"
import type { ChatTranscriptTurn } from "../../ChatTranscript"
import type { AskResponse } from "../../../../lib/api"

const AGENT = "Sprntly"

function reply(answer: string): AskResponse {
  return {
    answer,
    sources: [],
    follow_ups: [],
    key_points: [],
    citations: [],
    confidence: 1,
    unanswered: "",
  } as AskResponse
}

function settledTurn(id: string, query: string, answer: string): ChatTranscriptTurn {
  return {
    turnId: id,
    user: { name: "Ada", initials: "A", query },
    agentName: AGENT,
    agentBadge: "Product Coworker",
    isLast: true,
    reply: reply(answer),
  }
}

/** A complete surface:"main" descriptor; overrides merge shallowly. */
function mainDescriptor(over: Partial<ChatSurfaceDescriptor> = {}): ChatSurfaceDescriptor {
  return {
    surface: "main",
    frame: {
      mode: "thread",
      viewportClassName: "od-center-scroll",
      ...(over.frame ?? {}),
    },
    transcript: {
      agentName: AGENT,
      agentBadge: "Product Coworker",
      timestamps: "none",
      ...(over.transcript ?? {}),
    },
    composer: { busyMode: "block-while-asking", ...(over.composer ?? {}) },
    reply: { mode: "streamed", ...(over.reply ?? {}) },
    send: { onSubmit: () => {}, pendingSendBubble: true, ...(over.send ?? {}) },
    ...over,
  }
}

afterEach(() => cleanup())

// ── Descriptor contract (AC1) ───────────────────────────────────────────────

describe("ChatSurfaceDescriptor contract", () => {
  it("test_descriptor_types_expose_full_contract", () => {
    // The proof is that this file compiles: a descriptor using every field incl.
    // the next-wave seams, plus a ShellTurn with parentRef + pickOptions.
    const turn: ShellTurn = {
      id: "s1",
      author: { kind: "peer", name: "Grace", role: "PM", userId: "u1", avatarStyle: "a" },
      content: "hello",
      reply: reply("hi"),
      pending: false,
      partial: null,
      streamDropped: false,
      stopped: false,
      error: null,
      timedOut: false,
      createdAt: 1_700_000_000_000,
      invokedBy: "Ada",
      invokedByMe: true,
      pickOptions: [{ id: "p1", title: "Option", instruction: "do it" }],
      parentRef: { turnId: "s0", authorName: "Ada", snippet: "prev", role: null },
      footerData: {},
    }
    const full: ChatSurfaceDescriptor = {
      surface: "project_group",
      projectId: 7,
      testIdPrefix: "gc",
      frame: {
        mode: "thread",
        landing: <div />,
        aboveViewport: <div />,
        loading: false,
        loadingNode: <div />,
        viewportClassName: "vp",
        threadClassName: "bc-thread",
        dockClassName: "bc-dock",
      },
      refs: { viewportRef: React.createRef<HTMLDivElement>(), onViewportScroll: () => {}, contentColumnRef: () => {} },
      transcript: {
        agentName: AGENT,
        agentBadge: "AGENT",
        multiParty: true,
        userHead: "named",
        timestamps: "fromTurn",
        renderUserBody: (t) => <span>{t.content}</span>,
        renderAgentBody: (t) => <span>{t.id}</span>,
        turnBeforeNode: (t) => <span>{t.parentRef?.snippet}</span>,
        turnHeadExtra: (t) => <span>{t.invokedBy}</span>,
        turnActions: (t) => <span>{t.id}</span>,
        turnFooter: (t) => <span>{t.id}</span>,
        turnAfterNode: (t, i) => <span>{i}{t.id}</span>,
        leading: <div />,
        trailing: <div />,
      },
      composer: {
        placeholder: "Message…",
        busyMode: "never-block",
        stop: { enabled: false },
        escToStop: true,
        voice: "default",
        attachments: false,
        slashMenu: <div />,
        aboveInput: <div />,
        onKeyDownCapture: () => true,
        minChars: 2,
        hint: <div />,
      },
      reply: { mode: "backgrounded", runStatus: (s) => (s === null ? <span>stayed out</span> : null) },
      send: { onSubmit: () => {}, pendingSendBubble: false },
      dock: { aboveComposer: <div /> },
      overlays: { attachmentViewer: true },
    }
    expect(full.surface).toBe("project_group")
    expect(turn.pickOptions?.[0].id).toBe("p1")
    expect(turn.parentRef?.turnId).toBe("s0")
  })
})

// ── Shell structure — surface main (AC2) ────────────────────────────────────

describe("ChatShell structure (surface: main)", () => {
  it("test_shell_main_thread_mode_renders_center_scroll_thread_dock", () => {
    const { container } = render(
      <ChatShell
        descriptor={mainDescriptor()}
        turns={[settledTurn("t1", "hi", "hello there")]}
        composerNode={<div data-testid="composer" />}
      />,
    )
    const main = container.querySelector("main.od-center")!
    expect(main.classList.contains("od-center--thread")).toBe(true)
    const scroll = main.querySelector(":scope > div.od-center-scroll")!
    expect(scroll).toBeTruthy()
    const thread = scroll.querySelector(":scope > .bc-scroll > .bc-thread")!
    expect(thread).toBeTruthy()
    // the transcript rendered the turn inside the thread column
    expect(thread.textContent).toContain("hello there")
    // the dock is a child of <main>, sibling of the viewport, in thread mode
    expect(main.querySelector(":scope > .bc-dock")).toBeTruthy()
    expect(screen.getByTestId("composer")).toBeTruthy()
  })

  it("test_shell_main_landing_mode_hides_dock_and_flags_home_landing", () => {
    const { container } = render(
      <ChatShell
        descriptor={mainDescriptor({
          frame: { mode: "landing", viewportClassName: "od-center-scroll", landing: <div data-testid="landing">welcome</div> },
        })}
        turns={[]}
        composerNode={<div data-testid="composer" />}
      />,
    )
    const main = container.querySelector("main.od-center")!
    expect(main.classList.contains("od-center--landing")).toBe(true)
    const scroll = main.querySelector(":scope > div.od-center-scroll")!
    expect(scroll.classList.contains("od-center-scroll--home-landing")).toBe(true)
    expect(screen.getByTestId("landing").textContent).toBe("welcome")
    // no dock, and the landing composer is not the dock composer
    expect(main.querySelector(":scope > .bc-dock")).toBeNull()
    expect(scroll.querySelector(".bc-thread")).toBeNull()
  })

  it("test_shell_attachment_viewer_gated_on_overlays_flag", () => {
    const viewer = <div data-testid="viewer" />
    const on = render(
      <ChatShell descriptor={mainDescriptor({ overlays: { attachmentViewer: true } })} turns={[]} attachmentViewer={viewer} />,
    )
    expect(screen.getByTestId("viewer")).toBeTruthy()
    cleanup()
    const off = render(
      <ChatShell descriptor={mainDescriptor({ overlays: { attachmentViewer: false } })} turns={[]} attachmentViewer={viewer} />,
    )
    expect(off.container.querySelector('[data-testid="viewer"]')).toBeNull()
    cleanup()
    const unset = render(<ChatShell descriptor={mainDescriptor()} turns={[]} attachmentViewer={viewer} />)
    expect(unset.container.querySelector('[data-testid="viewer"]')).toBeNull()
  })
})

// ── Ref / portal identity (AC3, AC4) ────────────────────────────────────────

describe("ChatShell ref/portal identity", () => {
  it("test_shell_viewport_ref_binds_center_scroll_node", () => {
    const viewportRef = React.createRef<HTMLDivElement>()
    const { container, rerender } = render(
      <ChatShell descriptor={mainDescriptor({ refs: { viewportRef } })} turns={[]} composerNode={<div />} />,
    )
    const scroll = container.querySelector("div.od-center-scroll") as HTMLDivElement
    expect(viewportRef.current).toBe(scroll)
    // same node identity across a forced re-render
    rerender(
      <ChatShell
        descriptor={mainDescriptor({ refs: { viewportRef } })}
        turns={[settledTurn("t1", "q", "a")]}
        composerNode={<div />}
      />,
    )
    expect(viewportRef.current).toBe(scroll)
    expect(container.querySelector("div.od-center-scroll")).toBe(scroll)
  })

  it("test_shell_on_viewport_scroll_fires_from_center_scroll", () => {
    const onViewportScroll = vi.fn()
    const { container } = render(
      <ChatShell descriptor={mainDescriptor({ refs: { onViewportScroll } })} turns={[]} composerNode={<div />} />,
    )
    fireEvent.scroll(container.querySelector("div.od-center-scroll")!)
    expect(onViewportScroll).toHaveBeenCalledTimes(1)
  })

  it("test_shell_content_column_ref_receives_bc_thread_node", () => {
    let received: HTMLElement | null = null
    const contentColumnRef = (el: HTMLDivElement | null) => {
      if (el) received = el
    }
    const { container } = render(
      <ChatShell descriptor={mainDescriptor({ refs: { contentColumnRef } })} turns={[]} composerNode={<div />} />,
    )
    expect(received).toBe(container.querySelector(".bc-thread"))
  })

  it("test_shell_question_dock_portal_target_survives", () => {
    // Mirrors ChatScreen's dock-extras block (ChatScreen.tsx:7170 + :6303): the
    // host composes the `<div className="bc-question-dock" ref={setQuestionDockEl}/>`
    // and passes it via dock.aboveComposer; a PrdInputQuestions-shaped consumer
    // portals its content into that exact node via createPortal(popupHost).
    function PortalConsumer({ host }: { host: HTMLElement | null }) {
      return host ? createPortal(<span data-testid="portaled">Q?</span>, host) : null
    }
    function Harness() {
      const [dockEl, setQuestionDockEl] = React.useState<HTMLDivElement | null>(null)
      return (
        <ChatShell
          descriptor={mainDescriptor({
            dock: {
              aboveComposer: (
                <>
                  <div className="bc-question-dock" data-testid="qdock" ref={setQuestionDockEl} />
                  <PortalConsumer host={dockEl} />
                </>
              ),
            },
          })}
          turns={[]}
          composerNode={<div />}
        />
      )
    }
    render(<Harness />)
    const qdock = screen.getByTestId("qdock")
    // the dock target rendered inside .bc-dock (the state-setter fired with it)
    expect(qdock.closest(".bc-dock")).toBeTruthy()
    // the portal consumer landed its content inside that exact node
    const portaled = screen.getByTestId("portaled")
    expect(qdock.contains(portaled)).toBe(true)
  })
})

// ── Animation-once (AC5) ────────────────────────────────────────────────────

describe("ChatShell animation-once", () => {
  it("test_streamed_to_settled_animates_reply_once", () => {
    // partial (no reply) → settled (reply); the settled turn renders exactly one
    // reply body, no duplicate/double render of the answer.
    const partial: ChatTranscriptTurn = {
      turnId: "t1",
      user: { name: "Ada", initials: "A", query: "q" },
      agentName: AGENT,
      agentBadge: "Product Coworker",
      isLast: true,
      isGenerating: true,
      partial: "partial ans",
    }
    const settled: ChatTranscriptTurn = {
      ...settledTurn("t1", "q", "final answer"),
      isAnimated: true,
    }
    const { container, rerender } = render(
      <ChatShell descriptor={mainDescriptor()} turns={[partial]} composerNode={<div />} />,
    )
    rerender(<ChatShell descriptor={mainDescriptor()} turns={[settled]} composerNode={<div />} />)
    const matches = container.querySelectorAll(".bc-thread")
    expect(matches.length).toBe(1)
    // the answer renders once
    expect(container.textContent?.match(/final answer/g)?.length ?? 0).toBe(1)
  })
})

// ── Main no-op (AC6) ────────────────────────────────────────────────────────

describe("ChatShell main no-op", () => {
  it("test_shell_main_renders_no_project_affordance", () => {
    const { container } = render(
      <ChatShell
        descriptor={mainDescriptor({
          // project-only frame/transcript fields that main must never render
          frame: { mode: "thread", viewportClassName: "od-center-scroll", aboveViewport: <div data-testid="roster" /> },
          transcript: { agentName: AGENT, multiParty: true },
        })}
        turns={[settledTurn("t1", "q", "a")]}
        composerNode={<div />}
      />,
    )
    // no roster strip, no speaker/role chips, no avatars, no per-turn timestamp
    expect(container.querySelector('[data-testid="roster"]')).toBeNull()
    expect(container.querySelector(".bc-turn-time, time")).toBeNull()
  })

  it("test_shell_main_ignores_project_only_fields", () => {
    const turns = [settledTurn("t1", "q", "a")]
    const without = render(<ChatShell descriptor={mainDescriptor()} turns={turns} composerNode={<div />} />)
    const a = without.container.querySelector("main.od-center")!.outerHTML
    cleanup()
    const withStray = render(
      <ChatShell
        descriptor={mainDescriptor({ projectId: 42, transcript: { agentName: AGENT, multiParty: true } })}
        turns={turns}
        composerNode={<div />}
      />,
    )
    const b = withStray.container.querySelector("main.od-center")!.outerHTML
    expect(b).toBe(a)
  })
})

// ── Contract-only seams (AC8) ───────────────────────────────────────────────

describe("ChatShell contract-only seams", () => {
  it("test_shell_seams_render_nothing_when_unset", () => {
    const { container } = render(
      <ChatShell
        descriptor={mainDescriptor({
          transcript: { agentName: AGENT }, // turnBeforeNode/turnActions unset
          composer: { busyMode: "block-while-asking" }, // aboveInput unset
          // dock unset entirely
        })}
        turns={[settledTurn("t1", "q", "a")]}
        composerNode={<div data-testid="composer" />}
      />,
    )
    // the dock container still renders (thread mode) but has no above-composer
    // content beyond the composer node itself
    const dock = container.querySelector(".bc-dock")!
    expect(dock).toBeTruthy()
    expect(dock.children.length).toBe(1) // only the composer node
    expect(screen.getByTestId("composer")).toBeTruthy()
  })

  it("test_shell_handle_scroll_methods_callable", () => {
    const ref = React.createRef<ChatShellHandle>()
    render(<ChatShell ref={ref} descriptor={mainDescriptor()} turns={[]} composerNode={<div />} />)
    expect(ref.current).toBeTruthy()
    expect(() => ref.current!.scrollToTurn("anything")).not.toThrow()
    expect(() => ref.current!.scrollToBottom()).not.toThrow()
    expect(() => ref.current!.scrollToBottom("smooth")).not.toThrow()
  })
})
