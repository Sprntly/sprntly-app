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
  skillFileError,
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

describe("UploadSkillModal", () => {
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
