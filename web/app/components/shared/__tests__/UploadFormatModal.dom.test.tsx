// @vitest-environment jsdom
//
// UploadFormatModal: the add-a-format form. The server is the authoritative
// validator; this modal mirrors only the cheap checks so an obvious mistake
// never costs a round trip, and the retry contract is that a server rejection
// keeps every input intact.
//
// Matchers: native DOM only — NO @testing-library/jest-dom (repo convention).
// Model: UploadSkillModal.dom.test.tsx.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import {
  FORMAT_CAP_ERROR,
  FORMAT_EXT_ERROR,
  FORMAT_SIZE_ERROR,
  MAX_FORMAT_FILE_BYTES,
  MAX_FORMAT_SOURCE_CHARS,
  UploadFormatModal,
  formatFileError,
  nameFromFilename,
} from "../UploadFormatModal"

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function mount(over: Partial<React.ComponentProps<typeof UploadFormatModal>> = {}) {
  const onSubmit = vi.fn().mockResolvedValue(undefined)
  const onClose = vi.fn()
  render(
    React.createElement(UploadFormatModal, {
      open: true,
      initialType: "prd",
      onSubmit,
      onClose,
      ...over,
    }),
  )
  return { onSubmit, onClose }
}

function submitBtn(): HTMLButtonElement {
  return screen.getByRole("button", { name: /Add format|Adding…/ }) as HTMLButtonElement
}

function fileInput(): HTMLInputElement {
  return screen.getByLabelText("Choose a format file (.md)") as HTMLInputElement
}

/** jsdom won't let `size` be set on a File literal; define it. */
function fileOfSize(name: string, size: number): File {
  const f = new File(["x"], name, { type: "text/markdown" })
  Object.defineProperty(f, "size", { value: size })
  return f
}

describe("pure helpers", () => {
  it("mirrors the server's extension gate — .md and .markdown only", () => {
    expect(formatFileError(new File(["#"], "a.md"))).toBeNull()
    expect(formatFileError(new File(["#"], "a.MARKDOWN"))).toBeNull()
    expect(formatFileError(new File(["#"], "a.txt"))).toBe(FORMAT_EXT_ERROR)
    expect(formatFileError(new File(["#"], "noextension"))).toBe(FORMAT_EXT_ERROR)
  })

  it("mirrors the 2 MB byte cap", () => {
    expect(formatFileError(fileOfSize("a.md", MAX_FORMAT_FILE_BYTES))).toBeNull()
    expect(formatFileError(fileOfSize("a.md", MAX_FORMAT_FILE_BYTES + 1))).toBe(
      FORMAT_SIZE_ERROR,
    )
  })

  it("derives a pre-fill name from the filename, minus path and extension", () => {
    expect(nameFromFilename("acme-prd-v3.md")).toBe("acme-prd-v3")
    expect(nameFromFilename("C:\\docs\\Acme PRD v3.markdown")).toBe("Acme PRD v3")
    expect(nameFromFilename("no-extension")).toBe("no-extension")
  })
})

describe("the form", () => {
  it("opens on Paste — a format lives in Confluence or a Doc, not on disk", () => {
    mount()
    expect(
      screen.getByRole("button", { name: "Paste Markdown" }).getAttribute("aria-pressed"),
    ).toBe("true")
    expect(screen.getByLabelText("Paste your format as Markdown")).toBeTruthy()
  })

  it("pre-selects the type the caller opened with", () => {
    mount({ initialType: "impl_spec" })
    expect(
      screen.getByRole("button", { name: "Engineering spec" }).getAttribute("aria-pressed"),
    ).toBe("true")
    expect(
      screen.getByRole("button", { name: "PRD" }).getAttribute("aria-pressed"),
    ).toBe("false")
  })

  it("gates submit until there is a source AND a name", () => {
    mount()
    expect(submitBtn().disabled).toBe(true)

    fireEvent.change(screen.getByLabelText("Paste your format as Markdown"), {
      target: { value: "# Product requirements" },
    })
    // Source but no name — still gated.
    expect(submitBtn().disabled).toBe(true)

    fireEvent.change(screen.getByLabelText(/Name it/), {
      target: { value: "Acme PRD v3" },
    })
    expect(submitBtn().disabled).toBe(false)
  })

  it("gates submit on a picked file in the file branch", () => {
    mount()
    fireEvent.click(screen.getByRole("button", { name: "Upload a .md file" }))
    fireEvent.change(screen.getByLabelText(/Name it/), {
      target: { value: "Acme PRD v3" },
    })
    expect(submitBtn().disabled).toBe(true)
    fireEvent.change(fileInput(), {
      target: { files: [new File(["# x"], "acme.md", { type: "text/markdown" })] },
    })
    expect(submitBtn().disabled).toBe(false)
  })

  it("counts characters against the cap, live and always visible", () => {
    mount()
    expect(screen.getByText(`0 / ${MAX_FORMAT_SOURCE_CHARS.toLocaleString()} characters`)).toBeTruthy()
    fireEvent.change(screen.getByLabelText("Paste your format as Markdown"), {
      target: { value: "abcde" },
    })
    expect(screen.getByText(`5 / ${MAX_FORMAT_SOURCE_CHARS.toLocaleString()} characters`)).toBeTruthy()
  })

  it("hands the paste through as markdown, never as a file", async () => {
    const { onSubmit, onClose } = mount()
    fireEvent.change(screen.getByLabelText("Paste your format as Markdown"), {
      target: { value: "# Product requirements" },
    })
    fireEvent.change(screen.getByLabelText(/Name it/), {
      target: { value: "  Acme PRD v3  " },
    })
    await act(async () => {
      fireEvent.click(submitBtn())
    })
    expect(onSubmit).toHaveBeenCalledWith({
      type: "prd",
      // Trimmed here so the server never stores a name with edge whitespace.
      name: "Acme PRD v3",
      source: "paste",
      markdown: "# Product requirements",
      file: null,
    })
    await waitFor(() => expect(onClose).toHaveBeenCalled())
  })
})

