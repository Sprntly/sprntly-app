// @vitest-environment jsdom
//
// UploadSkillModal: the custom-skill upload form (PRD 1854 happy path).
// Client-side mirror of the server gates — required name/description with
// touched-empty highlighting, .md/.zip-only file pick, 20 MB pre-check, the
// 50,000-character content pre-check for bare .md files — and the retry
// contract: a failed upload keeps every input intact.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import {
  MAX_SKILL_CONTENT_CHARS,
  MAX_SKILL_FILE_BYTES,
  UploadSkillModal,
  countLine,
  skillFileError,
  slugifyName,
} from "../UploadSkillModal"

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function pickFile(container: HTMLElement, file: File) {
  const input = container.querySelector('input[type="file"]') as HTMLInputElement
  fireEvent.change(input, { target: { files: [file] } })
}

const MD_FILE = () => new File(["# method"], "skill.md", { type: "text/markdown" })

describe("skillFileError", () => {
  it("accepts .md and .zip (case-insensitive), rejects everything else", () => {
    expect(skillFileError(new File(["x"], "a.md"))).toBeNull()
    expect(skillFileError(new File(["x"], "a.zip"))).toBeNull()
    expect(skillFileError(new File(["x"], "A.MD"))).toBeNull()
    expect(skillFileError(new File(["x"], "a.pdf"))).toMatch(/Only \.md files and \.zip/)
    expect(skillFileError(new File(["x"], "noext"))).toMatch(/Only \.md files and \.zip/)
  })

  it("rejects a file over 20 MB (boundary is inclusive)", () => {
    const atLimit = new File(["x"], "a.md")
    Object.defineProperty(atLimit, "size", { value: MAX_SKILL_FILE_BYTES })
    expect(skillFileError(atLimit)).toBeNull()

    const over = new File(["x"], "a.md")
    Object.defineProperty(over, "size", { value: MAX_SKILL_FILE_BYTES + 1 })
    expect(skillFileError(over)).toMatch(/20 MB/)
  })
})

describe("countLine", () => {
  it("counts additions and updates, singular where it matters", () => {
    expect(countLine(4, 1)).toBe("4 skills added, 1 updated.")
    expect(countLine(1, 0)).toBe("1 skill added.")
    expect(countLine(0, 3)).toBe("3 updated.")
  })
})

describe("slugifyName", () => {
  it("mirrors the backend slugify (lowercase kebab, no edge hyphens)", () => {
    expect(slugifyName("PRD Author")).toBe("prd-author")
    expect(slugifyName("  Roadmap!! v2 ")).toBe("roadmap-v2")
    expect(slugifyName("!!!")).toBe("")
  })
})

