// LocateConfirmView — defensive rendering for malformed/partial candidates.
// Guards a confirmed prod client-side crash: a candidate whose entry_component
// is null/undefined drove `deriveScreenLabel` into `undefined.replace(...)`,
// throwing an uncaught render exception that tripped the whole-page React error
// boundary. The deriveScreenLabel + mapLocateCandidates ?? "" coalescing must
// let the picker render without throwing.
//
// node-env vitest, SSR via renderToStaticMarkup — same harness as the sibling
// LocateConfirmView.test.tsx. mapLocateCandidates is exercised directly to prove
// the adapter boundary also hardens partial backend candidates.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { afterEach, describe, expect, it, vi } from "vitest"

// Classic JSX runtime reads globalThis.React for createElement.
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import {
  LocateConfirmView,
  type LocateConfirmViewProps,
  type LocateConfirmCandidate,
} from "../ClarifyingQuestionSurface"
import { mapLocateCandidates } from "../GenerateModal"
import type { LocateCandidate } from "../../../lib/api"

afterEach(() => {
  vi.restoreAllMocks()
})

function renderView(props: LocateConfirmViewProps): string {
  return renderToStaticMarkup(React.createElement(LocateConfirmView, props))
}

describe("LocateConfirmView tolerates malformed candidates (no render crash)", () => {
  it("renders a candidate whose entry_component is null without throwing", () => {
    // A raw, partial candidate as the backend could emit it: entry_component
    // null. Pre-fix this drove deriveScreenLabel into undefined.replace → throw.
    const candidates: LocateConfirmCandidate[] = [
      {
        id: "/team",
        route: "/team",
        // Cast: the type says string, but the prod payload can be null — that
        // unvalidated shape is exactly what this guard defends against.
        entry_component: null as unknown as string,
        component_count: 3,
        rationale: "Manage teammates and roles.",
        is_top: true,
      },
      {
        id: "/dashboard",
        route: "/dashboard",
        entry_component: "DashboardPage",
        component_count: 7,
        rationale: "Home overview after sign-in.",
        is_top: false,
      },
    ]

    let html = ""
    expect(() => {
      html = renderView({
        candidates,
        onChoose: vi.fn(),
        onSearchOther: vi.fn(),
      })
    }).not.toThrow()

    // The picker still renders the lead card + the "Search for another screen"
    // affordance — the user is not stranded on a blank error boundary.
    expect(html).toContain('data-testid="locate-confirm-use"')
    expect(html).toContain('data-testid="locate-confirm-search-other"')
    expect(html).toContain("Search for another screen")
    // The malformed candidate is the is_top lead; its label falls back to the
    // route when entry_component is null.
    expect(html).toContain("/team")
  })

  it("renders a candidate whose entry_component is missing/undefined without throwing", () => {
    // entry_component key entirely absent — undefined at the throw site.
    const partial = {
      id: "/dashboard",
      route: "/dashboard",
      component_count: 7,
      rationale: "Home overview after sign-in.",
      is_top: true,
    } as unknown as LocateConfirmCandidate

    let html = ""
    expect(() => {
      html = renderView({
        candidates: [partial],
        onChoose: vi.fn(),
        onSearchOther: vi.fn(),
      })
    }).not.toThrow()

    expect(html).toContain('data-testid="locate-confirm-use"')
    expect(html).toContain('data-testid="locate-confirm-search-other"')
    expect(html).toContain("/dashboard")
  })

  it("renders a SINGLE malformed candidate (null entry_component, null rationale) as the lead only, without throwing", () => {
    const partial = {
      id: "/team",
      route: "/team",
      entry_component: null,
      component_count: 2,
      rationale: null,
      is_top: true,
    } as unknown as LocateConfirmCandidate

    let html = ""
    expect(() => {
      html = renderView({
        candidates: [partial],
        onChoose: vi.fn(),
        onSearchOther: vi.fn(),
      })
    }).not.toThrow()

    // Lead card present; no Other options / alt rows for a single candidate.
    expect(html).toContain('data-testid="locate-confirm-use"')
    expect(html).not.toContain('data-testid="locate-others-label"')
    expect(html).not.toContain('data-testid="locate-alt-row"')
    // null rationale → narrative line omitted (no crash).
    expect(html).not.toContain('data-testid="locate-confirm-narrative"')
    // Route fallback label shows.
    expect(html).toContain("/team")
  })
})

describe("mapLocateCandidates coalesces partial backend candidates", () => {
  it("null/undefined fields become safe defaults the view can render", () => {
    // A degraded ranked[] entry: entry_component null, route undefined,
    // rationale null, component_count undefined, id null.
    const ranked = [
      {
        id: null,
        route: undefined,
        entry_component: null,
        confidence: 0.4,
        rationale: null,
        ambiguous: true,
        component_count: undefined,
      } as unknown as LocateCandidate,
    ]

    const mapped = mapLocateCandidates(ranked)
    expect(mapped[0]!.id).toBe("")
    expect(mapped[0]!.route).toBe("")
    expect(mapped[0]!.entry_component).toBe("")
    expect(mapped[0]!.rationale).toBe("")
    expect(mapped[0]!.component_count).toBe(0)
    expect(mapped[0]!.is_top).toBe(true)

    // The mapped output feeds the view without throwing.
    expect(() =>
      renderView({ candidates: mapped, onChoose: vi.fn() }),
    ).not.toThrow()
  })

  it("well-formed candidates pass through byte-identically (non-behavioral)", () => {
    const ranked: LocateCandidate[] = [
      {
        id: "/team",
        route: "/team",
        entry_component: "TeamScreen",
        confidence: 0.9,
        rationale: "best match",
        ambiguous: false,
        component_count: 3,
      },
    ]
    const mapped = mapLocateCandidates(ranked)
    expect(mapped).toEqual([
      {
        id: "/team",
        route: "/team",
        entry_component: "TeamScreen",
        component_count: 3,
        rationale: "best match",
        is_top: true,
      },
    ])
  })
})