describe("client mirrors of the server caps", () => {
  it("a .txt pick shows the mirrored error and never calls the API", async () => {
    const { onSubmit } = mount()
    fireEvent.click(screen.getByRole("button", { name: "Upload a .md file" }))
    fireEvent.change(fileInput(), {
      target: { files: [new File(["hello"], "format.txt", { type: "text/plain" })] },
    })
    expect(screen.getByText(FORMAT_EXT_ERROR)).toBeTruthy()
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it("an oversize file shows the 2 MB error without transmitting a byte", () => {
    mount()
    fireEvent.click(screen.getByRole("button", { name: "Upload a .md file" }))
    fireEvent.change(fileInput(), {
      target: { files: [fileOfSize("huge.md", MAX_FORMAT_FILE_BYTES + 1)] },
    })
    expect(screen.getByText(FORMAT_SIZE_ERROR)).toBeTruthy()
  })

  it("a paste over 50,000 characters shows the cap error and never submits", async () => {
    const { onSubmit } = mount()
    fireEvent.change(screen.getByLabelText("Paste your format as Markdown"), {
      target: { value: "x".repeat(MAX_FORMAT_SOURCE_CHARS + 1) },
    })
    fireEvent.change(screen.getByLabelText(/Name it/), {
      target: { value: "Too long" },
    })
    await act(async () => {
      fireEvent.click(submitBtn())
    })
    expect(screen.getByText(FORMAT_CAP_ERROR)).toBeTruthy()
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it("pre-fills the name from the filename but never over what the user typed", () => {
    mount()
    fireEvent.click(screen.getByRole("button", { name: "Upload a .md file" }))
    fireEvent.change(fileInput(), {
      target: { files: [new File(["# x"], "acme-prd-v3.md", { type: "text/markdown" })] },
    })
    expect((screen.getByLabelText(/Name it/) as HTMLInputElement).value).toBe(
      "acme-prd-v3",
    )

    cleanup()
    mount()
    fireEvent.click(screen.getByRole("button", { name: "Upload a .md file" }))
    fireEvent.change(screen.getByLabelText(/Name it/), {
      target: { value: "My own name" },
    })
    fireEvent.change(fileInput(), {
      target: { files: [new File(["# x"], "acme-prd-v3.md", { type: "text/markdown" })] },
    })
    expect((screen.getByLabelText(/Name it/) as HTMLInputElement).value).toBe(
      "My own name",
    )
  })
})

describe("duplicate name", () => {
  const existing = [
    { name: "Acme PRD v3", artifact_type: "prd" as const },
    { name: "Acme tickets", artifact_type: "tickets" as const },
  ]

  it("is a role=status FYI and leaves submit enabled — names are free text", () => {
    mount({ existing })
    fireEvent.change(screen.getByLabelText("Paste your format as Markdown"), {
      target: { value: "# x" },
    })
    fireEvent.change(screen.getByLabelText(/Name it/), {
      target: { value: "acme prd v3" },
    })
    const notice = screen.getByText(
      /You already have a PRD format called .Acme PRD v3./,
    )
    expect(notice.getAttribute("role")).toBe("status")
    expect(notice.textContent).toMatch(/Both will be listed/)
    // Nothing is overwritten server-side, so nothing is blocked here.
    expect(submitBtn().disabled).toBe(false)
  })

  it("only clashes within the SAME document type", () => {
    mount({ existing })
    fireEvent.change(screen.getByLabelText(/Name it/), {
      target: { value: "Acme tickets" },
    })
    // The chip is on PRD; a ticket format of that name is not the same thing.
    expect(screen.queryByText(/You already have/)).toBeNull()
    fireEvent.click(screen.getByRole("button", { name: "Tickets" }))
    expect(
      screen.getByText(/You already have a ticket format called .Acme tickets./),
    ).toBeTruthy()
  })
})

describe("server rejection", () => {
  it("shows the message inline and preserves every input for the retry", async () => {
    const onSubmit = vi
      .fn()
      .mockRejectedValue(new Error("A format with that name is too long."))
    render(
      React.createElement(UploadFormatModal, {
        open: true,
        initialType: "tickets",
        onSubmit,
        onClose: vi.fn(),
      }),
    )
    fireEvent.change(screen.getByLabelText("Paste your format as Markdown"), {
      target: { value: "# Ticket format" },
    })
    fireEvent.change(screen.getByLabelText(/Name it/), {
      target: { value: "Acme tickets" },
    })
    await act(async () => {
      fireEvent.click(submitBtn())
    })
    await waitFor(() =>
      expect(screen.getByText("A format with that name is too long.")).toBeTruthy(),
    )
    // Every input survives — the AC across the validation tickets.
    expect((screen.getByLabelText(/Name it/) as HTMLInputElement).value).toBe(
      "Acme tickets",
    )
    expect(
      (screen.getByLabelText("Paste your format as Markdown") as HTMLTextAreaElement).value,
    ).toBe("# Ticket format")
    expect(
      screen.getByRole("button", { name: "Tickets" }).getAttribute("aria-pressed"),
    ).toBe("true")
  })
})
