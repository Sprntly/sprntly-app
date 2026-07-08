import { readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { afterEach, describe, expect, it, vi } from "vitest"
import { DesignAgentLauncher, DesignAgentLauncherView } from "../DesignAgentLauncher"
import { PrototypePreviewCard } from "../PrototypePreviewCard"
import type { PrototypeRecord } from "../../../lib/api"

// PrdSections-style shim: Sprntly components have no `import React`; vitest's
// esbuild transform defaults to the classic runtime, so expose React globally
// rather than touch the shared vitest config (outside the engagement's map).
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const HERE = dirname(fileURLToPath(import.meta.url))
// __tests__ → design-agent — read the working-tree source for the no-dead-import
// content invariant below (repo convention — see PrdSections-design.test.tsx;
// NEVER `git show <rev>` / `git diff <sha>`, which fails under CI's shallow clone).
const LAUNCHER_SRC = readFileSync(join(HERE, "..", "DesignAgentLauncher.tsx"), "utf8")

afterEach(() => {
  vi.restoreAllMocks()
})

describe("DesignAgentLauncher — surface wrapper markup", () => {
  it("renders the design-agent-surface wrapper without a direct Generate button (test_launcher_renders_surface_wrapper)", () => {
    const html = renderToStaticMarkup(
      React.createElement(DesignAgentLauncher, {
        prdId: 1,
        figmaFileKey: null,
      }),
    )
    expect(html).toContain('class="design-agent-surface prd-design-launcher"')
    expect(html).toMatch(/contenteditable="false"/i)
    // The generation trigger moved to the Approve modal — no direct button here.
    expect(html).not.toContain("Generate Prototype")
  })

  it("the surface wrapper has contentEditable={false} — clickable inside the PRD editable region (test_launcher_content_editable_wrapper)", () => {
    const html = renderToStaticMarkup(
      React.createElement(DesignAgentLauncher, {
        prdId: 1,
        figmaFileKey: null,
      }),
    )
    // The wrapper div carries contentEditable="false" — load-bearing for
    // Sprntly's PRD editable region. Generation lives in the Approve modal now.
    expect(html).toMatch(/contenteditable="false"/i)
    expect(html).not.toContain("Generate Prototype")
  })
})

describe("DesignAgentLauncher — open interaction (DI)", () => {
  it("the preview card's onOpen calls onOpenExisting — existing-prototype open path (test_launcher_preview_card_opens_canvas)", () => {
    const onOpenExisting = vi.fn()
    // The view is pure (no hooks); call it directly to inspect the element tree.
    const tree = DesignAgentLauncherView({
      prdId: 1,
      figmaFileKey: null,
      existing: { id: 7, status: "ready", bundle_url: "https://cdn/x/bundle/index.html", error: null },
      onOpenExisting,
    }) as React.ReactElement
    const children = React.Children.toArray(
      (tree.props as { children: React.ReactNode }).children,
    ) as React.ReactElement[]
    const card = children.find((c) => c.type === PrototypePreviewCard)
    expect(card).toBeTruthy()
    ;(card!.props as { onOpen: () => void }).onOpen()
    expect(onOpenExisting).toHaveBeenCalledTimes(1)
  })
})

describe("DesignAgentLauncher — exported signatures unchanged (test_launcher_signatures_unchanged, AC8)", () => {
  it("DesignAgentLauncher / DesignAgentLauncherView remain exported components", () => {
    expect(typeof DesignAgentLauncher).toBe("function")
    expect(typeof DesignAgentLauncherView).toBe("function")
  })
})

describe("DesignAgentLauncherView — no dead result/failure/clarify/drawer branches (AC5)", () => {
  it("never renders post-generation-result / generation-error-banner / clarifying-question-surface / a drawer with the surviving props (test_launcher_view_has_no_result_branch)", () => {
    const html = renderToStaticMarkup(
      React.createElement(DesignAgentLauncherView, {
        prdId: 1,
        figmaFileKey: null,
        prdTitle: "Checkout redesign",
        existing: {
          id: 7,
          status: "ready",
          bundle_url: "https://cdn/x/bundle/index.html",
          error: null,
        },
        onOpenExisting: () => {},
        onDeleteExisting: async () => {},
      }),
    )
    expect(html).not.toContain('data-testid="post-generation-result"')
    expect(html).not.toContain('data-testid="generation-error-banner"')
    expect(html).not.toContain('data-testid="clarifying-question-surface"')
  })

  it("renders nothing (no preview card) when there is no existing prototype", () => {
    const html = renderToStaticMarkup(
      React.createElement(DesignAgentLauncherView, {
        prdId: 1,
        figmaFileKey: null,
      }),
    )
    expect(html).not.toContain('data-testid="post-generation-result"')
    expect(html).not.toContain("Generate Prototype")
  })
})

describe("DesignAgentLauncher module — no dead exports (AC4/AC13)", () => {
  it("resultFromGeneration/failureFromGeneration/pendingKey/pollUntilAdvanced/refreshShareTokenStep/defaultRenderDrawer are all gone (test_launcher_module_has_no_dead_exports)", async () => {
    const mod: Record<string, unknown> = await import("../DesignAgentLauncher")
    expect(mod.resultFromGeneration).toBeUndefined()
    expect(mod.failureFromGeneration).toBeUndefined()
    expect(mod.pendingKey).toBeUndefined()
    expect(mod.pollUntilAdvanced).toBeUndefined()
    expect(mod.refreshShareTokenStep).toBeUndefined()
    expect(mod.defaultRenderDrawer).toBeUndefined()
  })
})

describe("DesignAgentLauncher source — no dead imports (AC3, content invariant)", () => {
  it("the source file references none of the deleted drawer/result/comment/iterate/clarify/generation symbols (test_launcher_source_has_no_dead_imports)", () => {
    for (const symbol of [
      "DesignAgentDrawer",
      "PostGenerationResult",
      "GenerationErrorBanner",
      "CommentsPanel",
      "IterateComposer",
      "ClarifyingQuestionSurface",
      "runDesignAgentGeneration",
    ]) {
      expect(LAUNCHER_SRC).not.toContain(symbol)
    }
  })
})
