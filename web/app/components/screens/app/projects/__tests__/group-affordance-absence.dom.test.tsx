// @vitest-environment jsdom
//
// Closed-world guard for the group-chat-removal ticket (AC2/AC6): no group
// affordance renders anywhere, and the group-only FE symbols are gone from
// the source tree. Complements — does not duplicate — the per-file behaviour
// tests (`ProjectDetailScreen.test.tsx`, `ProjectMainThread.test.tsx`,
// `ProjectsScreen.test.tsx`, `useProjectConversation.actions.dom.test.tsx`);
// this file is the single place that asserts the CLOSED WORLD across the
// whole surface.
import * as React from "react"
import { readFileSync, existsSync } from "node:fs"
import { join, relative } from "node:path"
import { act, cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React
if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = ((q: string) => ({
    matches: false, media: q, onchange: null,
    addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {}, dispatchEvent() { return false },
  })) as unknown as typeof window.matchMedia
}

// ── ProjectDetailView — no toggle, private-only mount (AC1/AC2) ────────────
// `ProjectMainThread` is mocked to the same light stub the shell's own test
// file uses — this suite proves the SHELL renders no toggle/badge, not the
// thread's own internals (that is `ProjectMainThread.test.tsx`'s job).
vi.mock("../ProjectMainThread", () => ({
  ProjectMainThread: (props: { projectId: number | string }) =>
    React.createElement("div", {
      "data-testid": "main-thread-individual",
      "data-project-id": String(props.projectId),
    }),
}))
// `ProjectDetailView` mounts the real (unmocked) `ProjectArtifactsDrawer`,
// which reads `useRouter` for its legacy deep-link fallback — no Next
// app-router provider exists in jsdom, so stub it (same isolation reason
// `ProjectDetailScreen.test.tsx` stubs it).
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}))

import { ProjectDetailView, type ProjectDetailViewProps } from "../ProjectDetailScreen"
import type { ArtifactItem, ProjectDetail, ProjectMemorySummary } from "../../../../../lib/api"

const hoursAgo = (h: number) => new Date(Date.now() - h * 3600 * 1000).toISOString()

const MULTI_MEMBER_PROJECT: ProjectDetail = {
  id: 101,
  company_id: "c1",
  workspace_id: "w1",
  name: "Instant-quote flow",
  origin: "manual",
  created_by: "u1",
  created_at: hoursAgo(48),
  updated_at: hoursAgo(2),
  members: [
    { kind: "agent", user_id: null, name: "Sprntly", role_label: "Agent coworker", status: "working" },
    { kind: "human", user_id: "u1", name: "David M.", email: "david@example.com", avatar_url: null, job_role: "PM", added_at: hoursAgo(48) },
    { kind: "human", user_id: "u2", name: "Shristi", email: "shristi@example.com", avatar_url: null, job_role: "Design", added_at: hoursAgo(40) },
  ],
}

const SOLO_PROJECT: ProjectDetail = {
  ...MULTI_MEMBER_PROJECT,
  id: 102,
  members: [
    { kind: "agent", user_id: null, name: "Sprntly", role_label: "Agent coworker", status: "working" },
    { kind: "human", user_id: "u1", name: "David M.", email: "david@example.com", avatar_url: null, job_role: "PM", added_at: hoursAgo(48) },
  ],
}

const ARTIFACTS: ArtifactItem[] = []
const MEMORY: ProjectMemorySummary = { summary_md: null, entry_count: 0, stale: false }
const noop = () => {}

function viewProps(overrides: Partial<ProjectDetailViewProps> = {}): ProjectDetailViewProps {
  return {
    project: MULTI_MEMBER_PROJECT,
    artifacts: ARTIFACTS,
    memory: MEMORY,
    ledgerCounts: null,
    ledgerRows: [],
    onOpenArtifacts: noop,
    onOpenArtifactInPlace: noop,
    openPrdId: null,
    onOpenTasks: noop,
    onOpenSettings: noop,
    onOpenInvite: noop,
    currentUserId: "current-viewer",
    onRemoveMember: noop,
    refetchArtifacts: noop,
    artifactsDrawerOpen: false,
    onCloseArtifactsDrawer: noop,
    ...overrides,
  }
}

afterEach(cleanup)

