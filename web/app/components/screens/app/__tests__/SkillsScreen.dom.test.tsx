// @vitest-environment jsdom
//
// Tests for the Skills gallery: it lists the routable skills from
// askApi.skills grouped by catalog category (in display order, unknown
// categories appended rather than dropped), and clicking a card hands off to
// the chat — setPendingOndemandDraft("<trigger> ") + goTo("chat") — so the
// composer opens pre-filled with the skill invoked. "Create or upload skill"
// is a coming-soon toast, not a navigation.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const skillsMock = vi.fn()
const goToMock = vi.fn()
const setPendingOndemandDraftMock = vi.fn()
const showToastMock = vi.fn()

vi.mock("../../../../lib/api", () => ({
  askApi: {
    skills: (...a: unknown[]) => skillsMock(...a),
  },
}))

vi.mock("../../../../context/NavigationContext", () => ({
  useNavigation: () => ({
    goTo: goToMock,
    setPendingOndemandDraft: setPendingOndemandDraftMock,
    showToast: showToastMock,
  }),
}))

// AppLayout drags in app contexts; the screen logic under test doesn't need it.
vi.mock("../AppLayout", () => ({
  AppLayout: ({ children }: { children: React.ReactNode }) =>
    React.createElement("div", null, children),
}))

import { SkillsScreen, skillBlurb, groupSkills } from "../SkillsScreen"
import type { SkillInfo } from "../../../../lib/api"

const STAKEHOLDER_MAP: SkillInfo = {
  id: "stakeholder-map",
  label: "Stakeholder map",
  trigger: "/stakeholder-map",
  description:
    'Map stakeholders and plan alignment, including RACI. Use when the user says "stakeholder map".',
  category: "Stakeholder & Communication",
}

const JOURNEY_MAP: SkillInfo = {
  id: "journey-map",
  label: "Journey map",
  trigger: "/journey-map",
  description: "Map a specific actor's end-to-end journey toward a goal.",
  category: "Discovery & Research",
}

const POSITIONING: SkillInfo = {
  id: "positioning",
  label: "Positioning",
  trigger: "/positioning",
  description: "Define product positioning and messaging.",
  category: "Strategy & Vision",
}

