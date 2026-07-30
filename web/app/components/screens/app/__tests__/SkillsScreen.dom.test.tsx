// @vitest-environment jsdom
//
// Tests for the Skills gallery: it lists the routable skills from
// askApi.skills grouped by catalog category (in display order, unknown
// categories appended rather than dropped), plus the company's CUSTOM skills
// from skillsApi.list (own section, uploader byline). Clicking any card hands
// off to the chat — setPendingOndemandDraft("<trigger> ") + goTo("chat") — so
// the composer opens pre-filled with the skill invoked. "Create or upload
// skill" opens the upload modal; a successful upload prepends the new skill.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const skillsMock = vi.fn()
const customListMock = vi.fn()
const customUploadMock = vi.fn()
const customRemoveMock = vi.fn()
const goToMock = vi.fn()
const setPendingOndemandDraftMock = vi.fn()
const showToastMock = vi.fn()

vi.mock("../../../../lib/api", () => ({
  askApi: {
    skills: (...a: unknown[]) => skillsMock(...a),
  },
  skillsApi: {
    list: (...a: unknown[]) => customListMock(...a),
    upload: (...a: unknown[]) => customUploadMock(...a),
    remove: (...a: unknown[]) => customRemoveMock(...a),
  },
}))

vi.mock("../../../../context/NavigationContext", () => ({
  useNavigation: () => ({
    goTo: goToMock,
    setPendingOndemandDraft: setPendingOndemandDraftMock,
    showToast: showToastMock,
  }),
}))

