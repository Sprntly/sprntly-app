import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { afterEach, describe, expect, it, vi } from "vitest"
import {
  DesignAgentLauncher,
  DesignAgentLauncherView,
  type LauncherDrawerProps,
} from "../DesignAgentLauncher"

// PrdSections-style shim: Sprntly components have no `import React`; vitest's
// esbuild transform defaults to the classic runtime, so expose React globally
// rather than touch the shared vitest config (outside the engagement's map).
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const noop = () => {}

afterEach(() => {
  vi.restoreAllMocks()
})

/** Spy drawer renderer: records the props the launcher forwards, renders
 *  nothing. Lets the launcher render under node-env vitest without the real
 *  drawer's NavigationContext dependency. */
function makeDrawerSpy() {
  const calls: LauncherDrawerProps[] = []
  const renderDrawer = (props: LauncherDrawerProps) => {
    calls.push(props)
    return null
  }
  return { calls, renderDrawer }
}

describe("DesignAgentLauncher — button + wrapper markup", () => {
  it("renders a 'Generate Prototype' button (test_launcher_renders_button_with_label)", () => {
    const { renderDrawer } = makeDrawerSpy()
    const html = renderToStaticMarkup(
      React.createElement(DesignAgentLauncher, {
        prdId: 1,
        figmaFileKey: null,
        renderDrawer,
      }),
    )
    expect(html).toContain("Generate Prototype")
    expect(html).toMatch(/<button[^>]*type="button"/)
  })

  it("wraps the button in a contentEditable={false} div (test_launcher_button_wrapped_in_content_editable_false)", () => {
    const { renderDrawer } = makeDrawerSpy()
    const html = renderToStaticMarkup(
      React.createElement(DesignAgentLauncher, {
        prdId: 1,
        figmaFileKey: null,
        renderDrawer,
      }),
    )
    // The wrapper div carries contentEditable="false" and the button is nested
    // inside it — load-bearing so the button is clickable inside the PRD's
    // contentEditable region. Case-insensitive: HTML attributes are
    // case-insensitive and react-dom/server emits the camelCase form here.
    expect(html).toMatch(/<div[^>]*contenteditable="false"[^>]*>\s*<button/i)
  })
})

describe("DesignAgentLauncher — drawer state + prop forwarding", () => {
  it("mounts the drawer closed by default (test_launcher_drawer_closed_by_default)", () => {
    const { calls, renderDrawer } = makeDrawerSpy()
    renderToStaticMarkup(
      React.createElement(DesignAgentLauncher, {
        prdId: 1,
        figmaFileKey: null,
        renderDrawer,
      }),
    )
    expect(calls).toHaveLength(1)
    expect(calls[0].open).toBe(false)
  })

  it("forwards prdId to the drawer (test_launcher_passes_prdid_to_drawer)", () => {
    const { calls, renderDrawer } = makeDrawerSpy()
    renderToStaticMarkup(
      React.createElement(DesignAgentLauncher, {
        prdId: 42,
        figmaFileKey: null,
        renderDrawer,
      }),
    )
    expect(calls[0].prdId).toBe(42)
  })

  it("forwards figmaFileKey when present (test_launcher_passes_figma_file_key_when_present)", () => {
    const { calls, renderDrawer } = makeDrawerSpy()
    renderToStaticMarkup(
      React.createElement(DesignAgentLauncher, {
        prdId: 7,
        figmaFileKey: "abc123",
        renderDrawer,
      }),
    )
    expect(calls[0].figmaFileKey).toBe("abc123")
  })

  it("forwards figmaFileKey as undefined when absent (test_launcher_handles_figma_file_key_absent)", () => {
    const { calls, renderDrawer } = makeDrawerSpy()
    renderToStaticMarkup(
      React.createElement(DesignAgentLauncher, {
        prdId: 7,
        renderDrawer,
      }),
    )
    expect(calls[0].figmaFileKey).toBeUndefined()
  })
})

describe("DesignAgentLauncher — open interaction (DI)", () => {
  it("the button's onClick opens the drawer via setOpen(true) (test_launcher_click_opens_drawer)", () => {
    const setOpen = vi.fn()
    // The view is pure (no hooks), so calling it directly yields its element
    // tree; we extract the button and invoke its handler — no DOM needed.
    const tree = DesignAgentLauncherView({
      prdId: 1,
      figmaFileKey: null,
      open: false,
      setOpen,
      renderDrawer: () => null,
    }) as React.ReactElement
    const children = React.Children.toArray(
      (tree.props as { children: React.ReactNode }).children,
    ) as React.ReactElement[]
    const button = children.find((c) => c.type === "button")
    expect(button).toBeTruthy()
    ;(button!.props as { onClick: () => void }).onClick()
    expect(setOpen).toHaveBeenCalledWith(true)
  })

  it("forwards onOpenChange === setOpen so the drawer can close itself", () => {
    const setOpen = vi.fn()
    const { calls, renderDrawer } = makeDrawerSpy()
    DesignAgentLauncherView({
      prdId: 1,
      figmaFileKey: null,
      open: true,
      setOpen,
      renderDrawer,
    })
    expect(calls[0].onOpenChange).toBe(setOpen)
    calls[0].onOpenChange(false)
    expect(setOpen).toHaveBeenCalledWith(false)
  })
})
