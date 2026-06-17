// LocateConfirmView — node-env vitest, no DOM, no testing-library.
// The view is SSR-rendered via renderToStaticMarkup for default-state markup
// assertions (renderToStaticMarkup honours useState's initial value, so the
// default lead/alternatives split renders deterministically). Click-handler
// wiring is verified by intercepting React.createElement to capture button
// props, then invoking the captured onClick directly. The click-to-PROMOTE
// interaction (which needs a state update + re-render) is covered separately in
// LocateConfirmPromote.test.tsx under a jsdom environment.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { afterEach, describe, expect, it, vi } from "vitest"

// Classic JSX runtime reads globalThis.React for createElement.
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import {
  LocateConfirmView,
  selectLeadAndAlternatives,
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
    rationale: "Where teammates are invited and their roles are managed.",
    is_top: true,
  },
  {
    id: "/dashboard",
    route: "/dashboard",
    entry_component: "DashboardPage",
    component_count: 7,
    rationale: "The home overview a user lands on after signing in.",
    is_top: false,
  },
  {
    id: "/settings",
    route: "/settings",
    entry_component: "SettingsPanel",
    component_count: 2,
    rationale: "Where account and workspace preferences are changed.",
    is_top: false,
  },
]

// ---- Lead card + alternatives layout ----------------------------------------

describe("suggested lead + alternative rows", () => {
  it("leads with the top candidate: name, full rationale, and a Use button", () => {
    const html = renderView({ candidates: THREE_CANDIDATES, onChoose: vi.fn() })
    // Lead is the is_top candidate (/team) — full description, no truncation.
    expect(html).toContain('data-testid="locate-suggested-badge"')
    expect(html).toContain("Suggested")
    expect(html).toContain('data-testid="locate-lead-name"')
    expect(html).toContain("Team")
    expect(html).toContain('data-testid="locate-confirm-use"')
    // The lead's full rationale is the load-bearing PM-facing narrative.
    expect(html).toContain('data-testid="locate-confirm-narrative"')
    expect(html).toContain(
      "Where teammates are invited and their roles are managed.",
    )
  })

  it("renders the other candidates as alt rows (count = candidates - 1)", () => {
    const html = renderView({ candidates: THREE_CANDIDATES, onChoose: vi.fn() })
    const altCount = (html.match(/data-testid="locate-alt-row"/g) ?? []).length
    expect(altCount).toBe(THREE_CANDIDATES.length - 1)
    // Alternative descriptions (CSS-truncated) are present in the markup.
    expect(html).toContain("The home overview a user lands on after signing in.")
    expect(html).toContain("Where account and workspace preferences are changed.")
    expect(html).toContain('data-testid="locate-others-label"')
  })

  it("surfaces the lead's route + component count as a demoted secondary line", () => {
    const html = renderView({ candidates: THREE_CANDIDATES, onChoose: vi.fn() })
    expect(html).toContain('data-testid="locate-confirm-route-info"')
    expect(html).toContain("/team")
    expect(html).toContain("3 components")
  })

  it("omits the lead narrative line when rationale is empty, keeping the name", () => {
    const html = renderView({
      candidates: [
        {
          id: "/team",
          route: "/team",
          entry_component: "TeamScreen",
          component_count: 3,
          rationale: "",
          is_top: false,
        },
      ],
      onChoose: vi.fn(),
    })
    expect(html).not.toContain('data-testid="locate-confirm-narrative"')
    expect(html).toContain("Team")
  })
})

describe("single-candidate edge case", () => {
  it("renders only the lead — no Other options label and no alt rows", () => {
    const html = renderView({
      candidates: [THREE_CANDIDATES[0]!],
      onChoose: vi.fn(),
    })
    expect(html).toContain('data-testid="locate-confirm-use"')
    expect(html).not.toContain('data-testid="locate-others-label"')
    expect(html).not.toContain('data-testid="locate-alt-row"')
  })
})

// ---- Pure split helper ------------------------------------------------------

describe("selectLeadAndAlternatives", () => {
  it("promotes the candidate whose id matches promotedId; rest keep order", () => {
    const { lead, alternatives } = selectLeadAndAlternatives(
      THREE_CANDIDATES,
      "/settings",
    )
    expect(lead?.id).toBe("/settings")
    expect(alternatives.map((c) => c.id)).toEqual(["/team", "/dashboard"])
  })

  it("falls back to the is_top candidate when promotedId matches nothing", () => {
    const { lead } = selectLeadAndAlternatives(THREE_CANDIDATES, "/nope")
    expect(lead?.id).toBe("/team")
  })

  it("falls back to index 0 when no candidate is is_top", () => {
    const noTop = THREE_CANDIDATES.map((c) => ({ ...c, is_top: false }))
    const { lead } = selectLeadAndAlternatives(noTop, "/nope")
    expect(lead?.id).toBe("/team")
  })

  it("returns lead null + empty alternatives for an empty list", () => {
    const { lead, alternatives } = selectLeadAndAlternatives([], "/x")
    expect(lead).toBeNull()
    expect(alternatives).toEqual([])
  })
})

