// @vitest-environment jsdom
//
// EditSkillModal: editing a custom skill in place — name, description and the
// method text, no file. Covers what the form owes the user beyond the fields:
//   - it pre-fills from the loaded skill, and shows its own loading/failure
//     states while the method text is in flight
//   - a rename says the current trigger will stop working
//   - a rename onto ANOTHER of the company's skills is destructive, so it is
//     gated behind an explicit two-step confirm and never saves on one click
//   - the 50,000-character cap counts a .zip skill's attached files too
//   - a failed save keeps every input intact so the user can retry
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { EditSkillModal, replacementTarget } from "../EditSkillModal"
import { MAX_SKILL_CONTENT_CHARS } from "../UploadSkillModal"

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

const SKILL = {
  id: "skill-1",
  slug: "estimation-helper",
  trigger: "/estimation-helper",
  name: "Estimation helper",
  description: "Scores features by reach × confidence.",
  uploader_name: "Dana Whitfield",
  created_at: "2026-07-28T18:00:00+00:00",
  has_file: true,
  name_conflict: false,
  method: "# Estimation method\nScore by reach x confidence.\n",
  modules: [] as string[],
  references: [] as string[],
  attached_chars: 0,
}

function renderModal(over: Partial<React.ComponentProps<typeof EditSkillModal>> = {}) {
  const onSave = vi.fn().mockResolvedValue(undefined)
  const onClose = vi.fn()
  const utils = render(
    React.createElement(EditSkillModal, {
      open: true,
      skill: SKILL,
      loading: false,
      loadError: null,
      others: [],
      onSave,
      onClose,
      ...over,
    }),
  )
  return { ...utils, onSave, onClose }
}

describe("replacementTarget", () => {
  it("matches on the slugified name, like the server does", () => {
    const others = [{ id: "b", slug: "prd-author-2", name: "PRD Author" }]
    expect(replacementTarget("PRD  author!", others)?.id).toBe("b")
    expect(replacementTarget("Something else", others)).toBeUndefined()
    // A name with nothing sluggable matches nothing rather than everything.
    expect(replacementTarget("!!!", others)).toBeUndefined()
  })
})

