// LocateConfirmView — node-env vitest, no DOM, no testing-library.
// Pure views are SSR-rendered via renderToStaticMarkup for markup assertions.
// Click-handler wiring is verified by intercepting React.createElement to
// capture button props (same technique as the sibling surface test), then
// invoking the captured onClick directly.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { afterEach, describe, expect, it, vi } from "vitest"

// Classic JSX runtime reads globalThis.React for createElement.
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import {
  LocateConfirmView,
  ClarifyingQuestionSurfaceView,
  type LocateConfirmViewProps,
  type LocateConfirmCandidate,
} from "../ClarifyingQuestionSurface"

afterEach(() => {
  vi.restoreAllMocks()
})

// ---- helpers ----------------------------------------------------------------

function renderView(props: LocateConfirmViewProps): string {
  return renderToStaticMarkup(React.createElement(LocateConfirmView, props))
}

/**
 * Intercept React.createElement to capture props from every button element
 * rendered by LocateConfirmView. Allows verifying onClick wiring in node-env
 * vitest where no real DOM event dispatch is available.
 */
function captureButtonProps(
  props: LocateConfirmViewProps,
): Record<string, unknown>[] {
  const realReact = (globalThis as { React?: typeof React }).React!
  const realCreate = realReact.createElement
  const captured: Record<string, unknown>[] = []
  ;(globalThis as { React?: unknown }).React = {
    ...realReact,
    createElement: (
      type: unknown,
      p: Record<string, unknown> | null,
      ...kids: unknown[]
    ) => {
      if (type === "button") {
        captured.push(p ?? {})
      }
      return (realCreate as (...a: unknown[]) => unknown)(type, p, ...kids)
    },
  }
  try {
    renderToStaticMarkup(
      (realCreate as (...a: unknown[]) => React.ReactElement)(
        LocateConfirmView,
        props,
      ),
    )
  } finally {
    ;(globalThis as { React?: unknown }).React = realReact
  }
  return captured
}

const THREE_CANDIDATES: LocateConfirmCandidate[] = [
  {
    id: "/team",
    route: "/team",
    entry_component: "TeamScreen",
    component_count: 3,
    is_top: true,
  },
  {
    id: "/dashboard",
    route: "/dashboard",
    entry_component: "DashboardPage",
    component_count: 7,
    is_top: false,
  },
  {
    id: "/settings",
    route: "/settings",
    entry_component: "SettingsPanel",
    component_count: 2,
    is_top: false,
  },
]

// ---- Rendering --------------------------------------------------------------

describe("test_renders_each_candidate_with_route_and_count", () => {
  it("renders three choice buttons each showing label + route · N components (AC1)", () => {
    const html = renderView({ candidates: THREE_CANDIDATES, onChoose: vi.fn() })
    const choiceCount = (
      html.match(/data-testid="locate-confirm-choice"/g) ?? []
    ).length
    expect(choiceCount).toBe(3)
    expect(html).toContain("/team")
    expect(html).toContain("3 components")
    expect(html).toContain("/dashboard")
    expect(html).toContain("7 components")
    expect(html).toContain("/settings")
    expect(html).toContain("2 components")
  })
})

describe("test_top_candidate_marker_only_on_leading", () => {
  it("renders the Top candidate badge only where is_top === true (AC2)", () => {
    const html = renderView({ candidates: THREE_CANDIDATES, onChoose: vi.fn() })
    const badgeCount = (
      html.match(/data-testid="locate-confirm-top-badge"/g) ?? []
    ).length
    expect(badgeCount).toBe(1)
    expect(html).toContain("Top candidate")
  })
})

describe("test_default_question_text_when_omitted", () => {
  it("renders the default question text when question prop is omitted (AC1)", () => {
    const html = renderView({ candidates: THREE_CANDIDATES, onChoose: vi.fn() })
    expect(html).toContain("Which screen does this change affect?")
    expect(html).toContain('data-testid="locate-confirm-question"')
  })

  it("renders a custom question when supplied", () => {
    const html = renderView({
      candidates: THREE_CANDIDATES,
      question: "Pick the target screen",
      onChoose: vi.fn(),
    })
    expect(html).toContain("Pick the target screen")
  })
})

