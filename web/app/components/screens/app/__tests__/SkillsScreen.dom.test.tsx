// @vitest-environment jsdom
//
// Tests for the Skills gallery: it lists the company's CUSTOM skills from
// skillsApi.list (uploader byline). Clicking a card hands off to the chat —
// setPendingOndemandDraft("<trigger> ") + goTo("chat") — so the composer opens
// pre-filled with the skill invoked. "Create or upload skill" opens the upload
// modal; a successful upload prepends the new skill.
//
// The BUILT-IN catalog this screen used to list alongside them (askApi.skills,
// grouped by category) is gone, and so are its tests + `groupSkills`. Their
// subject went with the built-in skill layer: chat selects no vendored method,
// so every one of those cards was a trigger that would have done nothing. The
// custom-skill tests below are untouched — that feature is the whole screen
// now.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const customListMock = vi.fn()
const customUploadMock = vi.fn()
const customRemoveMock = vi.fn()
const customGetMock = vi.fn()
const customUpdateMock = vi.fn()
const goToMock = vi.fn()
const setPendingOndemandDraftMock = vi.fn()
const showToastMock = vi.fn()

vi.mock("../../../../lib/api", () => ({
  skillsApi: {
    list: (...a: unknown[]) => customListMock(...a),
    upload: (...a: unknown[]) => customUploadMock(...a),
    remove: (...a: unknown[]) => customRemoveMock(...a),
    get: (...a: unknown[]) => customGetMock(...a),
    update: (...a: unknown[]) => customUpdateMock(...a),
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

import { SkillsScreen, skillBlurb } from "../SkillsScreen"

const CUSTOM_SKILL = {
  id: "b8f3a1c2-0000-0000-0000-000000000001",
  slug: "estimation-helper",
  trigger: "/estimation-helper",
  name: "Estimation helper",
  description:
    'Scores features by reach × confidence. Use when the user says "estimate".',
  uploader_name: "Fortune Tede",
  created_at: "2026-07-28T18:00:00+00:00",
  has_file: true,
  name_conflict: false,
}

// A second upload, so search/filter behaviour has something to discriminate.
const OTHER_SKILL = {
  ...CUSTOM_SKILL,
  id: "b8f3a1c2-0000-0000-0000-000000000002",
  slug: "journey-mapper",
  trigger: "/journey-mapper",
  name: "Journey mapper",
  description: "Maps an actor's end-to-end journey toward a goal.",
  uploader_name: "Ada Lovelace",
}

/** GET /v1/skills/{id} — the list is metadata-only, so the edit form's method
 *  text comes from the detail route. */
const CUSTOM_SKILL_DETAIL = {
  ...CUSTOM_SKILL,
  method: "# Estimation method\nScore by reach x confidence.\n",
  modules: [] as string[],
  references: [] as string[],
  attached_chars: 0,
}

beforeEach(() => {
  customListMock.mockResolvedValue({ skills: [CUSTOM_SKILL, OTHER_SKILL] })
  customGetMock.mockResolvedValue(CUSTOM_SKILL_DETAIL)
  searchParamsMock = new URLSearchParams()
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("SkillsScreen", () => {
  it("lists the company's uploaded skills", async () => {
    await act(async () => {
      render(React.createElement(SkillsScreen))
    })
    await waitFor(() => expect(customListMock).toHaveBeenCalled())

    expect(screen.getByRole("heading", { name: "Custom skills" })).toBeTruthy()
    expect(screen.getByText("Estimation helper")).toBeTruthy()
    expect(screen.getByText("Journey mapper")).toBeTruthy()
  })

  it("shows the first sentence of the description, without the routing tail", async () => {
    await act(async () => {
      render(React.createElement(SkillsScreen))
    })
    await waitFor(() => expect(screen.getByText("Estimation helper")).toBeTruthy())

    expect(screen.getByText("Scores features by reach × confidence")).toBeTruthy()
    expect(screen.queryByText(/Use when the user says/)).toBeNull()
  })

  it("opens the upload modal from Create or upload skill (no navigation)", async () => {
    await act(async () => {
      render(React.createElement(SkillsScreen))
    })
    await waitFor(() => expect(screen.getByText("Estimation helper")).toBeTruthy())

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

  it("lists two same-named uploads under the triggers that invoke each", async () => {
    // No-override (PRD 1854 revision): an upload replaces nothing and takes the
    // next free trigger, so BOTH cards belong in the library. Still true with
    // the built-in catalog gone — the collision this covers is now
    // upload-vs-upload as much as upload-vs-built-in.
    customListMock.mockResolvedValue({
      skills: [
        OTHER_SKILL,
        { ...OTHER_SKILL, id: "c-shadow", slug: "journey-mapper-2",
          trigger: "/journey-mapper-2", name_conflict: true },
      ],
    })
    await act(async () => {
      render(React.createElement(SkillsScreen))
    })
    await waitFor(() => expect(screen.getAllByText("Journey mapper").length).toBe(2))

    expect(screen.getByTitle(/^\/journey-mapper —/)).toBeTruthy()
    expect(screen.getByTitle(/^\/journey-mapper-2 —/)).toBeTruthy()
  })

  it("toasts the assigned trigger when the uploaded name was taken", async () => {
    // A BUILT-IN's name: nothing is replaced, so this is a NEW skill with its
    // own id and a disambiguated trigger.
    customUploadMock.mockResolvedValue({
      ...CUSTOM_SKILL,
      id: "b8f3a1c2-0000-0000-0000-000000000003",
      slug: "prd-author-2",
      trigger: "/prd-author-2",
      name: "PRD Author",
      name_conflict: true,
      replaced: false,
    })
    const { container } = render(React.createElement(SkillsScreen))
    await waitFor(() => expect(screen.getByText("Estimation helper")).toBeTruthy())

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /create or upload skill/i }))
    })
    await act(async () => {
      fireEvent.change(screen.getByLabelText(/skill name/i), {
        target: { value: "PRD Author" },
      })
      fireEvent.change(screen.getByLabelText(/what does this skill do/i), {
        target: { value: "Ours." },
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

    await waitFor(() =>
      expect(showToastMock).toHaveBeenCalledWith(
        "Skill uploaded",
        expect.stringContaining("/prd-author-2"),
      ),
    )
    // …and it says the skill that owned the name is still there.
    expect(showToastMock.mock.calls.at(-1)?.[1]).toMatch(/still works too/i)
  })

  it("swaps the card in place when the upload replaced one of our own skills", async () => {
    // Re-uploading a name the company already used updates that row: same id,
    // same trigger, new content. The library must show ONE card (prepending
    // would render the same id twice) and the toast must say "updated".
    customListMock.mockResolvedValue({ skills: [CUSTOM_SKILL, OTHER_SKILL] })
    customUploadMock.mockResolvedValue({
      ...CUSTOM_SKILL,
      description: "Scores features by reach × confidence, v2.",
      replaced: true,
    })
    const { container } = render(React.createElement(SkillsScreen))
    await waitFor(() => expect(screen.getByText("Estimation helper")).toBeTruthy())

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /create or upload skill/i }))
    })
    await act(async () => {
      fireEvent.change(screen.getByLabelText(/skill name/i), {
        target: { value: "Estimation helper" },
      })
      fireEvent.change(screen.getByLabelText(/what does this skill do/i), {
        target: { value: "Ours, v2." },
      })
    })
    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement
    await act(async () => {
      fireEvent.change(fileInput, {
        target: { files: [new File(["# method v2"], "skill.md", { type: "text/markdown" })] },
      })
    })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^upload skill$/i }))
    })

    await waitFor(() =>
      expect(showToastMock).toHaveBeenCalledWith(
        "Skill updated",
        expect.stringContaining("/estimation-helper"),
      ),
    )
    // One card for that skill, still first, now showing the new description.
    expect(screen.getAllByText("Estimation helper").length).toBe(1)
    expect(
      screen.getByText("Scores features by reach × confidence, v2"),
    ).toBeTruthy()
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

  it("opens the edit modal from the card's pencil, pre-filled from the detail route", async () => {
    customListMock.mockResolvedValue({ skills: [CUSTOM_SKILL] })
    await act(async () => {
      render(React.createElement(SkillsScreen))
    })
    await waitFor(() => expect(screen.getByText("Estimation helper")).toBeTruthy())

    // Every custom card carries the pencil, paired with the delete icon.
    expect(screen.getByRole("button", { name: "Delete Estimation helper" })).toBeTruthy()
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Edit Estimation helper" }))
    })

    // Opening the editor invokes nothing and deletes nothing.
    expect(goToMock).not.toHaveBeenCalled()
    expect(customRemoveMock).not.toHaveBeenCalled()
    expect(customGetMock).toHaveBeenCalledWith(CUSTOM_SKILL.id)
    await waitFor(() =>
      expect(
        (screen.getByLabelText(/^method/i) as HTMLTextAreaElement).value,
      ).toBe(CUSTOM_SKILL_DETAIL.method),
    )
    expect((screen.getByLabelText(/skill name/i) as HTMLInputElement).value).toBe(
      "Estimation helper",
    )
  })

  it("saves an edit and updates the card in place, with the new trigger", async () => {
    customListMock.mockResolvedValue({ skills: [CUSTOM_SKILL, OTHER_SKILL] })
    // A rename: the server re-derives the trigger, and its answer is what the
    // card must show — not anything the client guessed.
    customUpdateMock.mockResolvedValue({
      ...CUSTOM_SKILL_DETAIL,
      name: "Sizing guide",
      slug: "sizing-guide",
      trigger: "/sizing-guide",
      description: "Sizes work against our template.",
      replaced_skill_id: null,
    })
    await act(async () => {
      render(React.createElement(SkillsScreen))
    })
    await waitFor(() => expect(screen.getByText("Estimation helper")).toBeTruthy())

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Edit Estimation helper" }))
    })
    await waitFor(() => expect(screen.getByLabelText(/^method/i)).toBeTruthy())
    await act(async () => {
      fireEvent.change(screen.getByLabelText(/skill name/i), {
        target: { value: "Sizing guide" },
      })
      fireEvent.change(screen.getByLabelText(/what does this skill do/i), {
        target: { value: "Sizes work against our template." },
      })
    })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /save changes/i }))
    })

    await waitFor(() =>
      expect(customUpdateMock).toHaveBeenCalledWith(CUSTOM_SKILL.id, {
        name: "Sizing guide",
        description: "Sizes work against our template.",
        method: CUSTOM_SKILL_DETAIL.method,
      }),
    )
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
    // The card is the same one, renamed — the other skill is untouched.
    expect(screen.getByText("Sizing guide")).toBeTruthy()
    expect(screen.queryByText("Estimation helper")).toBeNull()
    expect(screen.getByText("Journey mapper")).toBeTruthy()
    expect(screen.getByTitle(/^\/sizing-guide —/)).toBeTruthy()
    expect(showToastMock).toHaveBeenCalledWith(
      "Skill updated",
      expect.stringContaining("/sizing-guide"),
    )
  })

  it("drops the replaced card when a rename absorbed another skill", async () => {
    customListMock.mockResolvedValue({ skills: [CUSTOM_SKILL, OTHER_SKILL] })
    customUpdateMock.mockResolvedValue({
      ...CUSTOM_SKILL_DETAIL,
      name: "Journey mapper",
      slug: "journey-mapper",
      trigger: "/journey-mapper",
      replaced_skill_id: OTHER_SKILL.id,
    })
    await act(async () => {
      render(React.createElement(SkillsScreen))
    })
    await waitFor(() => expect(screen.getByText("Journey mapper")).toBeTruthy())

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Edit Estimation helper" }))
    })
    await waitFor(() => expect(screen.getByLabelText(/^method/i)).toBeTruthy())
    await act(async () => {
      fireEvent.change(screen.getByLabelText(/skill name/i), {
        target: { value: "Journey mapper" },
      })
    })

    // Destructive: the first click only arms the confirm.
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /save changes/i }))
    })
    expect(customUpdateMock).not.toHaveBeenCalled()
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /replace and save/i }))
    })

    await waitFor(() => expect(customUpdateMock).toHaveBeenCalled())
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
    // One card left: the edited one, under the absorbed skill's name and
    // trigger. The replaced row is gone server-side, so its card goes too.
    expect(screen.getAllByText("Journey mapper").length).toBe(1)
    expect(screen.queryByText("Estimation helper")).toBeNull()
    expect(screen.queryByText("Ada Lovelace")).toBeNull()
    expect(showToastMock).toHaveBeenCalledWith(
      "Skill updated",
      expect.stringContaining("replaced your other skill of the same name"),
    )
  })

  it("shows the failure in the modal when the skill's detail can't be loaded", async () => {
    customListMock.mockResolvedValue({ skills: [CUSTOM_SKILL] })
    customGetMock.mockRejectedValue(new Error("Skill not found."))
    await act(async () => {
      render(React.createElement(SkillsScreen))
    })
    await waitFor(() => expect(screen.getByText("Estimation helper")).toBeTruthy())

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Edit Estimation helper" }))
    })

    await waitFor(() =>
      expect(screen.getByRole("dialog", { name: /edit skill/i })).toBeTruthy(),
    )
    expect(screen.getByRole("alert").textContent).toBe("Skill not found.")
    // The card is still there, unchanged — nothing was written.
    expect(screen.getByText("Estimation helper")).toBeTruthy()
  })

  it("surfaces the failure inline when the skills fetch fails", async () => {
    customListMock.mockRejectedValueOnce(new Error("custom down"))
    await act(async () => {
      render(React.createElement(SkillsScreen))
    })
    await waitFor(() =>
      expect(screen.getByText(/Custom skills couldn’t load/)).toBeTruthy(),
    )

    // ...and the surface still offers the way out of an empty library.
    expect(screen.getByRole("button", { name: /create or upload skill/i })).toBeTruthy()
  })

  it("uploads a skill through the modal and prepends it to the library", async () => {
    // Starts from an EMPTY library so "it's in the list now" is unambiguous —
    // uploading a skill whose name is already on screen proves nothing.
    customListMock.mockResolvedValue({ skills: [] })
    customUploadMock.mockResolvedValue(CUSTOM_SKILL)
    const { container } = render(React.createElement(SkillsScreen))
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /create or upload skill/i })).toBeTruthy(),
    )

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

  it("filters cards by search query, over name / trigger / description", async () => {
    await act(async () => {
      render(React.createElement(SkillsScreen))
    })
    await waitFor(() => expect(screen.getByText("Estimation helper")).toBeTruthy())

    // "reach" only appears in Estimation helper's FULL description — search
    // must match the stored description, not just the visible blurb.
    await act(async () => {
      fireEvent.change(screen.getByRole("searchbox", { name: /search skills/i }), {
        target: { value: "reach" },
      })
    })

    expect(screen.getByText("Estimation helper")).toBeTruthy()
    expect(screen.queryByText("Journey mapper")).toBeNull()
  })

  it("seeds the filter from the ?q= deep link (global search palette)", async () => {
    searchParamsMock = new URLSearchParams("q=Journey mapper")
    await act(async () => {
      render(React.createElement(SkillsScreen))
    })
    await waitFor(() => expect(screen.getByText("Journey mapper")).toBeTruthy())

    const input = screen.getByRole("searchbox", { name: /search skills/i }) as HTMLInputElement
    expect(input.value).toBe("Journey mapper")
    expect(screen.queryByText("Estimation helper")).toBeNull()
  })

  it("shows a no-match placeholder and restores the list when cleared", async () => {
    await act(async () => {
      render(React.createElement(SkillsScreen))
    })
    await waitFor(() => expect(screen.getByText("Estimation helper")).toBeTruthy())
    const input = screen.getByRole("searchbox", { name: /search skills/i })

    await act(async () => {
      fireEvent.change(input, { target: { value: "zzz-nothing" } })
    })
    expect(screen.getByText(/No skills match/)).toBeTruthy()
    expect(screen.queryByText("Estimation helper")).toBeNull()

    await act(async () => {
      fireEvent.change(input, { target: { value: "" } })
    })
    expect(screen.getByText("Estimation helper")).toBeTruthy()
    expect(screen.getByText("Journey mapper")).toBeTruthy()
  })

  it("invites an upload when the library is empty", async () => {
    customListMock.mockResolvedValue({ skills: [] })
    await act(async () => {
      render(React.createElement(SkillsScreen))
    })
    await waitFor(() =>
      expect(screen.getByText(/No skills yet — upload one to get started\./)).toBeTruthy(),
    )
  })

})

describe("skillBlurb", () => {
  it("cuts the routing-guidance tail even mid-sentence flow", () => {
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
