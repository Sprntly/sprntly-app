// @vitest-environment jsdom
//
// UploadSkillModal: the custom-skill upload form (PRD 1854 happy path).
// Client-side mirror of the server gates — required name/description with
// touched-empty highlighting, .md/.zip-only file pick, 20 MB pre-check —
// and the retry contract: a failed upload keeps every input intact.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import {
  MAX_SKILL_FILE_BYTES,
  UploadSkillModal,
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

describe("slugifyName", () => {
  it("mirrors the backend slugify (lowercase kebab, no edge hyphens)", () => {
    expect(slugifyName("PRD Author")).toBe("prd-author")
    expect(slugifyName("  Roadmap!! v2 ")).toBe("roadmap-v2")
    expect(slugifyName("!!!")).toBe("")
  })
})

describe("UploadSkillModal", () => {
  it("warns — without blocking — when the name matches a built-in skill", async () => {
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
    // Informational status, not an alert — the upload is allowed to proceed.
    expect(screen.getByRole("status").textContent).toMatch(
      /replace it with your skill/,
    )

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

  it("shows no override warning for a non-colliding name", async () => {
    render(
      React.createElement(UploadSkillModal, {
        open: true,
        onUpload: vi.fn(),
        onClose: vi.fn(),
        builtinSlugs: ["prd-author"],
      }),
    )
    await act(async () => {
      fireEvent.change(screen.getByLabelText(/skill name/i), {
        target: { value: "My Own Skill" },
      })
    })
    expect(screen.queryByRole("status")).toBeNull()
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

  it("keeps inputs intact when the upload fails, so the user can retry", async () => {
    const onUpload = vi.fn().mockRejectedValue(new Error("A skill with this name already exists in your company."))
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
      expect(screen.getByText(/already exists in your company/)).toBeTruthy(),
    )
    expect((screen.getByLabelText(/skill name/i) as HTMLInputElement).value).toBe("Dupe")
    expect(screen.getByText("skill.md")).toBeTruthy()
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
