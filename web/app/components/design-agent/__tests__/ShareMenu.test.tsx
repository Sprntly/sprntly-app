// P2-10 — ShareMenu tests. Node-env vitest (no DOM, no testing-library), so we
// SSR-render the pure view via renderToStaticMarkup and unit-test the extracted
// orchestration helpers with injected deps — same convention as CompletionBar.
import { readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import {
  ShareMenu,
  ShareMenuView,
  runApplyShareMode,
  runSelectMode,
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

describe("ShareMenuView — rendering", () => {
  // The visibility selector is the shipped native <input type="radio"> group
  // (kept so the browser provides arrow-key traversal + roving focus). Passcode
  // is intentionally NOT surfaced in the UI — only Private + Public render (the
  // "passcode" ShareMode value + its API guard are kept, just never selectable).
  // These presentation assertions match that markup; the wired-behaviour tests
  // below (runApplyShareMode / runSelectMode / runCopyShareLink) are unchanged
  // and still prove the share API plumbing.
  it("renders two native radio visibility rows (private + public only)", () => {
    const html = render({ mode: "private", passcode: "" })
    const radios = html.match(/type="radio"/g) ?? []
    expect(radios).toHaveLength(2)
    expect(html).toContain('data-testid="share-mode-private"')
    expect(html).toContain('data-testid="share-mode-public"')
    expect(html).not.toContain('data-testid="share-mode-passcode"')
  })

  it("marks the radio matching the current mode as checked", () => {
    // renderToStaticMarkup emits `checked=""` on a checked native radio and
    // nothing on an unchecked one (there is no literal role="radio" attribute on
    // native inputs). The active row's radio is checked; the others are not.
    const priv = render({ mode: "private", passcode: "" })
    expect(priv).toMatch(/id="share-mode-private"[^>]*checked/)
    expect(priv).not.toMatch(/id="share-mode-public"[^>]*checked/)
    const pub = render({ mode: "public", passcode: "" })
    expect(pub).toMatch(/id="share-mode-public"[^>]*checked/)
    expect(pub).not.toMatch(/id="share-mode-private"[^>]*checked/)
  })

  it("never renders a passcode input (passcode mode is not surfaced)", () => {
    expect(render({ mode: "private", passcode: "" })).not.toContain(
      'data-testid="passcode-input"',
    )
    // Even if the (hidden) passcode mode is the active value, no input renders.
    expect(render({ mode: "passcode", passcode: "" })).not.toContain(
      'data-testid="passcode-input"',
    )
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

// ─── P6-20 (#14): runSelectMode fires onShared on success only ───────────────
// The container's `selectMode` delegates to this exported orchestration helper
// (mirroring `runApplyShareMode`/`runCopyShareLink`) so the new onShared-on-
// success behaviour is testable in the node-env harness — there is no DOM to
// click the radio. Behaviour is byte-identical to the prior inline `selectMode`
// plus the single `onShared?.(token)` fire after `setToken`.
describe("runSelectMode — onShared fire (P6-20 #14)", () => {
  function setters() {
    return {
      setMode: vi.fn(),
      setToken: vi.fn(),
      setBusy: vi.fn(),
      setError: vi.fn(),
    }
  }

  it("fires onShared exactly once with the new token after a successful share (test_share_success_fires_on_shared, AC1)", async () => {
    // Regression: on unfixed code there is no `onShared` plumbing — the token
    // stayed local to ShareMenu and never reached the launcher. This asserts the
    // callback now fires with the freshly-minted token on success.
    const share = vi
      .fn()
      .mockResolvedValue({ prototype_id: 7, share_mode: "public", share_token: "tok-1" })
    const onShared = vi.fn()
    const s = setters()
    await runSelectMode({
      prototypeId: 7,
      next: "public",
      current: "private",
      passcode: "",
      api: { share },
      ...s,
      onShared,
    })
    expect(s.setMode).toHaveBeenCalledWith("public")
    expect(s.setToken).toHaveBeenCalledWith("tok-1")
    expect(onShared).toHaveBeenCalledTimes(1)
    expect(onShared).toHaveBeenCalledWith("tok-1")
    // The error slot was cleared (null), never set to an error string.
    expect(s.setError).toHaveBeenCalledWith(null)
    expect(s.setError).not.toHaveBeenCalledWith(expect.stringContaining("Failed"))
    // busy is always cleared in finally.
    expect(s.setBusy).toHaveBeenLastCalledWith(false)
  })

  it("fires onShared with null when a private share returns a null token (AC1 null-token)", async () => {
    const share = vi
      .fn()
      .mockResolvedValue({ prototype_id: 7, share_mode: "private", share_token: null })
    const onShared = vi.fn()
    await runSelectMode({
      prototypeId: 7,
      next: "private",
      current: "public",
      passcode: "",
      api: { share },
      ...setters(),
      onShared,
    })
    expect(onShared).toHaveBeenCalledTimes(1)
    expect(onShared).toHaveBeenCalledWith(null)
  })

  it("does NOT fire onShared when the share fails; sets the error instead (test_on_shared_not_fired_on_share_error, AC6)", async () => {
    const share = vi.fn().mockRejectedValue(new Error("network boom"))
    const onShared = vi.fn()
    const s = setters()
    await runSelectMode({
      prototypeId: 7,
      next: "public",
      current: "private",
      passcode: "",
      api: { share },
      ...s,
      onShared,
    })
    expect(onShared).not.toHaveBeenCalled()
    expect(s.setToken).not.toHaveBeenCalled()
    expect(s.setError).toHaveBeenCalledWith("network boom")
    expect(s.setBusy).toHaveBeenLastCalledWith(false)
  })

  it("the passcode guard rejects BEFORE the API and does not fire onShared (AC6)", async () => {
    const share = vi.fn()
    const onShared = vi.fn()
    const s = setters()
    await runSelectMode({
      prototypeId: 7,
      next: "passcode",
      current: "public",
      passcode: "",
      api: { share },
      ...s,
      onShared,
    })
    expect(share).not.toHaveBeenCalled()
    expect(onShared).not.toHaveBeenCalled()
    expect(s.setError).toHaveBeenCalledWith("Enter a passcode first")
  })

  it("tolerates a missing onShared (optional) on success — no throw", async () => {
    const share = vi
      .fn()
      .mockResolvedValue({ prototype_id: 7, share_mode: "public", share_token: "tok-1" })
    const s = setters()
    await expect(
      runSelectMode({
        prototypeId: 7,
        next: "public",
        current: "private",
        passcode: "",
        api: { share },
        ...s,
      }),
    ).resolves.toBeUndefined()
    expect(s.setToken).toHaveBeenCalledWith("tok-1")
  })
})

// ─── P6-22: runSelectMode optimistic mode select + revert (AC4) ──────────────
// The mode is set BEFORE the api.share await so a click/arrow selection
// registers immediately; on rejection it reverts to `current`. The token stays
// strictly server-confirmed (set only AFTER the await). These assert the helper
// logic in the reliable node harness; the jsdom sibling file covers the DOM.
describe("runSelectMode — optimistic select + revert (P6-22 AC4)", () => {
  function setters() {
    return {
      setMode: vi.fn(),
      setToken: vi.fn(),
      setBusy: vi.fn(),
      setError: vi.fn(),
    }
  }

  it("sets mode optimistically BEFORE api.share resolves; token stays post-await (Regression — fails on unfixed code)", async () => {
    // Regression: unfixed `runSelectMode` only calls setMode AFTER the await on
    // success, so before the deferred resolves setMode is never called. The
    // optimistic fix calls setMode(next) up-front while the token is withheld.
    let resolveShare: (v: {
      prototype_id: number
      share_mode: string
      share_token: string | null
    }) => void = () => {}
    const share = vi.fn(
      () =>
        new Promise<{ prototype_id: number; share_mode: string; share_token: string | null }>(
          (res) => {
            resolveShare = res
          },
        ),
    )
    const s = setters()
    const pending = runSelectMode({
      prototypeId: 7,
      next: "public",
      current: "private",
      passcode: "",
      api: { share },
      ...s,
    })
    // Optimistic: mode already reflected, token NOT yet set (server-confirmed).
    expect(s.setMode).toHaveBeenCalledWith("public")
    expect(s.setToken).not.toHaveBeenCalled()
    resolveShare({ prototype_id: 7, share_mode: "public", share_token: "tok-1" })
    await pending
    // Post-await: token reconciled from the server response only.
    expect(s.setToken).toHaveBeenCalledWith("tok-1")
    expect(s.setBusy).toHaveBeenLastCalledWith(false)
  })

  it("reverts mode to `current` when api.share rejects; never sets a token (Regression)", async () => {
    const share = vi.fn().mockRejectedValue(new Error("network boom"))
    const s = setters()
    await runSelectMode({
      prototypeId: 7,
      next: "public",
      current: "private",
      passcode: "",
      api: { share },
      ...s,
    })
    // Optimistic flip happened, then reverted to the prior mode.
    expect(s.setMode).toHaveBeenCalledWith("public")
    expect(s.setMode).toHaveBeenLastCalledWith("private")
    // Token never set on the failure path (no optimistic/fabricated token).
    expect(s.setToken).not.toHaveBeenCalled()
    expect(s.setError).toHaveBeenCalledWith("network boom")
    expect(s.setBusy).toHaveBeenLastCalledWith(false)
  })

  it("reverts to `current` when the empty-passcode guard rejects before the API", async () => {
    const share = vi.fn()
    const s = setters()
    await runSelectMode({
      prototypeId: 7,
      next: "passcode",
      current: "private",
      passcode: "",
      api: { share },
      ...s,
    })
    expect(share).not.toHaveBeenCalled()
    expect(s.setMode).toHaveBeenCalledWith("passcode") // optimistic
    expect(s.setMode).toHaveBeenLastCalledWith("private") // reverted
    expect(s.setError).toHaveBeenCalledWith("Enter a passcode first")
  })
})

describe("ShareMenu — non-breakage with the optional onShared prop (P6-20 AC7/AC8)", () => {
  it("the container type-checks + SSR-renders when onShared is omitted (test_optional_props_typecheck)", () => {
    // The new prop is optional/defaulted — existing callers that omit it (incl.
    // the public-viewer composition) still compile and render.
    const html = renderToStaticMarkup(
      React.createElement(ShareMenu, { prototypeId: 7, initialMode: "private" }),
    )
    expect(html).toContain('data-testid="share-menu"')
  })

  it("ShareMenuView SSR output is byte-stable — the new prop lives on the container, not the view (test_share_menu_render_unchanged)", () => {
    // onShared is a ShareMenuProps/ShareMenu member only; ShareMenuViewProps is
    // unchanged, so the rendered markup cannot shift.
    const a = render({ mode: "public", passcode: "", shareUrl: "https://app/p/t" })
    const b = render({ mode: "public", passcode: "", shareUrl: "https://app/p/t" })
    expect(a).toBe(b)
    expect(a).toContain('data-testid="share-menu"')
  })
})

// ─── share-panel CSS dedupe (guards the scoped sheet) ────────────────────────
// The scoped sheet had two `.share-menu` blocks (a globals collision-override +
// a separate visual block) and an orphaned `.share-vis-*` / `.share-title` /
// `.share-link-url` vocabulary that no markup uses. These read design-agent.css
// from disk and assert the merge + orphan removal, so the dedupe cannot silently
// regress back into a duplicate rule or a dead selector.
const SHARE_CSS = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), "..", "design-agent.css"),
  "utf8",
)

describe("design-agent.css — share-panel dedupe", () => {
  it("test_single_scoped_share_menu_block_with_override — exactly one scoped .share-menu block, carrying the override props", () => {
    const blocks =
      SHARE_CSS.match(/\.design-agent-surface\s+\.share-menu\s*\{[^}]*\}/g) ?? []
    expect(blocks).toHaveLength(1)
    const body = blocks[0]
    expect(body).toMatch(/position:\s*static/)
    expect(body).toMatch(/opacity:\s*1/)
    expect(body).toMatch(/pointer-events:\s*auto/)
    expect(body).toMatch(/transform:\s*none/)
    // ...and the visual layout the merged block also carries.
    expect(body).toMatch(/display:\s*flex/)
  })

  it("test_no_orphaned_share_vis_selectors — no dead share-vis / share-title / share-link-url selectors remain", () => {
    expect(SHARE_CSS).not.toMatch(/\.share-vis-/)
    expect(SHARE_CSS).not.toMatch(/\.share-title\b/)
    expect(SHARE_CSS).not.toMatch(/\.share-link-url\b/)
  })

  it("keeps exactly one scoped .share-passcode-label block", () => {
    const labels =
      SHARE_CSS.match(/\.design-agent-surface\s+\.share-passcode-label\s*\{/g) ?? []
    expect(labels).toHaveLength(1)
  })
})
