// P2-10 — CompletionBar tests. Node-env vitest (no DOM, no router, no
// testing-library), so — following the DesignAgentDrawer / page.test
// convention — we SSR-render the pure view via renderToStaticMarkup and
// unit-test the extracted orchestration helpers with injected deps.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { afterEach, describe, expect, it, vi } from "vitest"

// Sprntly components carry no `import React`; vitest's esbuild transform uses
// the classic runtime, so expose React globally (PrdSections/DesignAgentDrawer
// test convention) rather than touch the shared vitest config.
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import {
  CompletionBarView,
  runMarkComplete,
  runResume,
  runDownloadMarkdown,
  runCopyMarkdown,
} from "../CompletionBar"

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

function render(props: React.ComponentProps<typeof CompletionBarView>): string {
  return renderToStaticMarkup(React.createElement(CompletionBarView, props))
}

describe("CompletionBarView — editable rendering", () => {
  it("renders Mark Complete when WIP and editable (AC1)", () => {
    const html = render({ isComplete: false, editable: true, prototypeId: 42 })
    expect(html).toContain('data-testid="mark-complete-btn"')
    expect(html).toContain("Mark Complete")
  })

  it("renders Resume + Download + Copy when complete and editable (AC2)", () => {
    const html = render({ isComplete: true, editable: true, prototypeId: 42 })
    expect(html).toContain('data-testid="resume-btn"')
    expect(html).toContain('data-testid="download-md-btn"')
    expect(html).toContain('data-testid="copy-md-btn"')
  })

  it("does not render Mark Complete once complete (AC2)", () => {
    const html = render({ isComplete: true, editable: true, prototypeId: 42 })
    expect(html).not.toContain('data-testid="mark-complete-btn"')
  })
})

describe("CompletionBarView — read-only rendering (AC3)", () => {
  it("renders a WIP badge when not editable", () => {
    const html = render({ isComplete: false, editable: false })
    expect(html).toContain('data-testid="completion-bar-readonly"')
    expect(html).toContain("Work in progress")
  })

  it("renders a complete badge when not editable", () => {
    const html = render({ isComplete: true, editable: false })
    expect(html).toContain('data-testid="completion-bar-readonly"')
    expect(html).toContain("Marked Complete")
  })

  it("renders no mutation buttons when not editable", () => {
    const html = render({ isComplete: true, editable: false })
    expect(html).not.toContain('data-testid="mark-complete-btn"')
    expect(html).not.toContain('data-testid="resume-btn"')
    expect(html).not.toContain('data-testid="download-md-btn"')
    expect(html).not.toContain('data-testid="copy-md-btn"')
  })
})

describe("CompletionBar orchestration helpers — click behaviour", () => {
  it("runMarkComplete calls api.complete(prototypeId) exactly once (AC1)", async () => {
    const complete = vi
      .fn()
      .mockResolvedValue({ prototype_id: 42, is_complete: true, complete_checkpoint_id: 1 })
    const res = await runMarkComplete({ prototypeId: 42, api: { complete } })
    expect(complete).toHaveBeenCalledTimes(1)
    expect(complete).toHaveBeenCalledWith(42)
    expect(res.is_complete).toBe(true)
  })

  it("runResume calls api.resume(prototypeId)", async () => {
    const resume = vi
      .fn()
      .mockResolvedValue({ prototype_id: 42, is_complete: false, handoffs_flagged_stale: 2 })
    await runResume({ prototypeId: 42, api: { resume } })
    expect(resume).toHaveBeenCalledWith(42)
  })

  it("runDownloadMarkdown fetches the export and triggers a browser download (AC4)", async () => {
    const exportMarkdown = vi.fn().mockResolvedValue("# Design brief\n")
    const anchor = { href: "", download: "", click: vi.fn() }
    const createElement = vi.fn(() => anchor)
    const appendChild = vi.fn()
    const removeChild = vi.fn()
    vi.stubGlobal("document", {
      createElement,
      body: { appendChild, removeChild },
    })
    vi.stubGlobal("URL", {
      createObjectURL: vi.fn(() => "blob:fake-url"),
      revokeObjectURL: vi.fn(),
    })

    const md = await runDownloadMarkdown({ prototypeId: 42, api: { exportMarkdown } })

    expect(exportMarkdown).toHaveBeenCalledWith(42)
    expect(createElement).toHaveBeenCalledWith("a")
    expect(anchor.download).toBe("prototype-42-design-brief.md")
    expect(anchor.click).toHaveBeenCalledTimes(1)
    expect(md).toBe("# Design brief\n")
  })

  it("runCopyMarkdown fetches the export then writes it to the clipboard, in order (AC5)", async () => {
    const calls: string[] = []
    const exportMarkdown = vi.fn(async () => {
      calls.push("export")
      return "# md body"
    })
    const writeText = vi.fn(async (_: string) => {
      calls.push("clipboard")
    })
    const md = await runCopyMarkdown({
      prototypeId: 42,
      api: { exportMarkdown },
      clipboard: { writeText },
    })
    expect(exportMarkdown).toHaveBeenCalledWith(42)
    expect(writeText).toHaveBeenCalledWith("# md body")
    expect(calls).toEqual(["export", "clipboard"])
    expect(md).toBe("# md body")
  })
})

describe("CompletionBar — error handling", () => {
  it("runDownloadMarkdown rejects on a 409 and the view shows the error (AC6)", async () => {
    const err = Object.assign(new Error("Mark prototype complete first"), { status: 409 })
    const exportMarkdown = vi.fn().mockRejectedValue(err)
    await expect(
      runDownloadMarkdown({ prototypeId: 42, api: { exportMarkdown } }),
    ).rejects.toMatchObject({ status: 409 })

    const html = render({
      isComplete: true,
      editable: true,
      prototypeId: 42,
      error: "Mark prototype complete first",
    })
    expect(html).toContain('data-testid="completion-bar-error"')
    expect(html).toContain("Mark prototype complete first")
  })

  it("a failed mark-complete surfaces an error message in the view", async () => {
    const complete = vi.fn().mockRejectedValue(new Error("boom"))
    await expect(runMarkComplete({ prototypeId: 42, api: { complete } })).rejects.toThrow("boom")

    const html = render({
      isComplete: false,
      editable: true,
      prototypeId: 42,
      error: "Failed to mark complete",
    })
    expect(html).toContain('data-testid="completion-bar-error"')
    expect(html).toContain("Failed to mark complete")
  })
})

describe("CompletionBarView — stale handoff (AC7)", () => {
  it("renders the stale banner when isStaleHandoff is true", () => {
    const html = render({
      isComplete: false,
      editable: true,
      prototypeId: 42,
      isStaleHandoff: true,
    })
    expect(html).toContain('data-testid="stale-banner"')
    expect(html).toMatch(/out of date/)
  })
})
