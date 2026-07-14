// @vitest-environment jsdom
//
// ChatScreen — the insight renders as the agent's FIRST CHAT MESSAGE, not as a
// pinned heading above the chat.
//
// A tab bound to a PRD / brief insight (opened via openPrdTab) used to show a
// pinned `.chat-insight-pin` bar above the thread. It now renders inside the
// thread as an agent message (`[data-testid=chat-insight-msg]`) that hosts the
// Generate/View PRD + Generate/View Prototype CTAs. Those CTAs relabel to
// "View …" once the artifact is saved (PRD: loaded on the tab; prototype: ready
// in the DB via the brief-prototype map).
//
// These tests mount the REAL ChatScreen inside the real Navigation + Content
// providers and drive openPrdTab through the same tiny harness the PRD-tab test
// uses, asserting the in-flow message (not the removed pin) and the CTA labels.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

if (typeof window !== "undefined") window.scrollTo = () => {}

if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false, media: query, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    }) as unknown as MediaQueryList
}

// ── Boundary mocks (network / router / heavy contexts) ─────────────────────
vi.mock("../../../../lib/api", () => {
  class ApiError extends Error {
    status = 0
    body: unknown = null
  }
  return {
    ApiError,
    askApi: { ask: vi.fn(), skills: vi.fn().mockResolvedValue({ skills: [] }) },
    briefApi: { current: vi.fn().mockResolvedValue({ id: 1, insights: [] }) },
    conversationsApi: {
      create: vi.fn().mockResolvedValue({ id: 1 }),
      addTurn: vi.fn().mockResolvedValue({}),
    },
  }
})

const runPrdGeneration = vi.fn().mockResolvedValue({
  ok: true,
  prd: { prd_id: 77, title: "Generated PRD", metaLine: "", sections: [] },
})
const loadPrdById = vi.fn().mockResolvedValue({
  ok: true, prd: { prd_id: 796, title: "Loaded PRD", metaLine: "", sections: [] },
})
vi.mock("../../../../lib/runPrdGeneration", () => ({
  runPrdGeneration: (...args: unknown[]) => runPrdGeneration(...args),
  resumePrdGeneration: vi.fn(),
  runPrdGenerationFromBacklog: vi.fn().mockResolvedValue({
    ok: true, prd: { prd_id: 88, title: "Backlog PRD", metaLine: "", sections: [] },
  }),
  loadPrdById: (...args: unknown[]) => loadPrdById(...args),
}))

const runAskGeneration = vi.fn().mockResolvedValue({
  answer: "canned", sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "",
})
vi.mock("../../../../lib/runAskGeneration", () => ({
  runAskGeneration: (...args: unknown[]) => runAskGeneration(...args),
  resumeAskGeneration: vi.fn(),
  getPendingAsk: vi.fn().mockReturnValue(null),
}))

vi.mock("../../../../lib/usePipelineStatus", () => ({
  usePipelineStatus: () => ({ runStatus: null, isTriggering: false, showCompleted: false, triggerRun: vi.fn() }),
}))

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams(""),
}))

vi.mock("../../../../context/WorkspaceContext", () => ({
  profileDisplayName: () => "Ada Lovelace",
  useWorkspace: () => ({ loading: false, profile: null, workspace: null, refresh: async () => {} }),
}))

vi.mock("../../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme", setActiveCompany: vi.fn() }),
}))

vi.mock("../../../../lib/auth", () => ({ useAuth: () => ({ kind: "anonymous" }) }))

// The brief→prototype map drives the prototype CTA's View/Generate label. A
// hoisted mutable map lets a test seed a READY prototype for a given insight so
// the "View prototype" flip is exercised end-to-end.
const { protoMap, mapState } = vi.hoisted(() => ({ protoMap: new Map<number, unknown>(), mapState: { loading: false } }))
vi.mock("../../../design-agent/useBriefPrototypeMap", () => ({
  useBriefPrototypeMap: () => ({ entriesByInsight: protoMap, loading: mapState.loading, error: false, refetch: vi.fn() }),
}))

import { NavigationProvider, useNavigation, type PrdTabRequest } from "../../../../context/NavigationContext"
import { ContentProvider } from "../../../../context/ContentContext"
import { ChatScreen } from "../ChatScreen"

function Harness({ request }: { request: PrdTabRequest }) {
  const { openPrdTab } = useNavigation()
  return React.createElement(
    React.Fragment,
    null,
    React.createElement("button", { onClick: () => openPrdTab(request) }, "open-prd"),
    React.createElement(ChatScreen),
  )
}