describe("UploadSkillModal", () => {
  it("previews the trigger — without blocking — when a built-in has the name", async () => {
    const onUpload = vi.fn().mockResolvedValue(undefined)
    const { container } = render(
      React.createElement(UploadSkillModal, {
        open: true,
        onUpload,
        onClose: vi.fn(),
        builtinSlugs: ["prd-author"],
      }),
    )
    await act(async () => {
      fireEvent.change(screen.getByLabelText(/skill name/i), {
        target: { value: "PRD Author" },
      })
    })
    // Informational status, not an alert: nothing is replaced — the upload
    // proceeds and just gets its own trigger, which the notice names.
    const notice = screen.getByRole("status").textContent ?? ""
    expect(notice).toMatch(/both stay in your library/i)
    expect(notice).toContain("/prd-author-2")

    await act(async () => {
      fireEvent.change(screen.getByLabelText(/what does this skill do/i), {
        target: { value: "Our own PRD flow." },
      })
      pickFile(container, MD_FILE())
    })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /upload skill/i }))
    })
    await waitFor(() => expect(onUpload).toHaveBeenCalled())
  })

  it("previewed trigger skips a slug the company already handed out", async () => {
    render(
      React.createElement(UploadSkillModal, {
        open: true,
        onUpload: vi.fn(),
        onClose: vi.fn(),
        builtinSlugs: ["prd-author"],
        customSkills: [{ slug: "prd-author-2", name: "PRD Author 2" }],
      }),
    )
    await act(async () => {
      fireEvent.change(screen.getByLabelText(/skill name/i), {
        target: { value: "PRD Author" },
      })
    })
    expect(screen.getByRole("status").textContent).toContain("/prd-author-3")
  })

  it("warns that the company's OWN same-named skill will be replaced", async () => {
    const onUpload = vi.fn().mockResolvedValue(undefined)
    const { container } = render(
      React.createElement(UploadSkillModal, {
        open: true,
        onUpload,
        onClose: vi.fn(),
        customSkills: [{ slug: "estimation-helper-2", name: "Estimation Helper" }],
      }),
    )
    await act(async () => {
      // Same name once slugified — the equivalence the server replaces on.
      fireEvent.change(screen.getByLabelText(/skill name/i), {
        target: { value: "estimation  helper!" },
      })
    })
    // Announced as an alert (it overwrites something the team has) and it
    // names the trigger the replacement keeps — the STORED slug, which may
    // have been disambiguated away from the name's plain slug.
    const notice = screen.getByRole("alert").textContent ?? ""
    expect(notice).toMatch(/replaces it with this version/i)
    expect(notice).toContain("/estimation-helper-2")

    // It is a warning, not a block: the upload still goes through.
    await act(async () => {
      fireEvent.change(screen.getByLabelText(/what does this skill do/i), {
        target: { value: "Ours, v2." },
      })
      pickFile(container, MD_FILE())
    })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /upload skill/i }))
    })
    await waitFor(() => expect(onUpload).toHaveBeenCalled())
  })

  it("shows no name notice for a free name", async () => {
    render(
      React.createElement(UploadSkillModal, {
        open: true,
        onUpload: vi.fn(),
        onClose: vi.fn(),
        builtinSlugs: ["prd-author"],
        customSkills: [{ slug: "estimation-helper", name: "Estimation Helper" }],
      }),
    )
    await act(async () => {
      fireEvent.change(screen.getByLabelText(/skill name/i), {
        target: { value: "My Own Skill" },
      })
    })
    expect(screen.queryByRole("status")).toBeNull()
    expect(screen.queryByRole("alert")).toBeNull()
  })

  it("gates submit until file, name, and description are all present", async () => {
    const onUpload = vi.fn().mockResolvedValue(undefined)
    const { container } = render(
      React.createElement(UploadSkillModal, { open: true, onUpload, onClose: vi.fn() }),
    )
    const submit = screen.getByRole("button", { name: /upload skill/i }) as HTMLButtonElement
    expect(submit.disabled).toBe(true)

    await act(async () => {
      fireEvent.change(screen.getByLabelText(/skill name/i), { target: { value: "My skill" } })
    })
    expect(submit.disabled).toBe(true)

    await act(async () => {
      fireEvent.change(screen.getByLabelText(/what does this skill do/i), {
        target: { value: "Does things." },
      })
    })
    expect(submit.disabled).toBe(true)

    await act(async () => {
      pickFile(container, MD_FILE())
    })
    expect(submit.disabled).toBe(false)
  })

  it("highlights a required field that was touched and left empty", async () => {
    render(
      React.createElement(UploadSkillModal, {
        open: true,
        onUpload: vi.fn(),
        onClose: vi.fn(),
      }),
    )
    const name = screen.getByLabelText(/skill name/i)
    await act(async () => {
      fireEvent.change(name, { target: { value: "x" } })
      fireEvent.change(name, { target: { value: "" } })
    })
    expect(screen.getByText("Skill name is required.")).toBeTruthy()
    expect((name as HTMLInputElement).getAttribute("aria-invalid")).toBe("true")
  })

  it("shows the type error immediately on a bad pick", async () => {
    const { container } = render(
      React.createElement(UploadSkillModal, { open: true, onUpload: vi.fn(), onClose: vi.fn() }),
    )
    await act(async () => {
      pickFile(container, new File(["%PDF"], "skill.pdf"))
    })
    expect(screen.getByText(/Only \.md files and \.zip archives/)).toBeTruthy()
  })

  it("rejects a .md over the character cap on submit, without uploading", async () => {
    const onUpload = vi.fn()
    const { container } = render(
      React.createElement(UploadSkillModal, { open: true, onUpload, onClose: vi.fn() }),
    )
    await act(async () => {
      fireEvent.change(screen.getByLabelText(/skill name/i), { target: { value: "Big" } })
      fireEvent.change(screen.getByLabelText(/what does this skill do/i), {
        target: { value: "Desc" },
      })
      pickFile(
        container,
        new File(["x".repeat(MAX_SKILL_CONTENT_CHARS + 1)], "big.md", {
          type: "text/markdown",
        }),
      )
    })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /upload skill/i }))
    })

    await waitFor(() =>
      expect(screen.getByText(/50,000 character limit/)).toBeTruthy(),
    )
    expect(onUpload).not.toHaveBeenCalled()
    // Inputs survive so the user can trim and retry (same retry contract as
    // a server rejection).
    expect((screen.getByLabelText(/skill name/i) as HTMLInputElement).value).toBe("Big")
  })

  it("accepts a .md exactly at the character cap (inclusive boundary)", async () => {
    const onUpload = vi.fn().mockResolvedValue(undefined)
    const { container } = render(
      React.createElement(UploadSkillModal, { open: true, onUpload, onClose: vi.fn() }),
    )
    await act(async () => {
      fireEvent.change(screen.getByLabelText(/skill name/i), { target: { value: "At cap" } })
      fireEvent.change(screen.getByLabelText(/what does this skill do/i), {
        target: { value: "Desc" },
      })
      pickFile(
        container,
        new File(["x".repeat(MAX_SKILL_CONTENT_CHARS)], "atcap.md", {
          type: "text/markdown",
        }),
      )
    })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /upload skill/i }))
    })

    await waitFor(() => expect(onUpload).toHaveBeenCalled())
  })

  it("keeps inputs intact when the upload fails, so the user can retry", async () => {
    // Any server rejection — this one is the concurrent-upload 409 the route
    // still returns when two uploads race for the same free trigger.
    const onUpload = vi
      .fn()
      .mockRejectedValue(new Error("Another upload just took this skill's trigger. Please try again."))
    const { container } = render(
      React.createElement(UploadSkillModal, { open: true, onUpload, onClose: vi.fn() }),
    )
    await act(async () => {
      fireEvent.change(screen.getByLabelText(/skill name/i), { target: { value: "Dupe" } })
      fireEvent.change(screen.getByLabelText(/what does this skill do/i), {
        target: { value: "Desc" },
      })
      pickFile(container, MD_FILE())
    })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /upload skill/i }))
    })

    await waitFor(() =>
      expect(screen.getByText(/just took this skill's trigger/)).toBeTruthy(),
    )
    expect((screen.getByLabelText(/skill name/i) as HTMLInputElement).value).toBe("Dupe")
    expect(screen.getByText("skill.md")).toBeTruthy()
  })

  // A .zip holding a folder per SKILL.md imports as several skills at once.
  // The upload resolves with the list instead of nothing, and the modal owes
  // the user the outcome: which skills exist now, under which triggers, which
  // were updated rather than added, and what it couldn't import.
  describe("a multi-skill archive", () => {
    const MULTI = {
      skills: [
        {
          id: "s1", slug: "sprint-planner", trigger: "/sprint-planner",
          name: "Sprint Planner", description: "Plans a sprint.",
          uploader_name: "Fortune Tede", created_at: null, has_file: true,
          name_conflict: false, replaced: false,
        },
        {
          id: "s2", slug: "pricing-review-2", trigger: "/pricing-review-2",
          name: "Pricing Review", description: "Reviews pricing.",
          uploader_name: "Fortune Tede", created_at: null, has_file: true,
          name_conflict: true, replaced: true,
        },
      ],
      skipped: [
        { path: "bloated", name: "Bloated", reason: "it is over the 50,000 character limit" },
      ],
    }

    async function uploadMulti(result: typeof MULTI) {
      const onUpload = vi.fn().mockResolvedValue(result)
      const onClose = vi.fn()
      const { container } = render(
        React.createElement(UploadSkillModal, { open: true, onUpload, onClose }),
      )
      await act(async () => {
        fireEvent.change(screen.getByLabelText(/skill name/i), { target: { value: "Bundle" } })
        fireEvent.change(screen.getByLabelText(/what does this skill do/i), {
          target: { value: "Ignored." },
        })
        pickFile(container, new File(["zip"], "skills.zip", { type: "application/zip" }))
      })
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: /upload skill/i }))
      })
      await waitFor(() => expect(onUpload).toHaveBeenCalled())
      return { onClose }
    }

    it("stays open and reports every skill it created, with its trigger", async () => {
      const { onClose } = await uploadMulti(MULTI)

      // Not closed — a toast cannot carry per-skill triggers.
      expect(onClose).not.toHaveBeenCalled()
      expect(screen.getByRole("dialog", { name: /skills imported/i })).toBeTruthy()
      expect(screen.getByText("/sprint-planner")).toBeTruthy()
      expect(screen.getByText("/pricing-review-2")).toBeTruthy()
      // One added, one updated — counted, not conflated.
      expect(screen.getByRole("status").textContent).toMatch(/1 skill added, 1 updated\./)
      expect(screen.getByText("updated")).toBeTruthy()
      // …and it says the typed name/description were not used, because the
      // archive named its own skills.
      expect(screen.getByRole("status").textContent).toMatch(/named from its own SKILL\.md/i)
    })

    it("lists every folder it could not import, with the reason", async () => {
      await uploadMulti(MULTI)
      expect(screen.getByRole("alert").textContent).toMatch(/One folder wasn’t imported/)
      expect(screen.getByText(/Bloated — it is over the 50,000 character limit/)).toBeTruthy()
    })

    it("closes from Done", async () => {
      const { onClose } = await uploadMulti(MULTI)
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: /^done$/i }))
      })
      expect(onClose).toHaveBeenCalled()
    })

    it("says nothing about skipped folders when there were none", async () => {
      await uploadMulti({ ...MULTI, skipped: [] })
      expect(screen.queryByRole("alert")).toBeNull()
    })
  })

  it("resets and closes on success", async () => {
    const onUpload = vi.fn().mockResolvedValue(undefined)
    const onClose = vi.fn()
    const { container } = render(
      React.createElement(UploadSkillModal, { open: true, onUpload, onClose }),
    )
    await act(async () => {
      fireEvent.change(screen.getByLabelText(/skill name/i), { target: { value: "Good" } })
      fireEvent.change(screen.getByLabelText(/what does this skill do/i), {
        target: { value: "Desc" },
      })
      pickFile(container, MD_FILE())
    })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /upload skill/i }))
    })

    await waitFor(() => expect(onUpload).toHaveBeenCalledWith(expect.any(File), "Good", "Desc"))
    expect(onClose).toHaveBeenCalled()
  })
})
