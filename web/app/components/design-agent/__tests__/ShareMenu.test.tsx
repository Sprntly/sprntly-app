// P2-10 — ShareMenu tests. Node-env vitest (no DOM, no testing-library), so we
// SSR-render the pure view via renderToStaticMarkup and unit-test the extracted
// orchestration helpers with injected deps — same convention as CompletionBar.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import {
  ShareMenuView,
  runApplyShareMode,
  runCopyShareLink,
  buildShareUrl,
} from "../ShareMenu"

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

function render(props: React.ComponentProps<typeof ShareMenuView>): string {
  return renderToStaticMarkup(React.createElement(ShareMenuView, props))
}

describe("ShareMenuView — rendering (AC8)", () => {
  it("renders three radio buttons", () => {
    const html = render({ mode: "private", passcode: "" })
    const radios = html.match(/type="radio"/g) ?? []
    expect(radios).toHaveLength(3)
    expect(html).toContain('value="private"')
    expect(html).toContain('value="public"')
    expect(html).toContain('value="passcode"')
  })

  it("checks the radio matching the current mode", () => {
    // Attribute order is React's concern; assert the checked input is the one
    // carrying the active mode's value (order-independent).
    expect(render({ mode: "private", passcode: "" })).toMatch(
      /<input[^>]*checked[^>]*value="private"[^>]*>/,
    )
    expect(render({ mode: "public", passcode: "" })).toMatch(
      /<input[^>]*checked[^>]*value="public"[^>]*>/,
    )
    // ...and the non-active radios are not checked.
    expect(render({ mode: "private", passcode: "" })).not.toMatch(
      /<input[^>]*checked[^>]*value="public"[^>]*>/,
    )
  })

  it("disables the passcode input when mode is not passcode", () => {
    const html = render({ mode: "private", passcode: "" })
    expect(html).toMatch(/data-testid="passcode-input"[^>]*disabled/)
  })

  it("enables the passcode input when mode is passcode", () => {
    const html = render({ mode: "passcode", passcode: "" })
    expect(html).not.toMatch(/data-testid="passcode-input"[^>]*disabled/)
  })
})

describe("runApplyShareMode — mode change", () => {
  it("selecting public calls share with the public mode (AC9)", async () => {
    const share = vi
      .fn()
      .mockResolvedValue({ prototype_id: 7, share_mode: "public", share_token: "tok-abc" })
    const result = await runApplyShareMode({
      prototypeId: 7,
      next: "public",
      passcode: "",
      api: { share },
    })
    expect(share).toHaveBeenCalledWith(7, { mode: "public" })
    expect(result).toEqual({ mode: "public", token: "tok-abc" })
  })

  it("selecting passcode without a passcode rejects and does NOT call the API (AC10)", async () => {
    const share = vi.fn()
    await expect(
      runApplyShareMode({ prototypeId: 7, next: "passcode", passcode: "", api: { share } }),
    ).rejects.toThrow("Enter a passcode first")
    expect(share).not.toHaveBeenCalled()

    // ...and the view surfaces that error.
    const html = render({ mode: "passcode", passcode: "", error: "Enter a passcode first" })
    expect(html).toContain('data-testid="share-menu-error"')
    expect(html).toContain("Enter a passcode first")
  })

  it("selecting passcode WITH a passcode calls share with the passcode body (AC11)", async () => {
    const share = vi
      .fn()
      .mockResolvedValue({ prototype_id: 7, share_mode: "passcode", share_token: "tok-xyz" })
    await runApplyShareMode({
      prototypeId: 7,
      next: "passcode",
      passcode: "hunter2",
      api: { share },
    })
    expect(share).toHaveBeenCalledWith(7, { mode: "passcode", passcode: "hunter2" })
  })
})

describe("ShareMenuView — share link", () => {
  it("renders the share link + copy button when a public URL is present (AC9)", () => {
    const html = render({
      mode: "public",
      passcode: "",
      shareUrl: "https://app.sprntly.ai/p/tok-abc",
    })
    expect(html).toContain('data-testid="share-link"')
    expect(html).toContain('data-testid="copy-link-btn"')
    expect(html).toContain("https://app.sprntly.ai/p/tok-abc")
  })

  it("does not render the share link when private (no URL)", () => {
    const html = render({ mode: "private", passcode: "", shareUrl: null })
    expect(html).not.toContain('data-testid="share-link"')
  })
})

describe("share-link helpers", () => {
  it("buildShareUrl composes origin + /p/ + token (F6)", () => {
    expect(buildShareUrl("tok-abc", "https://app.sprntly.ai")).toBe(
      "https://app.sprntly.ai/p/tok-abc",
    )
  })

  it("runCopyShareLink writes the share URL to the clipboard (AC12)", async () => {
    const writeText = vi.fn(async (_: string) => {})
    const url = await runCopyShareLink({
      token: "tok-abc",
      origin: "https://app.sprntly.ai",
      clipboard: { writeText },
    })
    expect(writeText).toHaveBeenCalledWith("https://app.sprntly.ai/p/tok-abc")
    expect(url).toBe("https://app.sprntly.ai/p/tok-abc")
  })
})