function renderWith(request: PrdTabRequest) {
  return render(
    React.createElement(
      NavigationProvider,
      null,
      React.createElement(ContentProvider, null, React.createElement(Harness, { request })),
    ),
  )
}

const insightMsg = () => screen.getByTestId("chat-insight-msg")
async function clickOpenPrd() {
  await act(async () => { fireEvent.click(screen.getByText("open-prd")) })
}

beforeEach(() => {
  localStorage.clear()
  protoMap.clear()
  mapState.loading = false
  runPrdGeneration.mockClear()
  loadPrdById.mockClear()
})
afterEach(() => {
  cleanup()
  localStorage.clear()
  protoMap.clear()
})

describe("ChatScreen — insight renders as an in-chat message (not a pinned heading)", () => {
  const READY: PrdTabRequest = {
    title: "PRD · Enterprise expansion is stalled",
    source: { kind: "ready", prd: { prd_id: 5, title: "Enterprise expansion is stalled", metaLine: "", sections: [] } as never, meta: null },
  }

  it("shows the insight as an agent message with View PRD + a prototype CTA, and NO pinned heading", async () => {
    renderWith(READY)
    await clickOpenPrd()

    // The insight is a message in the thread — not the removed pinned bar.
    await waitFor(() => expect(insightMsg()).toBeTruthy())
    expect(screen.queryByTestId("chat-insight-pin")).toBeNull()
    expect(document.querySelector(".chat-insight-pin")).toBeNull()

    const msg = insightMsg()
    // The insight sentence shows WITHOUT the redundant "PRD · " tab-title prefix.
    expect(within(msg).getByText("Enterprise expansion is stalled")).toBeTruthy()
    expect(msg.textContent).not.toContain("PRD · ")
    // A saved PRD is loaded on the tab → the PRD CTA reads "View PRD" (not "Open").
    expect(within(msg).getByRole("button", { name: "View PRD" })).toBeTruthy()
    expect(within(msg).queryByRole("button", { name: /open prd/i })).toBeNull()
    // The prototype CTA is present; no prototype built yet → "Generate prototype".
    expect(within(msg).getByRole("button", { name: "Generate prototype" })).toBeTruthy()

    // …and the chat composer is present even though the thread is empty — the
    // user can immediately ask Sprntly about this PRD. (Regression: the dock
    // composer was gated on a non-empty thread, so insight tabs had no input.)
    expect(screen.getByPlaceholderText(/Ask Sprntly anything/i)).toBeTruthy()
  })

  it("renders the insight body under the title when the request carries one", async () => {
    renderWith({
      ...READY,
      insightBody: "Stakeholders keep asking for **read-only** access to dashboards.",
    })
    await clickOpenPrd()

    await waitFor(() => expect(insightMsg()).toBeTruthy())
    const body = document.querySelector(".bc-insight-msg-body")
    expect(body).toBeTruthy()
    // Body text renders (markdown **bold** → <strong>, so assert on text + node).
    expect(body!.textContent).toContain("Stakeholders keep asking for")
    expect(within(body as HTMLElement).getByText("read-only").tagName).toBe("STRONG")
    // The heading is still present and leads.
    expect(within(insightMsg()).getByText("Enterprise expansion is stalled")).toBeTruthy()
  })

  it("omits the body block when the request has no insightBody", async () => {
    renderWith(READY)
    await clickOpenPrd()

    await waitFor(() => expect(insightMsg()).toBeTruthy())
    expect(document.querySelector(".bc-insight-msg-body")).toBeNull()
  })

  it("backfills the body onto an already-open tab that was opened without one", async () => {
    // First open (no body) → tab exists, no body block.
    const { rerender } = renderWith(READY)
    await clickOpenPrd()
    await waitFor(() => expect(insightMsg()).toBeTruthy())
    expect(document.querySelector(".bc-insight-msg-body")).toBeNull()

    // Reopen the SAME tab (same title) now carrying a body — it must backfill.
    rerender(
      React.createElement(
        NavigationProvider,
        null,
        React.createElement(
          ContentProvider,
          null,
          React.createElement(Harness, { request: { ...READY, insightBody: "Body added on reopen." } }),
        ),
      ),
    )
    await clickOpenPrd()
    await waitFor(() => expect(document.querySelector(".bc-insight-msg-body")).toBeTruthy())
    expect(document.querySelector(".bc-insight-msg-body")!.textContent).toContain("Body added on reopen.")
  })

  it("relabels the prototype CTA to 'View prototype' once one is ready in the DB", async () => {
    // Seed a READY prototype for the insight this tab is bound to (index 0).
    protoMap.set(0, {
      insight_index: 0,
      prd_id: 77,
      prd_title: "Generated PRD",
      prototype: { ready: true, preview_image_url: null },
    })

    renderWith({
      title: "PRD · Enterprise expansion is stalled",
      source: { kind: "generate", meta: { briefId: 7, insightIndex: 0 } },
    })
    await clickOpenPrd()

    // Generation resolves (mock) → PRD loads on the tab → "View PRD".
    await waitFor(() => expect(within(insightMsg()).getByRole("button", { name: "View PRD" })).toBeTruthy())
    // The seeded ready prototype flips the prototype CTA to "View prototype".
    await waitFor(() => expect(within(insightMsg()).getByRole("button", { name: "View prototype" })).toBeTruthy())
    expect(within(insightMsg()).queryByRole("button", { name: "Generate prototype" })).toBeNull()
  })
})

