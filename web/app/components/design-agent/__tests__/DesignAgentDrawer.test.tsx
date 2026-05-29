import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { readFileSync } from "node:fs"
import { fileURLToPath } from "node:url"
import { dirname, resolve } from "node:path"
import { afterEach, describe, expect, it, vi } from "vitest"
import {
  DEFAULT_PLATFORM,
  DesignAgentDrawerView,
  DrawerFooter,
  runGenerateFlow,
  sourceDetectedLabel,
} from "../DesignAgentDrawer"
import { designAgentApi } from "../../../lib/api"

// PrdSections-style shim: Sprntly components have no `import React`; vitest's
// esbuild transform defaults to the classic runtime, so expose React globally
// rather than touch the shared vitest config (outside the engagement's map).
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const here = dirname(fileURLToPath(import.meta.url))

const noop = () => {}

afterEach(() => {
  vi.restoreAllMocks()
})

describe("sourceDetectedLabel (AC3)", () => {
  it("reports Figma detected when a file key is present", () => {
    expect(sourceDetectedLabel("abc")).toBe("Figma design files detected")
  })
  it("reports no source when the key is null/undefined", () => {
    expect(sourceDetectedLabel(null)).toBe("No Figma source connected")
    expect(sourceDetectedLabel(undefined)).toBe("No Figma source connected")
  })
})

describe("DesignAgentDrawerView render (AC1 markup, AC2, AC3)", () => {
  function render(props: Partial<Parameters<typeof DesignAgentDrawerView>[0]> = {}) {
    return renderToStaticMarkup(
      React.createElement(DesignAgentDrawerView, {
        open: true,
        onOpenChange: noop,
        prdId: 1,
        figmaFileKey: null,
        showToast: noop,
        ...props,
      }),
    )
  }

  it("renders the title, three platform options and the instructions field", () => {
    const html = render()
    expect(html).toContain("Generate Prototype")
    expect(html).toContain("Desktop")
    expect(html).toContain("Mobile")
    expect(html).toContain("Both")
    expect(html).toContain("dap-instructions")
  })

  it("defaults the target platform to Both (AC2)", () => {
    expect(DEFAULT_PLATFORM).toBe("both")
    const html = render()
    expect(html).toMatch(/id="dap-platform-both"[^>]*checked/)
    expect(html).not.toMatch(/id="dap-platform-desktop"[^>]*checked/)
    expect(html).not.toMatch(/id="dap-platform-mobile"[^>]*checked/)
  })

  it("shows the Figma-detected label when a file key is present (AC3)", () => {
    expect(render({ figmaFileKey: "fk" })).toContain("Figma design files detected")
  })

  it("shows the no-source label when no file key (AC3)", () => {
    expect(render()).toContain("No Figma source connected")
  })

  it("renders nothing when closed", () => {
    expect(render({ open: false })).toBe("")
  })

  it("does not call the API merely by rendering (cancel never hits the API — AC1)", () => {
    const gen = vi.spyOn(designAgentApi, "generate")
    render()
    expect(gen).not.toHaveBeenCalled()
  })
})

describe("DrawerFooter submitting state (AC4)", () => {
  it("idle: 'Generate' label, buttons enabled", () => {
    const html = renderToStaticMarkup(
      React.createElement(DrawerFooter, {
        submitting: false,
        onCancel: noop,
        onGenerate: noop,
      }),
    )
    expect(html).toContain("Generate")
    expect(html).not.toContain("Generating")
    expect(html).not.toContain("disabled")
  })

  it("submitting: 'Generating…' label, both buttons disabled", () => {
    const html = renderToStaticMarkup(
      React.createElement(DrawerFooter, {
        submitting: true,
        onCancel: noop,
        onGenerate: noop,
      }),
    )
    expect(html).toContain("Generating…")
    expect(html.match(/disabled/g)?.length).toBe(2)
  })
})

describe("runGenerateFlow (AC1, AC5)", () => {
  const params = {
    prd_id: 9,
    target_platform: "desktop" as const,
    instructions: "go dark",
    figma_file_key: null,
  }

  it("calls generate with the form values, closes, toasts, then polls (AC1)", async () => {
    const generate = vi
      .fn()
      .mockResolvedValue({ prototype_id: 7, status: "generating" })
    const genResult = Promise.resolve({ ok: true as const, prototype: {} as never })
    const runGeneration = vi.fn().mockReturnValue(genResult)
    const onOpenChange = vi.fn()
    const showToast = vi.fn()
    const setSubmitting = vi.fn()

    await runGenerateFlow({
      params,
      generate,
      runGeneration,
      onOpenChange,
      showToast,
      setSubmitting,
    })

    expect(generate).toHaveBeenCalledWith(params)
    expect(onOpenChange).toHaveBeenCalledWith(false)
    expect(showToast).toHaveBeenCalledWith(
      "Design Agent generating",
      expect.any(String),
    )
    expect(runGeneration).toHaveBeenCalledWith({ prototypeId: 7 })
    expect(setSubmitting).toHaveBeenNthCalledWith(1, true)
    expect(setSubmitting).toHaveBeenLastCalledWith(false)

    // Flush the fire-and-forget poll .then so the ready toast lands.
    await genResult
    await Promise.resolve()
    expect(showToast).toHaveBeenCalledWith("Prototype ready", expect.any(String))
  })

  it("surfaces a poll failure as a 'Generation failed' toast", async () => {
    const generate = vi
      .fn()
      .mockResolvedValue({ prototype_id: 8, status: "generating" })
    const genResult = Promise.resolve({ ok: false as const, message: "timed out" })
    const runGeneration = vi.fn().mockReturnValue(genResult)
    const showToast = vi.fn()

    await runGenerateFlow({
      params,
      generate,
      runGeneration,
      onOpenChange: vi.fn(),
      showToast,
      setSubmitting: vi.fn(),
    })
    await genResult
    await Promise.resolve()
    expect(showToast).toHaveBeenCalledWith("Generation failed", "timed out")
  })

  it("on kickoff error: toasts 'Generate failed', keeps the drawer open, no poll (AC5)", async () => {
    const generate = vi.fn().mockRejectedValue(new Error("server 500"))
    const runGeneration = vi.fn()
    const onOpenChange = vi.fn()
    const showToast = vi.fn()
    const setSubmitting = vi.fn()

    await runGenerateFlow({
      params,
      generate,
      runGeneration,
      onOpenChange,
      showToast,
      setSubmitting,
    })

    expect(showToast).toHaveBeenCalledWith("Generate failed", "server 500")
    expect(onOpenChange).not.toHaveBeenCalled()
    expect(runGeneration).not.toHaveBeenCalled()
    expect(setSubmitting).toHaveBeenLastCalledWith(false)
  })
})

describe("NavigationContext drawer union (AC10)", () => {
  it("includes the \"design-agent\" literal", () => {
    const navSrc = readFileSync(
      resolve(here, "../../../context/NavigationContext.tsx"),
      "utf8",
    )
    expect(navSrc).toContain('"design-agent"')
  })
})
