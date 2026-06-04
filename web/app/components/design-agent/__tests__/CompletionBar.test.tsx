// P2-10 — CompletionBar tests. Node-env vitest (no DOM, no router, no
// testing-library), so — following the DesignAgentDrawer / page.test
// convention — we SSR-render the pure view via renderToStaticMarkup and
// unit-test the extracted orchestration helpers with injected deps.
import { readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"
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

// P6-14: extract the opening <button> tag for the export action from SSR markup
// so we can assert presence/absence of the `disabled` attribute without a DOM.
function exportBtnTag(html: string): string | null {
  const m = html.match(/<button[^>]*data-testid="export-claude-code-btn"[^>]*>/)
  return m ? m[0] : null
}

// P6-14: design-agent.css lives one dir up from __tests__ (P6-11-owned; this
// ticket appends). Read the WORKING-TREE file via fs — never a historical git
// rev (CI shallow-clone has no such object). __tests__ → design-agent.
const CSS_PATH = join(dirname(fileURLToPath(import.meta.url)), "..", "design-agent.css")

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

// ---- P6-14 (UX-4) — "Export to Claude Code" gated handoff action -------------

describe("CompletionBarView — Export to Claude Code gating (P6-14)", () => {
  it("export button is disabled with a caption when WIP (AC1)", () => {
    const html = render({ isComplete: false, editable: true, prototypeId: 42 })
    const tag = exportBtnTag(html)
    expect(tag).not.toBeNull()
    expect(tag).toMatch(/\bdisabled\b/)
    expect(html).toContain("Export to Claude Code")
    expect(html).toContain("Available once the prototype is marked Complete.")
  })

  it("export button is enabled when complete and not busy (AC2)", () => {
    const html = render({ isComplete: true, editable: true, prototypeId: 42, busy: false })
    const tag = exportBtnTag(html)
    expect(tag).not.toBeNull()
    expect(tag).not.toMatch(/\bdisabled\b/)
    expect(html).toContain("Export to Claude Code")
  })

  it("export button is disabled while busy even when complete (AC2 edge)", () => {
    const html = render({ isComplete: true, editable: true, prototypeId: 42, busy: true })
    const tag = exportBtnTag(html)
    expect(tag).not.toBeNull()
    expect(tag).toMatch(/\bdisabled\b/)
  })

  it("the WIP caption renders only in the WIP branch, never when complete", () => {
    const wip = render({ isComplete: false, editable: true, prototypeId: 42 })
    const done = render({ isComplete: true, editable: true, prototypeId: 42 })
    expect(wip).toContain("export-claude-code-caption")
    expect(done).not.toContain("export-claude-code-caption")
  })

  it("no export action on the read-only public branch (editable=false)", () => {
    const html = render({ isComplete: true, editable: false })
    expect(html).not.toContain('data-testid="export-claude-code-btn"')
  })

  it("completed branch renders all four actions incl. the exact export label (AC5)", () => {
    const html = render({ isComplete: true, editable: true, prototypeId: 42 })
    expect(html).toContain('data-testid="resume-btn"')
    expect(html).toContain('data-testid="download-md-btn"')
    expect(html).toContain('data-testid="copy-md-btn"')
    expect(html).toContain('data-testid="export-claude-code-btn"')
    expect(html).toContain("Export to Claude Code")
  })
})

describe("Export to Claude Code — reuses onDownload → runDownloadMarkdown (P6-14)", () => {
  // The enabled export button's onClick is the EXISTING onDownload prop (no new
  // onExportClaudeCode prop — Check-25 reuse). onDownload → handleDownload →
  // runDownloadMarkdown → api.exportMarkdown. Node-env vitest cannot fire an SSR
  // onClick, so we exercise the wired target (the same helper "Download .md"
  // uses), per the repo's view/helper testability split.
  it("the export action's target invokes api.exportMarkdown and downloads (AC3)", async () => {
    const exportMarkdown = vi.fn().mockResolvedValue("# Design brief\n")
    const anchor = { href: "", download: "", click: vi.fn() }
    vi.stubGlobal("document", {
      createElement: vi.fn(() => anchor),
      body: { appendChild: vi.fn(), removeChild: vi.fn() },
    })
    vi.stubGlobal("URL", {
      createObjectURL: vi.fn(() => "blob:fake-url"),
      revokeObjectURL: vi.fn(),
    })
    const md = await runDownloadMarkdown({ prototypeId: 42, api: { exportMarkdown } })
    expect(exportMarkdown).toHaveBeenCalledWith(42)
    expect(anchor.click).toHaveBeenCalledTimes(1)
    expect(md).toBe("# Design brief\n")
  })

  it("a WIP 409 from the export route propagates rather than throwing uncaught (AC3 edge)", async () => {
    const err = Object.assign(new Error("Mark prototype complete first"), { status: 409 })
    const exportMarkdown = vi.fn().mockRejectedValue(err)
    await expect(
      runDownloadMarkdown({ prototypeId: 42, api: { exportMarkdown } }),
    ).rejects.toMatchObject({ status: 409 })
  })
})

describe("design-agent.css — P6-14 appended export rules (AC6, AC8)", () => {
  const css = readFileSync(CSS_PATH, "utf8")
  const markerIdx = css.indexOf("P6-14 (UX-4)")
  const appended = markerIdx >= 0 ? css.slice(markerIdx) : ""

  it("appends the three export selectors, all scoped to .design-agent-surface (AC6)", () => {
    expect(markerIdx).toBeGreaterThan(0)
    expect(appended).toContain(".design-agent-surface .btn-export {")
    expect(appended).toContain(".design-agent-surface .btn-export:disabled {")
    expect(appended).toContain(".design-agent-surface .export-claude-code-caption {")
  })

  it("every export selector rule in the appended block is prefixed .design-agent-surface (AC6)", () => {
    const selectorLines = appended
      .split("\n")
      .filter((l) => /(\.btn-export|\.export-claude-code-caption)/.test(l) && l.includes("{"))
    expect(selectorLines.length).toBeGreaterThan(0)
    for (const line of selectorLines) {
      expect(line.trimStart().startsWith(".design-agent-surface ")).toBe(true)
    }
  })

  it("the appended block introduces no literal colour value (AC6)", () => {
    const noComments = appended.replace(/\/\*[\s\S]*?\*\//g, "")
    expect(noComments).not.toMatch(/#[0-9a-fA-F]{3,8}\b/)
    expect(noComments).not.toMatch(/\brgb\(/)
    expect(noComments).not.toMatch(/\bhsl\(/)
  })

  it("btn-export styling appears only in the appended region — P6-11 blocks untouched (AC8)", () => {
    // No `.btn-export`/`.export-claude-code-caption` selector predates the P6-14
    // marker → the P6-11-owned section above is append-only (untouched). This is
    // a working-tree invariant, not a git-rev diff (CI shallow-clone safe).
    const firstCaption = css.indexOf("export-claude-code-caption")
    expect(firstCaption).toBeGreaterThan(markerIdx)
    // single append — the P6-14 header block appears exactly once.
    expect(
      css.split('"Export to Claude Code" gated handoff action').length - 1,
    ).toBe(1)
  })
})