describe("default question text when omitted", () => {
  it("renders the default question text when question prop is omitted", () => {
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

describe("label derived from entry component with route fallback", () => {
  it("strips a trailing Screen suffix and returns the base word", () => {
    const html = renderView({
      candidates: [
        {
          id: "/team",
          route: "/team",
          entry_component: "TeamScreen",
          component_count: 1,
          rationale: "Manage teammates and roles.",
          is_top: false,
        },
      ],
      onChoose: vi.fn(),
    })
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
          rationale: "Read the weekly briefing.",
          is_top: false,
        },
      ],
      onChoose: vi.fn(),
    })
    expect(html).toContain("Briefing")
    expect(html).not.toContain("BriefingPage")
  })

  it("falls back to the raw route when entry_component yields an empty label", () => {
    const html = renderView({
      candidates: [
        {
          id: "/team",
          route: "/team",
          entry_component: "",
          component_count: 1,
          rationale: "Manage teammates and roles.",
          is_top: false,
        },
      ],
      onChoose: vi.fn(),
    })
    const labelMatch = html.match(
      /data-testid="locate-lead-name">(.*?)<\/div>/,
    )
    expect(labelMatch?.[1]).toBe("/team")
  })
})

// ---- Interaction (default lead) ---------------------------------------------

describe("Use this screen confirms the default lead", () => {
  it("clicking Use calls onChoose with the top candidate's route AND id", () => {
    const onChoose = vi.fn()
    const buttons = captureButtonProps({ candidates: THREE_CANDIDATES, onChoose })
    const useBtn = buttons.find(
      (b) => b["data-testid"] === "locate-confirm-use",
    )
    expect(useBtn).toBeDefined()
    ;(useBtn!["onClick"] as () => void)()
    expect(onChoose).toHaveBeenCalledTimes(1)
    expect(onChoose).toHaveBeenCalledWith("/team", "/team")
  })

  it("clicking an alt row does NOT call onChoose (promote-only)", () => {
    const onChoose = vi.fn()
    const buttons = captureButtonProps({ candidates: THREE_CANDIDATES, onChoose })
    const altRows = buttons.filter(
      (b) => b["data-testid"] === "locate-alt-row",
    )
    expect(altRows.length).toBe(2)
    for (const row of altRows) {
      ;(row["onClick"] as () => void)()
    }
    expect(onChoose).not.toHaveBeenCalled()
  })
})

describe("search other conditional render and callback", () => {
  it("renders the search button when onSearchOther is provided", () => {
    const html = renderView({
      candidates: THREE_CANDIDATES,
      onChoose: vi.fn(),
      onSearchOther: vi.fn(),
    })
    expect(html).toContain('data-testid="locate-confirm-search-other"')
    expect(html).toContain("Search for another screen")
  })

  it("omits the search button when onSearchOther is undefined", () => {
    const html = renderView({ candidates: THREE_CANDIDATES, onChoose: vi.fn() })
    expect(html).not.toContain('data-testid="locate-confirm-search-other"')
  })

  it("calls onSearchOther when the search button is clicked", () => {
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

describe("busy disables the Use button, every alt row, and search", () => {
  it("all actionable buttons are disabled when busy=true", () => {
    const html = renderView({
      candidates: THREE_CANDIDATES,
      onChoose: vi.fn(),
      onSearchOther: vi.fn(),
      busy: true,
    })
    // Use (1) + alt rows (2) + search (1) = 4 disabled controls.
    const disabledCount = (html.match(/disabled=""/g) ?? []).length
    expect(disabledCount).toBe(4)
  })
})

describe("error renders alert", () => {
  it("renders the error markup with role=alert when error is non-null", () => {
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

describe("existing clarifying surface view tests still green", () => {
  it("existing choice-mode still renders candidate buttons", () => {
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

  it("existing free-text mode still renders input + submit", () => {
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

describe("no globals css change", () => {
  it("LocateConfirmView only uses scoped picker classes (no new global class)", () => {
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
    // Check the appended section only (from the LocateConfirmCandidate type onward).
    const appended = src.slice(src.indexOf("export type LocateConfirmCandidate"))
    const classMatches = [...appended.matchAll(/className="([^"]+)"/g)].map(
      (m) => m[1],
    )
    // Allowed: the pre-existing scoped clarifying-question-* family, the new
    // scoped picker classes (locate-*, styled in the component-scoped
    // design-agent.css — NOT globals.css), the pre-existing global button
    // classes (btn / btn-accent), and the shared "error" utility. Any other
    // token would imply a brand-new global class requiring a globals.css edit.
    const allowed = (token: string) =>
      token.startsWith("clarifying-question") ||
      token.startsWith("locate") ||
      token === "error" ||
      token === "btn" ||
      token === "btn-accent"
    for (const cls of classMatches) {
      for (const token of cls.split(" ")) {
        expect(allowed(token), `Unexpected class "${token}" in LocateConfirmView`).toBe(
          true,
        )
      }
    }
  })
})

// ---- Integrity ---------------------------------------------------------------

describe("no prohibited tokens in appended lines", () => {
  it("no internal ticket/decision IDs in the new view addition", () => {
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
    const seriesPattern = /[CPH]\d-\d/
    const adPattern = /\bAD\d/
    const fPattern = /\bF\d{1,2}\b/
    expect(seriesPattern.test(appended), "Found ticket-series ID in source append").toBe(false)
    expect(adPattern.test(appended), "Found AD-series token in source append").toBe(false)
    expect(fPattern.test(appended), "Found function-requirement token in source append").toBe(false)
  })
})
