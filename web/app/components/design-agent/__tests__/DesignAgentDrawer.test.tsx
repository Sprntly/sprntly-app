import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { readFileSync } from "node:fs"
import { fileURLToPath } from "node:url"
import { dirname, resolve } from "node:path"
import { afterEach, describe, expect, it, vi } from "vitest"
import {
  buildGenerateParams,
  DEFAULT_PLATFORM,
  DesignAgentDrawerView,
  DrawerFooter,
  replayCompletedNotifications,
  runGenerateFlow,
  sourceDetectedLabel,
} from "../DesignAgentDrawer"
import {
  __resetPageLoadGuards,
  markCompleted,
  markPending,
  pendingCompleted,
} from "../notificationStore"
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
      notifyOnReady: true,
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
      notifyOnReady: false,
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
      notifyOnReady: false,
    })

    expect(showToast).toHaveBeenCalledWith("Generate failed", "server 500")
    expect(onOpenChange).not.toHaveBeenCalled()
    expect(runGeneration).not.toHaveBeenCalled()
    expect(setSubmitting).toHaveBeenLastCalledWith(false)
  })
})

describe("F3 opt-in toggle (AC4, AC5, AC6)", () => {
  const params = {
    prd_id: 9,
    target_platform: "desktop" as const,
    instructions: "",
    figma_file_key: null,
  }

  function renderDrawer(
    props: Partial<Parameters<typeof DesignAgentDrawerView>[0]> = {},
  ) {
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

  it("renders the 'Notify me when ready' checkbox, unchecked by default (AC4)", () => {
    const html = renderDrawer()
    expect(html).toContain("Notify me when ready")
    expect(html).toMatch(/id="dap-notify"/)
    // unchecked → no `checked` attribute on the opt-in input specifically
    expect(html).not.toMatch(/id="dap-notify"[^>]*checked/)
  })

  it("does NOT fire the ready toast when notifyOnReady is false (AC5)", async () => {
    const genResult = Promise.resolve({ ok: true as const, prototype: {} as never })
    const showToast = vi.fn()

    await runGenerateFlow({
      params,
      generate: vi.fn().mockResolvedValue({ prototype_id: 7, status: "generating" }),
      runGeneration: vi.fn().mockReturnValue(genResult),
      onOpenChange: vi.fn(),
      showToast,
      setSubmitting: vi.fn(),
      notifyOnReady: false,
    })
    await genResult
    await Promise.resolve()

    expect(showToast).not.toHaveBeenCalledWith("Prototype ready", expect.any(String))
  })

  it("fires the ready toast when notifyOnReady is true (AC6)", async () => {
    const genResult = Promise.resolve({ ok: true as const, prototype: {} as never })
    const showToast = vi.fn()

    await runGenerateFlow({
      params,
      generate: vi.fn().mockResolvedValue({ prototype_id: 7, status: "generating" }),
      runGeneration: vi.fn().mockReturnValue(genResult),
      onOpenChange: vi.fn(),
      showToast,
      setSubmitting: vi.fn(),
      notifyOnReady: true,
    })
    await genResult
    await Promise.resolve()

    expect(showToast).toHaveBeenCalledWith("Prototype ready", expect.any(String))
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

// ─── P5-02: Scenario B floor — conditional inputs + request-body shape ───────

describe("Scenario B fallback inputs (P5-02 AC6)", () => {
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

  it("renders the website-URL + manual color + font inputs when no Figma (AC6)", () => {
    const html = render({ figmaFileKey: null })
    expect(html).toContain('id="dap-website-url"')
    expect(html).toContain('id="dap-manual-color"')
    expect(html).toContain('id="dap-manual-font"')
    // The color picker is a native <input type="color"> (attribute order is
    // JSX-source order: type precedes id).
    expect(html).toMatch(/type="color"[^>]*id="dap-manual-color"/)
  })

  it("hides all three inputs when a Figma file key is present (AC6)", () => {
    const html = render({ figmaFileKey: "fk" })
    expect(html).not.toContain('id="dap-website-url"')
    expect(html).not.toContain('id="dap-manual-color"')
    expect(html).not.toContain('id="dap-manual-font"')
  })
})

describe("buildGenerateParams request-body shape (P5-02 AC7)", () => {
  const base = {
    prdId: 9,
    platform: "both" as const,
    instructions: "",
    figmaFileKey: null,
  }

  it("includes website_url + manual_design when a URL, color and font are set (AC7)", () => {
    const params = buildGenerateParams({
      ...base,
      websiteUrl: "https://acme.com",
      manualColor: "#3b82f6",
      manualFont: "Inter",
    })
    expect(params.website_url).toBe("https://acme.com")
    expect(params.manual_design).toEqual({
      primary_color: "#3b82f6",
      font_family: "Inter",
    })
  })

  it("nulls website_url + manual_design when nothing is supplied (AC7)", () => {
    const params = buildGenerateParams({
      ...base,
      websiteUrl: "",
      manualColor: "#3b82f6", // default color, but no font name → not enough
      manualFont: "",
    })
    expect(params.website_url).toBeNull()
    expect(params.manual_design).toBeNull()
  })

  it("still threads figma_file_key and platform unchanged (AC7/AC3)", () => {
    const params = buildGenerateParams({
      prdId: 5,
      platform: "mobile",
      instructions: "dark theme",
      figmaFileKey: "FK",
      websiteUrl: "https://ignored.example", // present but Figma wins server-side
      manualColor: "#000000",
      manualFont: "Roboto",
    })
    expect(params.prd_id).toBe(5)
    expect(params.target_platform).toBe("mobile")
    expect(params.figma_file_key).toBe("FK")
    expect(params.instructions).toBe("dark theme")
  })
})

describe("DesignAgentDrawer prop signature unchanged (P5-02 AC9)", () => {
  it("renders with the existing prop set (no required new props)", () => {
    const html = renderToStaticMarkup(
      React.createElement(DesignAgentDrawerView, {
        open: true,
        onOpenChange: noop,
        prdId: 42,
        figmaFileKey: null,
        showToast: noop,
        // onGenerated is optional and omitted — proves the signature is unchanged
      }),
    )
    expect(html).toContain("Generate Prototype")
  })
})

// ─── P5-09: notification persistence delta (re-show on reload + acknowledge) ──

describe("notification persistence (P5-09)", () => {
  function makeSessionStorage(): Storage {
    let store: Record<string, string> = {}
    return {
      get length(): number {
        return Object.keys(store).length
      },
      getItem: (k: string): string | null => (k in store ? store[k] : null),
      setItem: (k: string, v: string): void => {
        store[k] = String(v)
      },
      removeItem: (k: string): void => {
        delete store[k]
      },
      clear: (): void => {
        store = {}
      },
      key: (i: number): string | null => Object.keys(store)[i] ?? null,
    }
  }

  // `unknown as` breaks the lib.dom `Window` typing so a node-env stub can be
  // installed and then set back to undefined (the SSR / no-storage case).
  const testGlobal = globalThis as unknown as {
    window?: { sessionStorage: Storage }
  }

  function installStorage() {
    testGlobal.window = { sessionStorage: makeSessionStorage() }
  }

  function removeWindow() {
    testGlobal.window = undefined
  }

  afterEach(() => {
    removeWindow()
    // P6-05: the replay now uses module-level per-page-load guards; reset them
    // between cases so a simulated reload re-shows (browser reload re-evaluates
    // the module).
    __resetPageLoadGuards()
  })

  // P6-05 (Decision-D(b)) — the replay was hoisted to the shell AND no longer
  // auto-acks on first show. This is the "moved-replay assertion" AC6 calls out:
  // the only existing P5-09 test whose behaviour changes by design.
  it("replay shows a completed entry once per page-load and does NOT auto-ack it (P6-05 AC3)", () => {
    installStorage()
    markCompleted(7, "Your prototype finished generating.")

    const showToast = vi.fn()
    replayCompletedNotifications(showToast) // simulates the shell replay on mount
    expect(showToast).toHaveBeenCalledTimes(1)
    expect(showToast).toHaveBeenCalledWith(
      "Prototype ready",
      "Your prototype finished generating.",
    )

    // P6-05: the entry is NOT acknowledged on show — it survives in sessionStorage
    // so a subsequent hard reload re-shows it (acked-until-user-acks).
    expect(pendingCompleted()).toEqual([
      { prototypeId: 7, sub: "Your prototype finished generating." },
    ])

    // A second replay within the SAME page-load does NOT re-show (per-load guard).
    const showToast2 = vi.fn()
    replayCompletedNotifications(showToast2)
    expect(showToast2).not.toHaveBeenCalled()

    // …but after a simulated hard reload (guards reset) it re-shows again.
    __resetPageLoadGuards()
    const showToast3 = vi.fn()
    replayCompletedNotifications(showToast3)
    expect(showToast3).toHaveBeenCalledTimes(1)
  })

  it("does NOT re-show a pending (not-yet-complete) entry on mount (AC3)", () => {
    installStorage()
    markPending(9)

    const showToast = vi.fn()
    replayCompletedNotifications(showToast)
    expect(showToast).not.toHaveBeenCalled()
  })

  it("records completion in sessionStorage while the live toast still fires (AC5)", async () => {
    installStorage()
    const genResult = Promise.resolve({ ok: true as const, prototype: {} as never })
    const showToast = vi.fn()

    await runGenerateFlow({
      params: {
        prd_id: 9,
        target_platform: "desktop" as const,
        instructions: "",
        figma_file_key: null,
      },
      generate: vi.fn().mockResolvedValue({ prototype_id: 7, status: "generating" }),
      runGeneration: vi.fn().mockReturnValue(genResult),
      onOpenChange: vi.fn(),
      showToast,
      setSubmitting: vi.fn(),
      notifyOnReady: true,
    })
    await genResult
    await Promise.resolve()

    // Live toast unchanged (still fires this page life)…
    expect(showToast).toHaveBeenCalledWith("Prototype ready", expect.any(String))
    // …AND the completion is persisted so a reload can re-show it.
    expect(pendingCompleted()).toEqual([
      { prototypeId: 7, sub: "Your prototype finished generating." },
    ])
  })

  it("SSR-renders the drawer without throwing when storage is unavailable (AC6)", () => {
    removeWindow()
    expect(() =>
      renderToStaticMarkup(
        React.createElement(DesignAgentDrawerView, {
          open: true,
          onOpenChange: noop,
          prdId: 1,
          figmaFileKey: null,
          showToast: noop,
        }),
      ),
    ).not.toThrow()
  })

  it("keeps the view's required prop set unchanged after the persistence delta (AC8)", () => {
    const html = renderToStaticMarkup(
      React.createElement(DesignAgentDrawerView, {
        open: true,
        onOpenChange: noop,
        prdId: 3,
        figmaFileKey: null,
        showToast: noop,
        // no persistence-related prop added — the store is internal
      }),
    )
    expect(html).toContain("Generate Prototype")
  })

  it("retargets the ready-toast sub off the removed Design section", async () => {
    installStorage()
    const genResult = Promise.resolve({ ok: true as const, prototype: {} as never })
    const showToast = vi.fn()

    await runGenerateFlow({
      params: {
        prd_id: 11,
        target_platform: "desktop" as const,
        instructions: "",
        figma_file_key: null,
      },
      generate: vi.fn().mockResolvedValue({ prototype_id: 11, status: "generating" }),
      runGeneration: vi.fn().mockReturnValue(genResult),
      onOpenChange: vi.fn(),
      showToast,
      setSubmitting: vi.fn(),
      notifyOnReady: true,
    })
    await genResult
    await Promise.resolve()

    const readyCall = showToast.mock.calls.find((c) => c[0] === "Prototype ready")
    expect(readyCall).toBeTruthy()
    const sub = readyCall![1] as string
    // Fails on the prior constant, which pointed at the now-removed Design section.
    expect(sub).not.toContain("Design section")
    expect(sub).toBe("Your prototype finished generating.")
  })

  it("re-shows the persisted sub byte-identical to the live toast on reload", async () => {
    installStorage()
    const genResult = Promise.resolve({ ok: true as const, prototype: {} as never })
    const liveToast = vi.fn()

    await runGenerateFlow({
      params: {
        prd_id: 12,
        target_platform: "desktop" as const,
        instructions: "",
        figma_file_key: null,
      },
      generate: vi.fn().mockResolvedValue({ prototype_id: 12, status: "generating" }),
      runGeneration: vi.fn().mockReturnValue(genResult),
      onOpenChange: vi.fn(),
      showToast: liveToast,
      setSubmitting: vi.fn(),
      notifyOnReady: true,
    })
    await genResult
    await Promise.resolve()

    const liveSub = liveToast.mock.calls.find((c) => c[0] === "Prototype ready")![1]
    // The live toast's sub is what gets persisted…
    expect(pendingCompleted()).toEqual([{ prototypeId: 12, sub: liveSub }])
    // …and the post-reload replay re-shows that exact persisted sub.
    const replayToast = vi.fn()
    replayCompletedNotifications(replayToast)
    expect(replayToast).toHaveBeenCalledWith("Prototype ready", liveSub)
  })
})

describe("buildGenerateParams github_repo threading", () => {
  const base = {
    prdId: 9,
    platform: "both" as const,
    instructions: "",
    figmaFileKey: null,
    websiteUrl: "",
    manualColor: "",
    manualFont: "",
  }

  it("emits github_repo when a repo is supplied; other keys unchanged", () => {
    const params = buildGenerateParams({ ...base, githubRepo: "org/repo" })
    expect(params.github_repo).toBe("org/repo")
    // Every existing key is unchanged by the new repo arg.
    expect(params.prd_id).toBe(9)
    expect(params.target_platform).toBe("both")
    expect(params.instructions).toBe("")
    expect(params.figma_file_key).toBeNull()
    expect(params.website_url).toBeNull()
    expect(params.manual_design).toBeNull()
  })

  it("nulls github_repo when the repo arg is blank or whitespace", () => {
    expect(buildGenerateParams({ ...base, githubRepo: "   " }).github_repo).toBeNull()
    expect(buildGenerateParams({ ...base, githubRepo: "" }).github_repo).toBeNull()
    // Omitted entirely → still null.
    expect(buildGenerateParams({ ...base }).github_repo).toBeNull()
  })
})