describe("closed-world — no group affordance renders (AC1/AC2)", () => {
  it("test_project_detail_renders_no_chat_toggle — a multi-member project renders no tablist/toggle testids", () => {
    render(React.createElement(ProjectDetailView, viewProps({ project: MULTI_MEMBER_PROJECT })))
    expect(screen.queryByTestId("topbar-chat-toggle")).toBeNull()
    expect(screen.queryByTestId("chat-row-group")).toBeNull()
    expect(screen.queryByTestId("chat-row-individual")).toBeNull()
  })

  it("test_project_detail_renders_no_chat_toggle — a SOLO project (one human member) also renders no tablist/toggle testids", () => {
    render(React.createElement(ProjectDetailView, viewProps({ project: SOLO_PROJECT })))
    expect(screen.queryByTestId("topbar-chat-toggle")).toBeNull()
    expect(screen.queryByTestId("chat-row-group")).toBeNull()
    expect(screen.queryByTestId("chat-row-individual")).toBeNull()
  })

  it("test_project_detail_mounts_private_surface_by_default — default mount has main-thread-individual, not main-thread-group", () => {
    render(React.createElement(ProjectDetailView, viewProps()))
    expect(screen.getByTestId("main-thread-individual")).toBeTruthy()
    expect(screen.queryByTestId("main-thread-group")).toBeNull()
  })

  it("no gc-mention-unread pill, private-unread dot, or group-chat card badge renders in the shell", () => {
    render(React.createElement(ProjectDetailView, viewProps()))
    expect(screen.queryByTestId("gc-mention-unread")).toBeNull()
    // The private-chat unread dot was removed entirely (not just its toggle
    // host) — the single-surface model has no affordance left to earn it.
    expect(screen.queryByTestId("individual-chat-unread-dot")).toBeNull()
    expect(screen.queryByText("Group chat")).toBeNull()
    expect(screen.queryByText("No group chat yet")).toBeNull()
  })
})

// ── The private composer — no mention picker overlay (AC2) ─────────────────
describe("closed-world — no @mention picker overlay (AC2)", () => {
  it("test_no_mention_picker_overlay_renders — typing '@' in the private composer produces no picker overlay node", async () => {
    // The picker overlay's own component/hook (`MentionPickerOverlay`,
    // `useMentionPicker`) no longer exist as files (see the grep guard
    // below) — this drives the REAL `useProjectConversation` hook's
    // `handleComposerInput` with an `@`-containing value and asserts (a) the
    // returned host-bag carries no picker fields any more and (b) rendering
    // `ConversationView` from that bag produces no overlay node in the DOM
    // (the overlay's own testid, `gc-mention-overlay`, from before this
    // ticket's removal).
    vi.resetModules()
    const h = vi.hoisted(() => ({
      individualChat: vi.fn(async () => ({ id: 7 })),
      listTurns: vi.fn(async () => ({ turns: [] })),
    }))
    vi.doMock("../../useMainConversation", () => ({
      useMainConversation: () => ({
        runConversationAsk: vi.fn(async () => {}),
        handleStopAsk: vi.fn(),
        runActionTurnInTab: vi.fn(async () => {}),
      }),
    }))
    vi.doMock("../useRealtimeChannel", () => ({ useRealtimeChannel: () => {} }))
    vi.doMock("../../../../../context/CompanyContext", () => ({ useCompany: () => ({ activeCompany: { id: 1 } }) }))
    vi.doMock("../../../../../context/WorkspaceContext", () => ({
      useWorkspace: () => ({ profile: { id: "me-1", full_name: "Me" } }),
      profileDisplayName: () => "Me",
    }))
    vi.doMock("../../../../../context/ContentContext", () => ({
      useContent: () => ({
        content: { prd: null, documentId: null, threadReports: [], threadReportsConversationId: null, reportFocusId: null },
        setContent: vi.fn(),
      }),
    }))
    vi.doMock("../../../../../context/NavigationContext", () => ({
      useNavigation: () => ({ openContentPanel: vi.fn(), contentPanelTab: null, showToast: vi.fn() }),
    }))
    vi.doMock("../../../../../lib/api", async (importOriginal) => {
      const actual = await importOriginal<typeof import("../../../../../lib/api")>()
      return {
        ...actual,
        projectsApi: { ...actual.projectsApi, individualChat: h.individualChat },
        conversationsApi: {
          ...actual.conversationsApi,
          listTurns: h.listTurns,
          update: vi.fn(async () => ({})),
          create: vi.fn(async () => ({ id: 7 })),
          addTurn: vi.fn(async () => ({ id: 1, conversation_id: 7, role: "user", content: "", created_at: "" })),
        },
        chatIntentApi: { ...actual.chatIntentApi, resolve: vi.fn(async () => null) },
        chatSuggestionsApi: { ...actual.chatSuggestionsApi, next: vi.fn(async () => ({ suggestions: [] })) },
        askApi: { ...actual.askApi, skills: vi.fn(async () => ({ skills: [] })), extractFile: vi.fn(async () => ({ markdown: "" })) },
      }
    })

    const { useProjectConversation } = await import("../useProjectConversation")
    const { ConversationView } = await import("../../ConversationView")

    let bag: ReturnType<typeof useProjectConversation> | null = null
    function Harness() {
      bag = useProjectConversation(101)
      return bag ? React.createElement(ConversationView, bag) : null
    }

    let view: ReturnType<typeof render>
    await act(async () => {
      view = render(React.createElement(Harness))
    })
    await act(async () => {
      await Promise.resolve()
    })

    // The host-bag no longer carries a picker field at all (structural proof
    // — not merely "closed/false").
    expect(bag && "mentionPickerNode" in bag).toBe(false)
    expect(bag && "mentionPickerOpen" in bag).toBe(false)

    // Drive the REAL composer-input handler with an "@"-containing value —
    // on the (removed) group surface this used to open the picker; here it
    // is a plain pass-through to the shared composer, nothing more.
    await act(async () => {
      bag!.handleComposerInput({
        target: { value: "@Fortune", selectionStart: 8, style: {}, scrollHeight: 0 },
      } as unknown as React.ChangeEvent<HTMLTextAreaElement>)
    })

    expect(view!.container.querySelector('[data-testid="gc-mention-overlay"]')).toBeNull()
  })
})