describe("test_label_derived_from_entry_component_with_route_fallback", () => {
  it("strips a trailing Screen suffix and returns the base word (AC7)", () => {
    const html = renderView({
      candidates: [
        {
          id: "/team",
          route: "/team",
          entry_component: "TeamScreen",
          component_count: 1,
          is_top: false,
        },
      ],
      onChoose: vi.fn(),
    })
    // Readable label "Team" is present; raw entry_component is not shown as-is
    expect(html).toContain("Team")
    expect(html).not.toContain("TeamScreen")
  })

  it("strips a trailing Page suffix too", () => {
    const html = renderView({
      candidates: [
        {
          id: "/briefing",
          route: "/briefing",
          entry_component: "BriefingPage",
          component_count: 4,
          is_top: false,
        },
      ],
      onChoose: vi.fn(),
    })
    expect(html).toContain("Briefing")
    expect(html).not.toContain("BriefingPage")
  })

  it("falls back to the raw route when entry_component yields an empty label (AC7)", () => {
    const html = renderView({
      candidates: [
        {
          id: "/team",
          route: "/team",
          entry_component: "",
          component_count: 1,
          is_top: false,
        },
      ],
      onChoose: vi.fn(),
    })
    // The label span should contain the route string as the fallback
    const labelMatch = html.match(
      /data-testid="locate-confirm-choice-label">(.*?)<\/span>/,
    )
    expect(labelMatch?.[1]).toBe("/team")
  })
})

// ---- Interaction ------------------------------------------------------------

describe("test_onChoose_fires_exact_route", () => {
  it("clicking a candidate button calls onChoose with the exact route string AND its stable id", () => {
    const onChoose = vi.fn()
    const buttons = captureButtonProps({ candidates: THREE_CANDIDATES, onChoose })
    // First candidate button → route "/team", id "/team"
    const firstChoice = buttons.find(
      (b) => b["data-testid"] === "locate-confirm-choice",
    )
    expect(firstChoice).toBeDefined()
    ;(firstChoice!["onClick"] as () => void)()
    expect(onChoose).toHaveBeenCalledTimes(1)
    expect(onChoose).toHaveBeenCalledWith("/team", "/team")
  })
})

describe("test_search_other_conditional_render_and_callback", () => {
  it("renders the search button when onSearchOther is provided (AC4)", () => {
    const html = renderView({
      candidates: THREE_CANDIDATES,
      onChoose: vi.fn(),
      onSearchOther: vi.fn(),
    })
    expect(html).toContain('data-testid="locate-confirm-search-other"')
    expect(html).toContain("Search for another screen")
  })

  it("omits the search button when onSearchOther is undefined (AC4)", () => {
    const html = renderView({ candidates: THREE_CANDIDATES, onChoose: vi.fn() })
    expect(html).not.toContain('data-testid="locate-confirm-search-other"')
  })

  it("calls onSearchOther when the search button is clicked (AC4)", () => {
    const onSearchOther = vi.fn()
    const buttons = captureButtonProps({
      candidates: THREE_CANDIDATES,
      onChoose: vi.fn(),
      onSearchOther,
    })
    const searchBtn = buttons.find(
      (b) => b["data-testid"] === "locate-confirm-search-other",
    )
    expect(searchBtn).toBeDefined()
    ;(searchBtn!["onClick"] as () => void)()
    expect(onSearchOther).toHaveBeenCalledTimes(1)
  })
})

// ---- State ------------------------------------------------------------------