describe("EditSkillModal", () => {
  it("pre-fills name, description and the method text", () => {
    renderModal()
    expect((screen.getByLabelText(/skill name/i) as HTMLInputElement).value).toBe(
      "Estimation helper",
    )
    expect(
      (screen.getByLabelText(/what does this skill do/i) as HTMLTextAreaElement).value,
    ).toBe("Scores features by reach × confidence.")
    expect((screen.getByLabelText(/^method/i) as HTMLTextAreaElement).value).toBe(
      "# Estimation method\nScore by reach x confidence.\n",
    )
  })

  it("shows a loading state while the method text is being fetched", () => {
    renderModal({ skill: null, loading: true })
    expect(screen.getByRole("status").textContent).toMatch(/loading the skill/i)
    expect(screen.queryByLabelText(/skill name/i)).toBeNull()
    // The save button is inert until there is something to save.
    expect(
      (screen.getByRole("button", { name: /save changes/i }) as HTMLButtonElement).disabled,
    ).toBe(true)
  })

  it("surfaces a failed detail fetch instead of an empty form", () => {
    renderModal({ skill: null, loading: false, loadError: "Skill not found." })
    expect(screen.getByRole("alert").textContent).toBe("Skill not found.")
    expect(screen.queryByLabelText(/^method/i)).toBeNull()
  })

  it("saves the three edited fields and closes", async () => {
    const { onSave, onClose } = renderModal()
    await act(async () => {
      fireEvent.change(screen.getByLabelText(/skill name/i), {
        target: { value: "Sizing guide" },
      })
      fireEvent.change(screen.getByLabelText(/what does this skill do/i), {
        target: { value: "Sizes work." },
      })
      fireEvent.change(screen.getByLabelText(/^method/i), {
        target: { value: "# Sizing\nBy hand.\n" },
      })
    })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /save changes/i }))
    })

    await waitFor(() =>
      expect(onSave).toHaveBeenCalledWith({
        name: "Sizing guide",
        description: "Sizes work.",
        // The method keeps its whitespace verbatim — markdown indentation is
        // load-bearing inside a fenced block.
        method: "# Sizing\nBy hand.\n",
      }),
    )
    expect(onClose).toHaveBeenCalled()
  })

  it("warns that a rename retires the current trigger", async () => {
    renderModal()
    // No notice while the name is the one that was loaded…
    expect(screen.queryByRole("status")).toBeNull()

    await act(async () => {
      fireEvent.change(screen.getByLabelText(/skill name/i), {
        target: { value: "Sizing guide" },
      })
    })
    const notice = screen.getByRole("status").textContent ?? ""
    expect(notice).toMatch(/renaming changes this skill’s trigger/i)
    expect(notice).toContain("/estimation-helper")
  })

  it("says nothing about the trigger for a rename that only reformats the name", async () => {
    renderModal()
    // "estimation  HELPER!" slugifies to the same trigger, so the server keeps
    // the slug — promising it would move would be a lie.
    await act(async () => {
      fireEvent.change(screen.getByLabelText(/skill name/i), {
        target: { value: "estimation  HELPER!" },
      })
    })
    expect(screen.queryByRole("status")).toBeNull()
  })

  it("gates a replacing rename behind an explicit confirm", async () => {
    const others = [{ id: "skill-2", slug: "journey-mapper", name: "Journey mapper" }]
    const { onSave } = renderModal({ others })

    await act(async () => {
      fireEvent.change(screen.getByLabelText(/skill name/i), {
        target: { value: "Journey mapper" },
      })
    })
    // Announced as an alert, because saving DELETES a skill the team has.
    const warning = screen.getByRole("alert").textContent ?? ""
    expect(warning).toMatch(/you already have a skill named/i)
    expect(warning).toMatch(/saving replaces it/i)
    expect(warning).toContain("/journey-mapper")

    // First click arms the confirm — it does NOT save.
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /save changes/i }))
    })
    expect(onSave).not.toHaveBeenCalled()
    expect(
      screen.getByRole("group", { name: /confirm replacing journey mapper/i }),
    ).toBeTruthy()

    // The confirm names the destructive outcome, and only its button saves.
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /replace and save/i }))
    })
    await waitFor(() =>
      expect(onSave).toHaveBeenCalledWith(
        expect.objectContaining({ name: "Journey mapper" }),
      ),
    )
  })

  it("cancelling the replace confirm saves nothing and keeps the form", async () => {
    const others = [{ id: "skill-2", slug: "journey-mapper", name: "Journey mapper" }]
    const { onSave, onClose } = renderModal({ others })

    await act(async () => {
      fireEvent.change(screen.getByLabelText(/skill name/i), {
        target: { value: "Journey mapper" },
      })
    })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /save changes/i }))
    })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^cancel$/i }))
    })

    expect(onSave).not.toHaveBeenCalled()
    expect(onClose).not.toHaveBeenCalled()
    expect((screen.getByLabelText(/skill name/i) as HTMLInputElement).value).toBe(
      "Journey mapper",
    )
    expect(screen.getByRole("button", { name: /save changes/i })).toBeTruthy()
  })

  it("re-arms the confirm when the name changes under it", async () => {
    const others = [{ id: "skill-2", slug: "journey-mapper", name: "Journey mapper" }]
    const { onSave } = renderModal({ others })

    await act(async () => {
      fireEvent.change(screen.getByLabelText(/skill name/i), {
        target: { value: "Journey mapper" },
      })
    })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /save changes/i }))
    })
    expect(screen.getByRole("group", { name: /confirm replacing/i })).toBeTruthy()

    // Editing the name after arming points the confirm at a different skill
    // (or none), so it stands down rather than saving what it now doesn't name.
    await act(async () => {
      fireEvent.change(screen.getByLabelText(/skill name/i), {
        target: { value: "Journey mapper v2" },
      })
    })
    expect(screen.queryByRole("group", { name: /confirm replacing/i })).toBeNull()
    expect(onSave).not.toHaveBeenCalled()
  })

  it("does not warn about replacing the skill being edited", async () => {
    // The caller excludes the edited skill from `others`; re-typing its own
    // name must read as "nothing changed", not "this deletes something".
    renderModal({ others: [{ id: "skill-9", slug: "other", name: "Other" }] })
    expect(screen.queryByRole("alert")).toBeNull()
    expect(screen.queryByRole("status")).toBeNull()
  })

  it("blocks a save that pushes the whole skill past the character cap", async () => {
    // A .zip skill: its attached files count toward the cap, so the method
    // alone being under it proves nothing.
    const { onSave } = renderModal({
      skill: {
        ...SKILL,
        modules: ["extra.md"],
        references: [],
        attached_chars: 1000,
      },
    })
    await act(async () => {
      fireEvent.change(screen.getByLabelText(/^method/i), {
        target: { value: "a".repeat(MAX_SKILL_CONTENT_CHARS - 999) },
      })
    })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /save changes/i }))
    })

    await waitFor(() => expect(screen.getByText(/50,000 character limit/)).toBeTruthy())
    expect(onSave).not.toHaveBeenCalled()
  })

  it("tells the user the archive's supporting files survive the edit", () => {
    renderModal({
      skill: {
        ...SKILL,
        modules: ["extra.md"],
        references: ["src.md"],
        attached_chars: 20,
      },
    })
    expect(
      screen.getByText(/2 supporting files from the uploaded archive stay attached/i),
    ).toBeTruthy()
  })

  it("blocks a save with an emptied method and says so", async () => {
    const { onSave } = renderModal()
    await act(async () => {
      fireEvent.change(screen.getByLabelText(/^method/i), { target: { value: "  \n" } })
    })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /save changes/i }))
    })

    expect(onSave).not.toHaveBeenCalled()
    expect(screen.getByText(/the skill method is empty/i)).toBeTruthy()
  })

  it("keeps inputs intact when the save fails, so the user can retry", async () => {
    const onSave = vi.fn().mockRejectedValue(new Error("Skill not found."))
    render(
      React.createElement(EditSkillModal, {
        open: true,
        skill: SKILL,
        loading: false,
        loadError: null,
        others: [],
        onSave,
        onClose: vi.fn(),
      }),
    )
    await act(async () => {
      fireEvent.change(screen.getByLabelText(/skill name/i), {
        target: { value: "Sizing guide" },
      })
    })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /save changes/i }))
    })

    await waitFor(() => expect(screen.getByText("Skill not found.")).toBeTruthy())
    expect((screen.getByLabelText(/skill name/i) as HTMLInputElement).value).toBe(
      "Sizing guide",
    )
  })

  it("renders nothing when closed", () => {
    const { container } = render(
      React.createElement(EditSkillModal, {
        open: false,
        skill: SKILL,
        loading: false,
        loadError: null,
        others: [],
        onSave: vi.fn(),
        onClose: vi.fn(),
      }),
    )
    expect(container.querySelector(".modal-overlay")).toBeNull()
  })
})