beforeEach(() => {
  // Deliberately NOT in display order — the screen must impose it.
  skillsMock.mockResolvedValue({ skills: [STAKEHOLDER_MAP, POSITIONING, JOURNEY_MAP] })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("SkillsScreen", () => {
  it("lists skills from askApi.skills grouped by category in display order", async () => {
    await act(async () => {
      render(React.createElement(SkillsScreen))
    })
    await waitFor(() => expect(skillsMock).toHaveBeenCalled())

    // Numbered category headings in catalog display order, not API order.
    const headings = screen.getAllByRole("heading").map((h) => h.textContent)
    expect(headings).toEqual([
      "1 · Discovery & Research",
      "2 · Strategy & Vision",
      "3 · Stakeholder & Communication",
    ])
    expect(screen.getByText("Journey map")).toBeTruthy()
    expect(screen.getByText("Positioning")).toBeTruthy()
    expect(screen.getByText("Stakeholder map")).toBeTruthy()
  })

  it("shows the first sentence of the description, without the router tail", async () => {
    await act(async () => {
      render(React.createElement(SkillsScreen))
    })
    await waitFor(() => expect(screen.getByText("Stakeholder map")).toBeTruthy())

    expect(
      screen.getByText("Map stakeholders and plan alignment, including RACI"),
    ).toBeTruthy()
    expect(screen.queryByText(/Use when the user says/)).toBeNull()
  })

  it("hands a clicked skill off to the chat with its trigger pre-filled", async () => {
    await act(async () => {
      render(React.createElement(SkillsScreen))
    })
    await waitFor(() => expect(screen.getByText("Stakeholder map")).toBeTruthy())

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /stakeholder map/i }))
    })

    expect(setPendingOndemandDraftMock).toHaveBeenCalledWith("/stakeholder-map ")
    expect(goToMock).toHaveBeenCalledWith("chat")
  })

  it("shows a coming-soon toast for Create or upload skill (no navigation)", async () => {
    await act(async () => {
      render(React.createElement(SkillsScreen))
    })
    await waitFor(() => expect(screen.getByText("Stakeholder map")).toBeTruthy())

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /create or upload skill/i }))
    })

    expect(showToastMock).toHaveBeenCalled()
    expect(goToMock).not.toHaveBeenCalled()
    expect(setPendingOndemandDraftMock).not.toHaveBeenCalled()
  })

  it("surfaces an error when loading fails", async () => {
    skillsMock.mockRejectedValueOnce(new Error("network down"))
    await act(async () => {
      render(React.createElement(SkillsScreen))
    })
    await waitFor(() => expect(screen.getByText(/network down/i)).toBeTruthy())
  })

  it("filters cards by search query, dropping empty categories and renumbering", async () => {
    await act(async () => {
      render(React.createElement(SkillsScreen))
    })
    await waitFor(() => expect(screen.getByText("Stakeholder map")).toBeTruthy())

    // "RACI" only appears in stakeholder-map's full description — search must
    // match the router description, not just the visible blurb.
    await act(async () => {
      fireEvent.change(screen.getByRole("searchbox", { name: /search skills/i }), {
        target: { value: "RACI" },
      })
    })

    expect(screen.getByText("Stakeholder map")).toBeTruthy()
    expect(screen.queryByText("Journey map")).toBeNull()
    expect(screen.queryByText("Positioning")).toBeNull()
    // Only one section remains and its number re-flows to 1.
    const headings = screen.getAllByRole("heading").map((h) => h.textContent)
    expect(headings).toEqual(["1 · Stakeholder & Communication"])
  })

  it("shows a no-match placeholder and restores the list when cleared", async () => {
    await act(async () => {
      render(React.createElement(SkillsScreen))
    })
    await waitFor(() => expect(screen.getByText("Stakeholder map")).toBeTruthy())
    const input = screen.getByRole("searchbox", { name: /search skills/i })

    await act(async () => {
      fireEvent.change(input, { target: { value: "zzz-nothing" } })
    })
    expect(screen.getByText(/No skills match/)).toBeTruthy()
    expect(screen.queryByText("Stakeholder map")).toBeNull()

    await act(async () => {
      fireEvent.change(input, { target: { value: "" } })
    })
    expect(screen.getByText("Stakeholder map")).toBeTruthy()
    expect(screen.getByText("Journey map")).toBeTruthy()
  })
})

describe("groupSkills", () => {
  it("appends unknown categories instead of dropping them", () => {
    const oddball: SkillInfo = {
      id: "future-skill",
      label: "Future skill",
      trigger: "/future-skill",
      description: "Does something new.",
      category: "Brand-New Category",
    }
    const groups = groupSkills([oddball, JOURNEY_MAP])
    expect(groups.map((g) => g.category)).toEqual([
      "Discovery & Research",
      "Brand-New Category",
    ])
  })
})

describe("skillBlurb", () => {
  it("cuts the router-guidance tail even mid-sentence flow", () => {
    expect(
      skillBlurb(
        'Map stakeholders and plan alignment, including RACI. Use when the user says "RACI".',
        "Stakeholder map",
      ),
    ).toBe("Map stakeholders and plan alignment, including RACI")
  })

  it("keeps a long first sentence intact (em-dashes are not sentence ends)", () => {
    expect(
      skillBlurb(
        "Map a specific actor's end-to-end journey toward a goal — phases, actions, thoughts. Use when asked.",
        "Journey map",
      ),
    ).toBe("Map a specific actor's end-to-end journey toward a goal — phases, actions, thoughts")
  })

  it("falls back to a generic line when the description is empty", () => {
    expect(skillBlurb("", "Roadmap")).toBe("Run the Roadmap workflow")
  })
})