// The screen reads the `?q=` deep-link param (global search palette) via
// useSearchParams; swap the URL by reassigning this between renders.
let searchParamsMock = new URLSearchParams()
vi.mock("next/navigation", () => ({
  useSearchParams: () => searchParamsMock,
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

const CUSTOM_SKILL = {
  id: "b8f3a1c2-0000-0000-0000-000000000001",
  slug: "estimation-helper",
  trigger: "/estimation-helper",
  name: "Estimation helper",
  description: "Scores features by reach × confidence.",
  uploader_name: "Fortune Tede",
  created_at: "2026-07-28T18:00:00+00:00",
  has_file: true,
}

beforeEach(() => {
  // Deliberately NOT in display order — the screen must impose it.
  skillsMock.mockResolvedValue({ skills: [STAKEHOLDER_MAP, POSITIONING, JOURNEY_MAP] })
  customListMock.mockResolvedValue({ skills: [] })
  searchParamsMock = new URLSearchParams()
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

  it("opens the upload modal from Create or upload skill (no navigation)", async () => {
    await act(async () => {
      render(React.createElement(SkillsScreen))
    })
    await waitFor(() => expect(screen.getByText("Stakeholder map")).toBeTruthy())

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /create or upload skill/i }))
    })

    expect(screen.getByRole("dialog", { name: /upload a custom skill/i })).toBeTruthy()
    expect(goToMock).not.toHaveBeenCalled()
    expect(setPendingOndemandDraftMock).not.toHaveBeenCalled()
  })

  it("renders custom skills in their own section with the uploader byline", async () => {
    customListMock.mockResolvedValue({ skills: [CUSTOM_SKILL] })
    await act(async () => {
      render(React.createElement(SkillsScreen))
    })
    await waitFor(() => expect(screen.getByText("Estimation helper")).toBeTruthy())

    expect(screen.getByRole("heading", { name: "Custom skills" })).toBeTruthy()
    expect(screen.getByText("Fortune Tede")).toBeTruthy()
    // Custom cards hand off to chat exactly like built-ins. Anchored regex:
    // the delete affordance is also a button whose name CONTAINS the skill
    // name ("Delete Estimation helper") — the card's name starts with it.
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^estimation helper/i }))
    })
    expect(setPendingOndemandDraftMock).toHaveBeenCalledWith("/estimation-helper ")
    expect(goToMock).toHaveBeenCalledWith("chat")
  })

  it("deletes a custom skill only through the inline confirm, with an in-flight state", async () => {
    customListMock.mockResolvedValue({ skills: [CUSTOM_SKILL] })
    // Deferred resolution so the Deleting… state is observable mid-flight.
    let resolveRemove!: (v: { deleted: true; id: string }) => void
    customRemoveMock.mockReturnValue(
      new Promise((res) => {
        resolveRemove = res
      }),
    )
    await act(async () => {
      render(React.createElement(SkillsScreen))
    })
    await waitFor(() => expect(screen.getByText("Estimation helper")).toBeTruthy())

    // Arming the confirm deletes nothing and doesn't invoke the skill.
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Delete Estimation helper" }))
    })
    expect(customRemoveMock).not.toHaveBeenCalled()
    expect(goToMock).not.toHaveBeenCalled()
    expect(screen.getByText(/Delete for the whole company\?/)).toBeTruthy()

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^delete$/i }))
    })

    // In flight: the confirm is gone, a live Deleting… status shows, and the
    // card is still present until the server confirms.
    expect(customRemoveMock).toHaveBeenCalledWith(CUSTOM_SKILL.id)
    expect(screen.getByRole("status").textContent).toContain("Deleting…")
    expect(screen.getByText("Estimation helper")).toBeTruthy()

    await act(async () => {
      resolveRemove({ deleted: true, id: CUSTOM_SKILL.id })
    })

    await waitFor(() => expect(screen.queryByText("Estimation helper")).toBeNull())
    expect(showToastMock).toHaveBeenCalledWith(
      "Skill deleted",
      expect.stringContaining("Estimation helper"),
    )
  })

  it("cancelling the delete confirm keeps the skill and calls nothing", async () => {
    customListMock.mockResolvedValue({ skills: [CUSTOM_SKILL] })
    await act(async () => {
      render(React.createElement(SkillsScreen))
    })
    await waitFor(() => expect(screen.getByText("Estimation helper")).toBeTruthy())

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Delete Estimation helper" }))
    })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /cancel/i }))
    })

    expect(customRemoveMock).not.toHaveBeenCalled()
    expect(screen.getByText("Estimation helper")).toBeTruthy()
    expect(screen.queryByText(/Delete for the whole company\?/)).toBeNull()
  })

  it("keeps the card and surfaces a toast when the delete fails", async () => {
    customListMock.mockResolvedValue({ skills: [CUSTOM_SKILL] })
    customRemoveMock.mockRejectedValue(new Error("Skill not found."))
    await act(async () => {
      render(React.createElement(SkillsScreen))
    })
    await waitFor(() => expect(screen.getByText("Estimation helper")).toBeTruthy())

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Delete Estimation helper" }))
    })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^delete$/i }))
    })

    await waitFor(() =>
      expect(showToastMock).toHaveBeenCalledWith(
        "Couldn't delete the skill",
        "Skill not found.",
      ),
    )
    expect(screen.getByText("Estimation helper")).toBeTruthy()
  })

  it("keeps built-ins rendering when the custom-skills fetch fails", async () => {
    customListMock.mockRejectedValueOnce(new Error("custom down"))
    await act(async () => {
      render(React.createElement(SkillsScreen))
    })
    await waitFor(() => expect(screen.getByText("Stakeholder map")).toBeTruthy())

    expect(screen.getByText(/Custom skills couldn’t load/)).toBeTruthy()
    expect(screen.getByText("Journey map")).toBeTruthy()
  })

  it("uploads a skill through the modal and prepends it to the library", async () => {
    customUploadMock.mockResolvedValue(CUSTOM_SKILL)
    const { container } = render(React.createElement(SkillsScreen))
    await waitFor(() => expect(screen.getByText("Stakeholder map")).toBeTruthy())

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /create or upload skill/i }))
    })
    await act(async () => {
      fireEvent.change(screen.getByLabelText(/skill name/i), {
        target: { value: "Estimation helper" },
      })
      fireEvent.change(screen.getByLabelText(/what does this skill do/i), {
        target: { value: "Scores features by reach × confidence." },
      })
    })
    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement
    await act(async () => {
      fireEvent.change(fileInput, {
        target: { files: [new File(["# method"], "skill.md", { type: "text/markdown" })] },
      })
    })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^upload skill$/i }))
    })

    // waitFor: the modal's .md content pre-check reads the file (FileReader)
    // before calling upload, so the call lands a tick after the click.
    await waitFor(() =>
      expect(customUploadMock).toHaveBeenCalledWith(
        expect.any(File),
        "Estimation helper",
        "Scores features by reach × confidence.",
      ),
    )
    // Modal closes, toast fires, and the new skill is in the library.
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
    expect(showToastMock).toHaveBeenCalled()
    expect(screen.getByText("Fortune Tede")).toBeTruthy()
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

  it("seeds the filter from the ?q= deep link (global search palette)", async () => {
    searchParamsMock = new URLSearchParams("q=Journey map")
    await act(async () => {
      render(React.createElement(SkillsScreen))
    })
    await waitFor(() => expect(screen.getByText("Journey map")).toBeTruthy())

    const input = screen.getByRole("searchbox", { name: /search skills/i }) as HTMLInputElement
    expect(input.value).toBe("Journey map")
    expect(screen.queryByText("Stakeholder map")).toBeNull()
    expect(screen.queryByText("Positioning")).toBeNull()
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