// ── After a reload, the PRD CTA is DB-backed (View PRD + load, not regenerate) ──
// Tabs persist to localStorage with `prd` stripped, so on reload `activeTab.prd`
// is null even when a PRD exists in the DB. The CTA must read the brief-prototype
// map (hasPrd), show "View PRD", and LOAD the existing PRD by id — never spawn a
// fresh generation. Regression: it showed "Generate PRD" and regenerated.
describe("ChatScreen — insight PRD CTA survives a reload (DB-backed)", () => {
  // Render ChatScreen alone; the active insight tab is restored from localStorage
  // (the reload path) rather than opened via openPrdTab this session.
  function renderRestored() {
    return render(
      React.createElement(
        NavigationProvider,
        null,
        React.createElement(ContentProvider, null, React.createElement(ChatScreen)),
      ),
    )
  }

  it("shows 'View PRD' and loads the existing PRD (no regeneration) when the DB has one", async () => {
    // Persisted tab (prd stripped, briefMeta kept) — exactly what a reload restores.
    localStorage.setItem("sprntly_chat_tabs_anon_acme", JSON.stringify([
      { id: "tab-reload", title: "PRD · Enterprise expansion is stalled", dbConvId: null, briefMeta: { briefId: 7, insightIndex: 0 } },
    ]))
    localStorage.setItem("sprntly_chat_active_tab_anon_acme", "tab-reload")
    // The DB map says insight 0 already has PRD #796 (no prototype yet).
    protoMap.set(0, {
      insight_index: 0,
      prd_id: 796,
      prd_title: "Enterprise expansion is stalled",
      prototype: null,
    })

    await act(async () => { renderRestored() })

    // The CTA reflects the DB PRD even though the tab carries no loaded prd.
    const msg = insightMsg()
    await waitFor(() => expect(within(msg).getByRole("button", { name: "View PRD" })).toBeTruthy())
    expect(within(msg).queryByRole("button", { name: "Generate PRD" })).toBeNull()

    // Clicking it LOADS the existing PRD by id — and never regenerates.
    await act(async () => { fireEvent.click(within(insightMsg()).getByRole("button", { name: "View PRD" })) })
    await waitFor(() => expect(loadPrdById).toHaveBeenCalledWith(796))
    expect(runPrdGeneration).not.toHaveBeenCalled()
  })

  it("shows a neutral 'Loading…' (not 'Generate PRD') while the map is still loading", async () => {
    // Map still in flight → we don't yet know if a PRD exists. The CTA must not
    // flash "Generate PRD" (it would flip to "View PRD" the instant the map lands).
    mapState.loading = true
    localStorage.setItem("sprntly_chat_tabs_anon_acme", JSON.stringify([
      { id: "tab-reload", title: "PRD · Enterprise expansion is stalled", dbConvId: null, briefMeta: { briefId: 7, insightIndex: 0 } },
    ]))
    localStorage.setItem("sprntly_chat_active_tab_anon_acme", "tab-reload")

    await act(async () => { renderRestored() })

    const btn = within(insightMsg()).getByRole("button", { name: "Loading…" })
    expect(btn).toBeTruthy()
    expect((btn as HTMLButtonElement).disabled).toBe(true)
    // Never the premature "Generate PRD" (nor a wrong "View PRD") mid-load.
    expect(within(insightMsg()).queryByRole("button", { name: "Generate PRD" })).toBeNull()
    expect(within(insightMsg()).queryByRole("button", { name: "View PRD" })).toBeNull()
  })
})