// ── ProjectsScreen — no group-chat card badge (AC2) ─────────────────────────
vi.mock("../ProjectsScreen", async (importOriginal) => importOriginal())

describe("closed-world — no group-chat card badge on the project list (AC2)", () => {
  it("test_project_card_has_no_group_badge — a project card renders no 'Group chat'/'No group chat yet' text", async () => {
    const { ProjectsView } = await import("../ProjectsScreen")
    const project = {
      id: 101,
      company_id: "c1",
      workspace_id: "w1",
      name: "Instant-quote flow",
      origin: "manual" as const,
      created_by: "u1",
      created_at: hoursAgo(48),
      updated_at: hoursAgo(2),
      artifact_counts: {},
      member_count: 2,
      memory_count: 0,
    }
    render(
      React.createElement(ProjectsView, {
        projects: [project],
        loading: false,
        error: false,
        onRetry: noop,
        search: "",
        onSearchChange: noop,
        onOpen: noop,
        onNewProject: noop,
      }),
    )
    expect(screen.queryByText("Group chat")).toBeNull()
    expect(screen.queryByText("No group chat yet")).toBeNull()
  })
})

// ── Source-tree closed-world grep (AC6) ─────────────────────────────────────
describe("closed-world — group FE symbols are gone from the source tree (AC6)", () => {
  it("test_group_symbols_absent_from_web_tree — the AC6 grep across web/app (product files) returns zero hits; the six clean-delete files no longer exist", () => {
    const webAppRoot = join(__dirname, "../../../../../")
    const CLEAN_DELETE_FILES = [
      "components/screens/app/projects/MentionBubble.tsx",
      "components/screens/app/projects/MentionBubble.module.css",
      "components/screens/app/projects/MentionPickerOverlay.tsx",
      "components/screens/app/projects/useMentionPicker.tsx",
      "components/screens/app/projects/mentionPicker.module.css",
      "components/screens/app/projects/useMentionNotifications.ts",
    ]
    for (const rel of CLEAN_DELETE_FILES) {
      expect(existsSync(join(webAppRoot, rel))).toBe(false)
    }

    const PATTERN = new RegExp(
      [
        "MentionBubble", "MentionPickerOverlay", "useMentionPicker", "useMentionNotifications",
        "detectMentionQuery", "insertMentionChip", "parseMentionChips", "mentionsAgent", "stripAgentMention",
        String.raw`projectsApi\.(groupTurns|postGroupTurn|groupChat)`,
        String.raw`surface *=== *"group"`, String.raw`activeChat *=== *"group"`,
        "has_group_chat", "main-thread-group", "topbar-chat-toggle",
      ].join("|"),
    )

    // Pre-existing, OUT-OF-SCOPE doc-comment mention this ticket does not
    // touch (`chat-shell/__tests__/ChatShell.module-graph.test.tsx`'s own
    // FORBIDDEN guard already bars ChatShell from importing `mentions` at
    // all — this is prose, not a reference, and the file is on this
    // ticket's explicit KEEP-entirely/do-not-touch list). Excluded by exact
    // (file, line) so a REAL future `MentionBubble` reference anywhere else
    // still fails this guard.
    const KNOWN_PRODUCT_FALSE_POSITIVES = new Set([
      "components/shared/chat-shell/types.ts:250",
    ])

    const hits: string[] = []
    function walk(dir: string) {
      const { readdirSync, statSync } = require("node:fs") as typeof import("node:fs")
      for (const entry of readdirSync(dir)) {
        const full = join(dir, entry)
        const st = statSync(full)
        if (st.isDirectory()) {
          if (entry === "node_modules" || entry === "__tests__") continue
          walk(full)
          continue
        }
        if (!/\.(ts|tsx)$/.test(entry)) continue
        const content = readFileSync(full, "utf8")
        const lines = content.split("\n")
        lines.forEach((line, i) => {
          if (!PATTERN.test(line)) return
          const relKey = `${relative(webAppRoot, full)}:${i + 1}`
          if (KNOWN_PRODUCT_FALSE_POSITIVES.has(relKey)) return
          hits.push(`${full}:${i + 1}: ${line.trim()}`)
        })
      }
    }
    walk(webAppRoot)
    expect(hits).toEqual([])
  })
})