describe("test_busy_disables_all_buttons", () => {
  it("every candidate button and the search button are disabled when busy=true (AC5)", () => {
    const html = renderView({
      candidates: THREE_CANDIDATES,
      onChoose: vi.fn(),
      onSearchOther: vi.fn(),
      busy: true,
    })
    // 3 candidates + 1 search button = 4 total; all must be disabled
    const disabledCount = (html.match(/disabled=""/g) ?? []).length
    expect(disabledCount).toBe(4)
  })
})

describe("test_error_renders_alert", () => {
  it("renders the error markup with role=alert when error is non-null (AC6)", () => {
    const html = renderView({
      candidates: THREE_CANDIDATES,
      onChoose: vi.fn(),
      error: "Something went wrong",
    })
    expect(html).toContain('role="alert"')
    expect(html).toContain('data-testid="locate-confirm-error"')
    expect(html).toContain("Something went wrong")
  })

  it("omits the error markup when error is null", () => {
    const html = renderView({
      candidates: THREE_CANDIDATES,
      onChoose: vi.fn(),
      error: null,
    })
    expect(html).not.toContain('data-testid="locate-confirm-error"')
  })
})

// ---- Non-breakage (existing usage) ------------------------------------------

describe("test_existing_clarifying_surface_view_tests_still_green", () => {
  it("existing choice-mode still renders candidate buttons (AC8)", () => {
    // Spot-check that the existing view export is unchanged after the append.
    // Full coverage lives in ClarifyingQuestionSurface.test.tsx.
    const html = renderToStaticMarkup(
      React.createElement(ClarifyingQuestionSurfaceView, {
        question: "List or grid?",
        choices: ["List", "Grid"],
        answer: "",
      }),
    )
    expect(html).toContain('data-testid="clarifying-question-choices"')
    expect(html).toContain("List")
    expect(html).toContain("Grid")
  })

  it("existing free-text mode still renders input + submit (AC8)", () => {
    const html = renderToStaticMarkup(
      React.createElement(ClarifyingQuestionSurfaceView, {
        question: "What tone?",
        choices: null,
        answer: "",
      }),
    )
    expect(html).toContain('data-testid="clarifying-question-input"')
    expect(html).toContain('data-testid="clarifying-question-submit"')
  })
})

describe("test_no_globals_css_change", () => {
  it("LocateConfirmView only uses existing clarifying-question-* class names (AC9)", () => {
    const src = readFileSync(
      join(
        process.cwd(),
        "app",
        "components",
        "design-agent",
        "ClarifyingQuestionSurface.tsx",
      ),
      "utf8",
    )
    // Check the appended section only (from the LocateConfirmCandidate type onward)
    const appended = src.slice(src.indexOf("export type LocateConfirmCandidate"))
    const classMatches = [...appended.matchAll(/className="([^"]+)"/g)].map(
      (m) => m[1],
    )
    for (const cls of classMatches) {
      for (const token of cls.split(" ")) {
        expect(
          token.startsWith("clarifying-question") || token === "error",
          `Unexpected class "${token}" in LocateConfirmView`,
        ).toBe(true)
      }
    }
  })
})

// ---- Integrity ---------------------------------------------------------------

describe("test_no_prohibited_tokens_in_appended_lines", () => {
  it("no internal ticket/decision IDs in the new view addition (AC10)", () => {
    const src = readFileSync(
      join(
        process.cwd(),
        "app",
        "components",
        "design-agent",
        "ClarifyingQuestionSurface.tsx",
      ),
      "utf8",
    )
    const appended = src.slice(src.indexOf("export type LocateConfirmCandidate"))
    // Check for common prohibited pattern families (ticket series, framework IDs)
    const seriesPattern = /[CPH]\d-\d/
    const adPattern = /\bAD\d/
    const fPattern = /\bF\d{1,2}\b/
    expect(seriesPattern.test(appended), "Found ticket-series ID in source append").toBe(false)
    expect(adPattern.test(appended), "Found AD-series token in source append").toBe(false)
    expect(fPattern.test(appended), "Found function-requirement token in source append").toBe(false)
  })
})
