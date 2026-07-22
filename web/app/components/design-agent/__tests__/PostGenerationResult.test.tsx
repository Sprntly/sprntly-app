// P2-12 — PostGenerationResult tests. Node-env vitest (no DOM, no router, no
// @testing-library), so — following the CompletionBar / DesignAgentDrawer
// convention — we SSR-render the pure view via renderToStaticMarkup and
// unit-test the extracted pure helper directly.
import { readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { afterEach, describe, expect, it, vi } from "vitest"

// Sprntly components carry no `import React`; vitest's esbuild transform uses
// the classic runtime, so expose React globally (PrdSections/CompletionBar test
// convention) rather than touch the shared vitest config.
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import {
  PostGenerationResult,
  PostGenerationResultView,
  DaControlBar,
  resolveViewHref,
  reseedStep,
  viewerSrc,
  viewerRemountKey,
  type PostGenerationResultViewProps,
  type ReseedBaseline,
} from "../PostGenerationResult"
import { ShareMenu } from "../ShareMenu"
import type { PrototypeRecord } from "../../../lib/api"

afterEach(() => {
  vi.restoreAllMocks()
})

function renderView(
  over: Partial<PostGenerationResultViewProps> = {},
): string {
  return renderToStaticMarkup(
    React.createElement(PostGenerationResultView, {
      prototypeId: 42,
      isComplete: false,
      shareMode: "private",
      shareToken: null,
      bundleUrl: null,
      ...over,
    }),
  )
}

function countOccurrences(haystack: string, needle: string): number {
  let count = 0
  let idx = haystack.indexOf(needle)
  while (idx !== -1) {
    count += 1
    idx = haystack.indexOf(needle, idx + needle.length)
  }
  return count
}

function proto(over: Partial<PrototypeRecord> = {}): PrototypeRecord {
  return {
    id: 42,
    status: "ready",
    bundle_url: null,
    error: null,
    ...over,
  }
}

// the control bar is now a COMPACT single row.
// The full CompletionBar (Mark Complete / Resume / Export) + full ShareMenu
// radios are NO LONGER rendered inline — they live inside closed dropdown
// popovers (Actions ⋯ / Share) that only open client-side. So the SSR markup now
// asserts the COMPACT TRIGGERS exist, not the expanded panels.
describe("PostGenerationResultView — compact control bar", () => {
  it("renders the compact control bar with the Actions (handoff) + Share triggers, not the inline panels", () => {
    const html = renderView({ isComplete: false })
    expect(html).toContain('data-testid="da-controlbar"')
    // Compact triggers present…
    expect(html).toContain('data-testid="da-actions-toggle"')
    expect(html).toContain('data-testid="da-share-toggle"')
    // …and the expanded CompletionBar / ShareMenu panels are NOT inline (closed
    // popovers): the bar never bloats with the full button stack / radios.
    expect(html).not.toContain('data-testid="mark-complete-btn"')
    expect(html).not.toContain('data-testid="share-menu"')
  })

  it("mounts the result wrapper", () => {
    const html = renderView()
    expect(html).toContain('data-testid="post-generation-result"')
  })

  it("renders the LEFT-cluster Desktop/Mobile platform toggle in the bar", () => {
    const html = renderView()
    expect(html).toContain('class="da-controlbar-l"')
    expect(html).toContain('class="platform-toggle da-controlbar-platform"')
  })

  it("never renders the read-only completion bar on the editable surface (test_is_editable_not_readonly)", () => {
    const html = renderView({ isComplete: false })
    expect(html).not.toContain('data-testid="completion-bar-readonly"')
  })
})

// ─── P6-16 (UX-6): always-shown full-screen View affordance ──────────────────
// INVERTED from the obsolete "View prototype link" block: the `view-prototype-link`
// anchor (silently hidden when resolveViewHref returned null; a chrome-less raw
// new tab otherwise) is replaced by an ALWAYS-shown "View full screen" trigger
// (enabled when a bundle exists, disabled-with-label otherwise) + a full-screen
// overlay that reuses the P6-12 device frame. resolveViewHref is KEPT (its pure
// describe below still passes) but no longer gates a hidden link.
describe("PostGenerationResultView — View affordance never dead (P6-16 AC1/AC4b)", () => {
  // the always-shown View control is now the
  // COMPACT `proto-fullscreen-trigger` icon button in the control bar (renamed
  // from the prior center-toolbar `view-fullscreen-trigger`). Still never a dead
  // link: enabled when a bundle exists, disabled-with-title otherwise.
  it("test_view_affordance_never_hidden_when_no_bundle — always renders a (disabled) View control with no bundle or token", () => {
    const html = renderView({ bundleUrl: null, shareToken: null })
    expect(html).toContain('data-testid="proto-fullscreen-trigger"')
    // Disabled + explanatory title — present, NOT removed.
    expect(html).toMatch(/data-testid="proto-fullscreen-trigger"[^>]*disabled/)
    expect(html).toContain("Prototype building")
    // The obsolete dead-end anchor is gone entirely.
    expect(html).not.toContain('data-testid="view-prototype-link"')
  })

  it("test_view_affordance_enabled_when_bundle — enabled 'View full screen' trigger when a bundle exists", () => {
    const html = renderView({ bundleUrl: "https://cdn/x/bundle/index.html" })
    expect(html).toContain('data-testid="proto-fullscreen-trigger"')
    expect(html).toContain("View full screen")
    expect(html).not.toMatch(/data-testid="proto-fullscreen-trigger"[^>]*disabled/)
  })

  it("no bundle but shared (token present) still shows the always-present control, not the old /p link dead-end", () => {
    const html = renderView({ bundleUrl: null, shareToken: "tok-123" })
    // Always-shown control present (disabled — no bundle to open yet).
    expect(html).toContain('data-testid="proto-fullscreen-trigger"')
    expect(html).toMatch(/data-testid="proto-fullscreen-trigger"[^>]*disabled/)
    // No raw new-tab /p link affordance remains here (ShareMenu owns sharing).
    expect(html).not.toContain('data-testid="view-prototype-link"')
  })

  it("the inline iframe follows a refreshed bundle_url (test_preview_refreshes_after_iterate, AC4)", () => {
    // bundle_url is read straight from the prop, so a refreshed record
    // (post-iterate, same id) re-renders the inline viewer iframe onto the NEW
    // bundle with no manual remount — the #5 staleness P6-05 fixed, preserved here.
    const oldHtml = renderView({ bundleUrl: "https://cdn/OLD/index.html" })
    expect(oldHtml).toContain("https://cdn/OLD/index.html")
    const newHtml = renderView({ bundleUrl: "https://cdn/NEW/index.html" })
    expect(newHtml).toContain("https://cdn/NEW/index.html")
    expect(newHtml).not.toContain("https://cdn/OLD/index.html")
  })
})

describe("PostGenerationResultView — full-screen overlay (P6-16 AC2/AC3/AC3b)", () => {
  const BUNDLE = "https://cdn/x/bundle/index.html"

  it("test_trigger_opens_overlay — fullscreenOpen renders the proto-fullscreen dialog; closed renders none", () => {
    const open = renderView({ bundleUrl: BUNDLE, fullscreenOpen: true })
    expect(open).toContain('data-testid="proto-fullscreen"')
    expect(open).toMatch(/role="dialog"/)
    expect(open).toMatch(/aria-modal="true"/)
    expect(open).toContain('data-testid="proto-fullscreen-close"')

    const closed = renderView({ bundleUrl: BUNDLE, fullscreenOpen: false })
    expect(closed).not.toContain('data-testid="proto-fullscreen"')
  })

  it("keeps the fullscreen exit (close X) for a single-device prototype — never traps", () => {
    // A single-device prototype suppresses the in-frame toggle + chrome head in
    // fullscreen; the exit MUST still render (Escape/browser-back is not an
    // acceptable only-way-out). Both mobile-only and desktop-only.
    const mobileOnly = renderView({
      bundleUrl: BUNDLE,
      fullscreenOpen: true,
      showDesktop: false,
      showMobile: true,
      platform: "mobile",
    })
    expect(mobileOnly).toContain('data-testid="proto-fullscreen-close"')
    // and the toggle is gated away (the actual single-device behaviour)
    expect(mobileOnly).not.toContain('aria-label="Preview platform"')

    const desktopOnly = renderView({
      bundleUrl: BUNDLE,
      fullscreenOpen: true,
      showDesktop: true,
      showMobile: false,
      platform: "desktop",
    })
    expect(desktopOnly).toContain('data-testid="proto-fullscreen-close"')
  })

  it("never renders the overlay without a bundle even if fullscreenOpen is true", () => {
    const html = renderView({ bundleUrl: null, fullscreenOpen: true })
    expect(html).not.toContain('data-testid="proto-fullscreen"')
  })

  it("test_overlay_mounts_device_frame — the open overlay mounts a <PrototypeViewer> (proto-frame device chrome), not a bare iframe", () => {
    const html = renderView({ bundleUrl: BUNDLE, fullscreenOpen: true })
    // The device frame (P6-12) is present inside the overlay.
    expect(html).toContain('class="proto-frame"')
    // The viewer's iframe carries the locked className (inside proto-frame),
    // i.e. it is a PrototypeViewer, not a top-level bare <iframe>.
    expect(html).toContain('class="da-prototype-iframe"')
    // The same bundle url is passed to the overlay viewer.
    expect(html).toContain(BUNDLE)
  })

  it("test_overlay_viewer_does_not_shadow_inline_edit_iframe — at most ONE da-prototype-iframe at any instant (AC3b)", () => {
    // Overlay closed: exactly one (inline) iframe. ManualEditOverlay trigger is
    // intentionally absent from the canvas — no shadowing risk.
    const closed = renderView({ bundleUrl: BUNDLE, fullscreenOpen: false })
    expect(countOccurrences(closed, 'class="da-prototype-iframe"')).toBe(1)
    expect(closed).not.toContain('data-testid="manual-edit-overlay"')

    // Overlay open: the inline viewer is unmounted, leaving exactly one
    // (overlay, view-only) iframe — the single-iframe invariant holds.
    const open = renderView({ bundleUrl: BUNDLE, fullscreenOpen: true })
    expect(countOccurrences(open, 'class="da-prototype-iframe"')).toBe(1)
    expect(open).not.toContain('data-testid="manual-edit-overlay"')
  })
})

// ─── full-screen overlay honours target_platform ────────────────────────────
// Regression: the fullscreen overlay rendered its OWN in-frame toggle (not the
// gated control-bar toggle), so a single-device prototype showed BOTH buttons
// AND opened on Desktop. The container now threads showDesktop/showMobile +
// initialPlatform into FullscreenOverlay so it matches the inline gating.
describe("PostGenerationResultView — full-screen overlay honours target_platform", () => {
  const BUNDLE = "https://cdn/x/bundle/index.html"

  // Isolate the fullscreen dialog's markup from the always-rendered control bar
  // (which owns its own gated toggle) — the overlay is the last node rendered.
  function fullscreenHtml(html: string): string {
    const idx = html.indexOf('data-testid="proto-fullscreen"')
    return idx === -1 ? "" : html.slice(idx)
  }

  it("mobile-only prototype in fullscreen hides the toggle group and opens on mobile", () => {
    const fs = fullscreenHtml(
      renderView({
        bundleUrl: BUNDLE,
        fullscreenOpen: true,
        showDesktop: false,
        showMobile: true,
        platform: "mobile",
      }),
    )
    // The in-frame Desktop/Mobile toggle is NOT in the fullscreen DOM.
    expect(fs).not.toContain('aria-label="Preview platform"')
    expect(fs).not.toContain('class="platform-toggle"')
    // …and the stage opens on the single (mobile) device, not the Desktop default.
    expect(fs).toContain('class="proto-stage mobile"')
    expect(fs).not.toContain('class="proto-stage desktop"')
  })

  it("both-target prototype in fullscreen shows both toggle buttons", () => {
    const fs = fullscreenHtml(
      renderView({
        bundleUrl: BUNDLE,
        fullscreenOpen: true,
        showDesktop: true,
        showMobile: true,
        platform: "desktop",
      }),
    )
    expect(fs).toContain('aria-label="Preview platform"')
    expect(fs).toMatch(/>Desktop<\/button>/)
    expect(fs).toMatch(/>Mobile<\/button>/)
  })
})

// ─── single-device fullscreen presentation ──────────────────────────────────
// A single-device prototype keeps a slim chrome bar in fullscreen (chrome-less
// reads as broken); the vacated toggle slot is filled with a settled device
// indicator, and the bare "×" is upgraded to a labeled Close pill. Both-device
// keeps the live toggle and adopts the same Close pill.
describe("PostGenerationResultView — single-device fullscreen presentation", () => {
  const BUNDLE = "https://cdn/x/bundle/index.html"

  function fullscreenHtml(html: string): string {
    const idx = html.indexOf('data-testid="proto-fullscreen"')
    return idx === -1 ? "" : html.slice(idx)
  }

  it("mobile-only fullscreen shows a Mobile device indicator + labeled Close pill", () => {
    const fs = fullscreenHtml(
      renderView({
        bundleUrl: BUNDLE,
        fullscreenOpen: true,
        showDesktop: false,
        showMobile: true,
        platform: "mobile",
      }),
    )
    expect(fs).toContain('class="proto-fs-device"')
    expect(fs).toMatch(/proto-fs-device[\s\S]*Mobile/)
    expect(fs).toContain('class="proto-fs-close-label"')
    expect(fs).toMatch(/proto-fs-close-label">Close/)
    // exit stays intact
    expect(fs).toContain('data-testid="proto-fullscreen-close"')
  })

  it("desktop-only fullscreen shows a Desktop device indicator + labeled Close pill", () => {
    const fs = fullscreenHtml(
      renderView({
        bundleUrl: BUNDLE,
        fullscreenOpen: true,
        showDesktop: true,
        showMobile: false,
        platform: "desktop",
      }),
    )
    expect(fs).toContain('class="proto-fs-device"')
    expect(fs).toMatch(/proto-fs-device[\s\S]*Desktop/)
    expect(fs).toContain('class="proto-fs-close-label"')
    expect(fs).toContain('data-testid="proto-fullscreen-close"')
  })

  it("both-device fullscreen keeps the live toggle, no device indicator, same Close pill", () => {
    const fs = fullscreenHtml(
      renderView({
        bundleUrl: BUNDLE,
        fullscreenOpen: true,
        showDesktop: true,
        showMobile: true,
        platform: "desktop",
      }),
    )
    expect(fs).toContain('aria-label="Preview platform"')
    expect(fs).not.toContain('class="proto-fs-device"')
    expect(fs).toContain('class="proto-fs-close-label"')
    expect(fs).toContain('data-testid="proto-fullscreen-close"')
  })

  it("single-device fullscreen keeps the toggle gated away (no regression)", () => {
    const fs = fullscreenHtml(
      renderView({
        bundleUrl: BUNDLE,
        fullscreenOpen: true,
        showDesktop: false,
        showMobile: true,
        platform: "mobile",
      }),
    )
    expect(fs).not.toContain('aria-label="Preview platform"')
    expect(fs).toContain('data-testid="proto-fullscreen-close"')
  })
})

// ─── control bar + 3-section body ───────────
// The signed-in post-gen surface mounts a compact control bar (`.da-controlbar`)
// + a `.da-ready` flex body with a LEFT collapsible sidebar (`.da-left`: PRD +
// iterate compose `da-canvas-iterate`), a CENTER full-area canvas (`.da-stage`,
// testid `da-canvas-center`), and a RIGHT collapsible comments sidebar
// (`.da-right`, testid `da-canvas-comments`) that — per Problem 2 — ALWAYS exists
// (with CommentsPanel when present, else an empty state).
describe("PostGenerationResultView — control bar + 3-section body", () => {
  const sentinel = (id: string, text: string) =>
    React.createElement("div", { "data-testid": id }, text)

  it("wraps a control bar + LEFT sidebar (iterate), CENTER canvas, RIGHT comments", () => {
    const html = renderView({
      bundleUrl: "https://cdn/x/bundle/index.html",
      iterate: sentinel("sentinel-iterate", "SENTINEL_ITERATE"),
      comments: sentinel("sentinel-comments", "SENTINEL_COMMENTS"),
    })
    expect(html).toContain('data-testid="da-controlbar"')
    expect(html).toContain('class="da-ready"')
    expect(html).toContain('data-testid="da-canvas-iterate"')
    expect(html).toContain('data-testid="da-canvas-center"')
    expect(html).toContain('data-testid="da-canvas-comments"')
    // The slots land in their regions; the viewer (its bundle url) is in the center.
    expect(html).toContain("https://cdn/x/bundle/index.html")
    expect(html).toContain('data-testid="sentinel-iterate"')
    expect(html).toContain('data-testid="sentinel-comments"')
    // Order: control bar → body → left → center → right.
    const barIdx = html.indexOf('data-testid="da-controlbar"')
    const bodyIdx = html.indexOf('class="da-ready"')
    const iterateIdx = html.indexOf('data-testid="da-canvas-iterate"')
    const centerIdx = html.indexOf('data-testid="da-canvas-center"')
    const commentsIdx = html.indexOf('data-testid="da-canvas-comments"')
    expect(barIdx).toBeGreaterThanOrEqual(0)
    expect(bodyIdx).toBeGreaterThan(barIdx)
    expect(centerIdx).toBeGreaterThan(iterateIdx)
    expect(commentsIdx).toBeGreaterThan(centerIdx)
  })

  it("Problem 2 — the RIGHT comments sidebar ALWAYS exists; unshared shows the empty state", () => {
    // No comments node (unshared): the sidebar shell + empty-state still render
    // so the control-bar comments-toggle has something to reveal.
    const html = renderView({ bundleUrl: "https://cdn/x/bundle/index.html" })
    expect(html).toContain('data-testid="da-canvas-comments"')
    expect(html).toContain('data-testid="da-comments-empty"')
    expect(html).toContain("to collect comments")
  })

  it("renders CommentsPanel content inside the right sidebar when a comments node is present", () => {
    const html = renderView({
      bundleUrl: "https://cdn/x/bundle/index.html",
      comments: sentinel("sentinel-comments", "SENTINEL_COMMENTS"),
    })
    expect(html).toContain('data-testid="da-canvas-comments"')
    expect(html).toContain('data-testid="sentinel-comments"')
    // The shared branch replaces the empty state.
    expect(html).not.toContain('data-testid="da-comments-empty"')
  })

  it("the right sidebar reflects the commentsOpen toggle via the `.open` class", () => {
    const closed = renderView({ bundleUrl: "https://cdn/x/bundle/index.html" })
    expect(closed).toContain('class="da-right"')
    const open = renderView({
      bundleUrl: "https://cdn/x/bundle/index.html",
      commentsOpen: true,
    })
    expect(open).toContain('class="da-right open"')
  })

  it("omits the iterate compose region when iterate is absent", () => {
    const html = renderView({ bundleUrl: "https://cdn/x/bundle/index.html" })
    expect(html).not.toContain('data-testid="da-canvas-iterate"')
  })

  it("places the View affordance inside the control bar", () => {
    const html = renderView({
      bundleUrl: "https://cdn/x/bundle/index.html",
      comments: sentinel("sentinel-comments", "SENTINEL_COMMENTS"),
    })
    const barIdx = html.indexOf('data-testid="da-controlbar"')
    const triggerIdx = html.indexOf('data-testid="proto-fullscreen-trigger"')
    const bodyIdx = html.indexOf('class="da-ready"')
    expect(barIdx).toBeGreaterThanOrEqual(0)
    expect(triggerIdx).toBeGreaterThan(barIdx)
    // The trigger is in the bar, before the body.
    expect(triggerIdx).toBeLessThan(bodyIdx)
  })
})

// ─── P6-05 (#5): guarded re-seed of the local isComplete on a refetch ────────

describe("reseedStep — guarded local-isComplete re-seed (AC4/AC5/AC10)", () => {
  it("re-seeds on a genuine checkpoint advance with a differing prop is_complete (test_post_generation_result_reseeds_is_complete_on_genuine_advance)", () => {
    // bundle_url changed AND prop is_complete (true) differs from baseline (false).
    const base: ReseedBaseline = { bundle: "old/bundle", complete: false }
    const out = reseedStep(base, "new/bundle", true)
    expect(out.setComplete).toBe(true)
    expect(out.baseline).toEqual({ bundle: "new/bundle", complete: true })
  })

  it("does NOT re-seed when only the bundle changed but prop is_complete equals the baseline", () => {
    // A checkpoint advance whose prop is_complete (false) matches baseline (false):
    // the baseline advances to the new bundle but the local copy is left alone.
    const base: ReseedBaseline = { bundle: "old/bundle", complete: false }
    const out = reseedStep(base, "new/bundle", false)
    expect(out.setComplete).toBeNull()
    expect(out.baseline).toEqual({ bundle: "new/bundle", complete: false })
  })

  it("no-ops when the bundle_url did not change (no checkpoint advance)", () => {
    const base: ReseedBaseline = { bundle: "same/bundle", complete: false }
    const out = reseedStep(base, "same/bundle", true)
    expect(out.setComplete).toBeNull()
    expect(out.baseline).toBe(base) // baseline unchanged
  })

  it("does NOT clobber a user's local Mark-Complete across prop changes (test_reseed_does_not_clobber_local_mark_complete)", () => {
    // Sequence: prop seeds is_complete=false (baseline). User marks complete
    // locally (the LOCAL copy is true; the baseline stays the prop-derived false).
    // 1) A prop re-render with the SAME bundle must not re-seed → local stays true.
    let baseline: ReseedBaseline = { bundle: "b1", complete: false }
    const sameBundle = reseedStep(baseline, "b1", false)
    expect(sameBundle.setComplete).toBeNull() // local Mark-Complete survives
    baseline = sameBundle.baseline

    // 2) A genuine checkpoint advance whose prop is_complete (false) equals the
    //    last prop-derived baseline (false) → still no spurious revert.
    const advanceSameComplete = reseedStep(baseline, "b2", false)
    expect(advanceSameComplete.setComplete).toBeNull()
    baseline = advanceSameComplete.baseline
    expect(baseline).toEqual({ bundle: "b2", complete: false })

    // 3) A checkpoint advance whose prop is_complete (true) differs from baseline
    //    (false) DOES re-seed — a real prop change, not a clobber of local state.
    const advanceDiffComplete = reseedStep(baseline, "b3", true)
    expect(advanceDiffComplete.setComplete).toBe(true)
  })
})

// ─── P6-20 (#14): forwards onShared down to <ShareMenu> ──────────────────────
// ShareMenu now lives inside DaControlBar's Share
// dropdown popover (DaControlBar → DaPopover → ShareMenu). The chain is
// PostGenerationResultView(onShared) → <DaControlBar onShared> → ... → <ShareMenu
// onShared>. We assert BOTH hops: the view forwards to <DaControlBar>, and
// DaControlBar threads it to the nested <ShareMenu>. We call each component as a
// function (no DOM render) and walk the returned element tree's `children`.
describe("PostGenerationResult — forwards onShared down to ShareMenu (P6-20 AC2)", () => {
  function findByType(
    node: React.ReactNode,
    type: React.ElementType,
  ): React.ReactElement | undefined {
    for (const child of React.Children.toArray(node) as React.ReactElement[]) {
      if (!child || typeof child !== "object") continue
      if (child.type === type) return child
      const props = child.props as {
        children?: React.ReactNode
        trigger?: (open: boolean) => React.ReactNode
      }
      // DaPopover passes its panel content via `children` and its trigger via a
      // `trigger` render-prop — descend into both so ShareMenu (in children) is found.
      const found =
        (props?.children ? findByType(props.children, type) : undefined) ??
        (typeof props?.trigger === "function"
          ? findByType(props.trigger(false), type)
          : undefined)
      if (found) return found
    }
    return undefined
  }

  function controlBarEl(
    over: Partial<PostGenerationResultViewProps> = {},
  ): React.ReactElement {
    const tree = PostGenerationResultView({
      prototypeId: 42,
      isComplete: false,
      shareMode: "private",
      shareToken: null,
      bundleUrl: null,
      ...over,
    }) as React.ReactElement
    const bar = findByType(tree, DaControlBar)
    expect(bar).toBeTruthy()
    return bar!
  }

  function shareMenuEl(
    over: Partial<PostGenerationResultViewProps> = {},
  ): React.ReactElement | undefined {
    // Render DaControlBar with the view-forwarded props to reach the nested ShareMenu.
    const barEl = controlBarEl(over)
    const rendered = DaControlBar(
      barEl.props as Parameters<typeof DaControlBar>[0],
    ) as React.ReactElement
    return findByType(rendered, ShareMenu)
  }

  it("the view forwards its onShared prop to <DaControlBar>", () => {
    const onShared = vi.fn()
    const bar = controlBarEl({ onShared })
    expect((bar.props as { onShared?: unknown }).onShared).toBe(onShared)
  })

  it("DaControlBar threads onShared down to the nested <ShareMenu> (test_post_generation_result_forwards_on_shared)", () => {
    const onShared = vi.fn()
    const share = shareMenuEl({ onShared })
    expect(share).toBeTruthy()
    expect((share!.props as { onShared?: unknown }).onShared).toBe(onShared)
  })

  it("ShareMenu receives onShared=undefined on the public-composition path (no handler supplied)", () => {
    const share = shareMenuEl({})
    expect(share).toBeTruthy()
    expect((share!.props as { onShared?: unknown }).onShared).toBeUndefined()
  })

  it("DaControlBar threads prdTitle down to the nested <ShareMenu> (test_post_generation_result_forwards_prd_title_to_share_menu, AC15)", () => {
    // ShareMenu slugifies prdTitle into the cosmetic feature segment of the
    // public share URL, so the title must reach it intact through the same
    // View → DaControlBar → ShareMenu chain that onShared travels.
    const share = shareMenuEl({ prdTitle: "Customer Onboarding Revamp" })
    expect(share).toBeTruthy()
    expect((share!.props as { prdTitle?: unknown }).prdTitle).toBe(
      "Customer Onboarding Revamp",
    )
  })
})

describe("resolveViewHref (pure)", () => {
  it("prefers the bundle url", () => {
    expect(resolveViewHref("https://b/x", "tok", "sprntly")).toBe("https://b/x")
  })
  it("falls back to the slug'd public token link", () => {
    // The public link now carries the cosmetic company slug:
    // /p/<slug>/<token> (intentional slug exposure — the one surface that
    // renders companies.slug). The slug is sourced at the call site from
    // useCompany().activeCompany.
    expect(resolveViewHref(null, "tok", "sprntly")).toBe("/p/sprntly/tok")
  })
  it("returns null when neither is available", () => {
    expect(resolveViewHref(null, null, "sprntly")).toBeNull()
  })
})

// ─── P6-13 (UX-3): CSS structural + public-viewer-unchanged invariants ───────
// WORKING-TREE content invariants read via fs — NEVER `git show <historical-rev>`
// / `git diff <sha>`, which fails under CI's shallow clone (fetch-depth=1). The
// "design-pane grid present" + "PublicTokenViewer does not use PostGenerationResult"
// checks assert the CURRENT tree's content (the AC intent), method free.

const HERE = dirname(fileURLToPath(import.meta.url))
// __tests__ → design-agent → components → app
const APP_DIR = join(HERE, "..", "..", "..")
const CSS = readFileSync(join(HERE, "..", "design-agent.css"), "utf8")
const PUBLIC_VIEWER = readFileSync(
  join(APP_DIR, "p", "PublicTokenViewer.tsx"),
  "utf8",
)
const RESULT_SRC = readFileSync(
  join(HERE, "..", "PostGenerationResult.tsx"),
  "utf8",
)
// C2b — the pin/mark logic was extracted VERBATIM into the shared usePinMarking
// hook so BOTH the signed-in editor and the public viewer drive ONE
// implementation. The handler-body source-invariants therefore read the hook;
// the create-fn injection (withAuthRetry(createComment(prototype.id))) is the
// per-surface seam and stays a source-invariant on PostGenerationResult.tsx.
const HOOK_SRC = readFileSync(
  join(HERE, "..", "usePinMarking.ts"),
  "utf8",
)

describe("design-agent.css — two-column design-pane appended + scoped (AC2)", () => {
  it("defines a scoped .design-pane grid at 1fr/320px (test_css_design_pane_appended_and_scoped)", () => {
    // The grid container is scoped under .design-agent-surface (P6-11's
    // scoping-invariant test independently enforces this for every selector).
    const block = CSS.match(
      /\.design-agent-surface\s+\.design-pane\s*\{([^}]*)\}/,
    )
    expect(block).not.toBeNull()
    const body = block![1]
    expect(body).toMatch(/display:\s*grid/)
    expect(body).toMatch(/grid-template-columns:\s*1fr\s+320px/)
  })

  it("collapses to a single column at ≤1080px via a media query", () => {
    // The @media block flips grid-template-columns to a single 1fr track.
    expect(CSS).toMatch(/@media\s*\(max-width:\s*1080px\)/)
    const media = CSS.match(
      /@media\s*\(max-width:\s*1080px\)\s*\{([\s\S]*?)\n\}/,
    )
    expect(media).not.toBeNull()
    expect(media![1]).toMatch(
      /\.design-agent-surface\s+\.design-pane\s*\{[^}]*grid-template-columns:\s*1fr\s*;/,
    )
  })

  it("introduces no new colour literals in the appended block", () => {
    // The appended P6-13 values are layout-only (grid/px) — no hex / rgb / hsl.
    // (P6-11's palette test enforces this file-wide; this is a local guard.)
    expect(CSS).toContain(".design-pane")
    const paneRegion = CSS.slice(CSS.indexOf(".design-pane"))
    expect(paneRegion).not.toMatch(/#[0-9a-fA-F]{3,8}\b/)
    expect(paneRegion).not.toMatch(/rgba?\(/)
    expect(paneRegion).not.toMatch(/hsla?\(/)
  })
})

describe("design-agent.css — full-screen overlay block appended + scoped (P6-16 AC6)", () => {
  it("test_css_fullscreen_block_appended_and_scoped", () => {
    expect(CSS).toContain(".design-agent-surface .proto-fullscreen")
    expect(CSS).toContain(".design-agent-surface .proto-fullscreen-body")
    expect(CSS).toContain(".design-agent-surface .proto-fullscreen-close")
    expect(CSS).toContain(".design-agent-surface .proto-fullscreen-trigger")
    // Every proto-fullscreen* selector occurrence is scoped under the surface.
    const fsSelectors = CSS.match(/^\s*\.[^\n{]*proto-fullscreen[^\n{]*\{/gm) ?? []
    expect(fsSelectors.length).toBeGreaterThan(0)
    for (const sel of fsSelectors) {
      expect(sel.trimStart()).toMatch(/^\.design-agent-surface\s/)
    }
    // The appended block uses tokens only — NO new colour literal (the scrim
    // reuses var(--ink-alpha-45)); no documented-scrim exception is needed.
    const block = CSS.slice(
      CSS.indexOf(".design-agent-surface .proto-fullscreen-trigger"),
    )
    expect(block).not.toMatch(/#[0-9a-fA-F]{3,8}\b/)
    expect(block).not.toMatch(/rgba?\(/)
    expect(block).not.toMatch(/hsla?\(/)
  })
})

describe("public viewer unaffected (AC4)", () => {
  it("PublicTokenViewer does NOT use PostGenerationResult (test_public_viewer_unchanged)", () => {
    // AC4: the public /p/<token> surface composes its own chrome and never
    // mounts PostGenerationResult, so the two-column relocation cannot touch it.
    expect(PUBLIC_VIEWER).not.toContain("PostGenerationResult")
  })
})

describe("PostGenerationResult container — defaults from the prototype record (AC9)", () => {
  // the editable handoff (CompletionBar) + share
  // (ShareMenu) controls now live in CLOSED control-bar popovers, so the container
  // SSR markup asserts the compact bar mounts (its triggers) rather than the
  // expanded inline panels. The seed-from-prop behaviour the launcher `key` fix
  // targets is still covered by reseedStep's pure unit tests below.
  it("mounts the compact control bar from a full record", () => {
    const html = renderToStaticMarkup(
      React.createElement(PostGenerationResult, {
        prototype: proto({
          is_complete: false,
          share_mode: "public",
          share_token: "abc",
        }),
      }),
    )
    expect(html).toContain('data-testid="post-generation-result"')
    expect(html).toContain('data-testid="da-controlbar"')
    expect(html).toContain('data-testid="da-actions-toggle"')
    expect(html).toContain('data-testid="da-share-toggle"')
  })

  it("a shared record (share_token present) mounts the right sidebar shell", () => {
    const html = renderToStaticMarkup(
      React.createElement(PostGenerationResult, {
        prototype: proto({ is_complete: true, share_mode: "private" }),
      }),
    )
    // The right comments sidebar always exists (Problem 2); unshared → empty state.
    expect(html).toContain('data-testid="da-canvas-comments"')
    expect(html).toContain('data-testid="da-comments-empty"')
  })

  it("mounts cleanly when the P2-06 columns are absent (older/partial rows)", () => {
    const html = renderToStaticMarkup(
      React.createElement(PostGenerationResult, {
        prototype: proto(),
      }),
    )
    expect(html).toContain('data-testid="da-controlbar"')
    expect(html).toContain('data-testid="post-generation-result"')
  })

  // NOTE: the container now GATES `bundle_url` behind the view-grant mint
  // (useViewGrant) — the iframe/overlay only render AFTER the async grant POST
  // resolves, which `renderToStaticMarkup` (synchronous, no effects) cannot drive.
  // The viewer-present-when-bundle cases (defaultFullscreen overlay; MarkOverlay
  // mount) therefore moved to the DOM-env companion
  // `PostGenerationResult.grant.dom.test.tsx`, which mocks viewGrant + waits.

  it("defaultFullscreen absent (other consumers) — overlay does not render on mount", () => {
    // Other consumers (PrdScreen, DesignAgentLauncher) omit defaultFullscreen;
    // it falls through to `undefined ?? false` so fullscreenOpen stays false and
    // the overlay is not present in the initial render.
    const html = renderToStaticMarkup(
      React.createElement(PostGenerationResult, {
        prototype: proto({ bundle_url: "https://cdn/p/42/index.html" }),
      }),
    )
    expect(html).not.toContain('data-testid="proto-fullscreen"')
    expect(html).not.toContain('data-testid="proto-fullscreen-close"')
  })

  it("onFullscreenChange prop is accepted without error and does not fire on the initial render", () => {
    // The callback is an optional notification-only prop — it fires on toggle, not
    // on mount. Passing it must not crash the static render. (The overlay-present
    // assertion moved to the DOM companion: the overlay is gated on the now-
    // async-granted bundle url, which renderToStaticMarkup can't drive.)
    const spy = vi.fn()
    const html = renderToStaticMarkup(
      React.createElement(PostGenerationResult, {
        prototype: proto({ bundle_url: "https://cdn/p/42/index.html" }),
        defaultFullscreen: true,
        onFullscreenChange: spy,
      }),
    )
    // The container chrome renders cleanly with the prop present.
    expect(html).toContain('data-testid="post-generation-result"')
    // The callback was NOT invoked during the static render — it only fires on
    // toggle (open/close handlers), not on initial mount.
    expect(spy).not.toHaveBeenCalled()
  })

  it("onFullscreenChange omitted on other consumers — renders identically to the no-prop case", () => {
    // Verify the optional prop has no footprint when absent: renders exactly the
    // same markup as when no fullscreen props are passed (closed by default).
    const htmlWithout = renderToStaticMarkup(
      React.createElement(PostGenerationResult, {
        prototype: proto({ bundle_url: "https://cdn/p/42/index.html" }),
      }),
    )
    const htmlWith = renderToStaticMarkup(
      React.createElement(PostGenerationResult, {
        prototype: proto({ bundle_url: "https://cdn/p/42/index.html" }),
        onFullscreenChange: undefined,
      }),
    )
    expect(htmlWith).toBe(htmlWithout)
  })
})

// ─── isInTab prop-threading integration: container → View → DaControlBar ──────
// Regression guard for the prop-threading bug where `isInTab` was set on the
// PostGenerationResult CONTAINER but dropped before reaching DaControlBar,
// causing the in-tab toolbar to silently render the OLD launcher bar. Leaf-only
// unit tests (mounting DaControlBar/InTabHandoffCluster with isInTab=true
// directly) cannot catch this — only rendering the full container end-to-end can.
describe("PostGenerationResult container — isInTab prop threads through to DaControlBar (regression: dropped-prop bug)", () => {
  const READY_PROTO = proto({ bundle_url: "https://cdn/p/42/index.html", is_complete: false })

  it("isInTab=true renders the in-tab Mark Complete button and NOT the launcher Actions/Done bar", () => {
    const html = renderToStaticMarkup(
      React.createElement(PostGenerationResult, {
        prototype: READY_PROTO,
        isInTab: true,
      }),
    )
    // The in-tab cluster must be present.
    expect(html).toContain('data-testid="da-mark-complete"')
    // The launcher bar buttons must be absent.
    expect(html).not.toContain('data-testid="da-control-done"')
    expect(html).not.toContain('data-testid="da-actions-toggle"')
  })

  it("isInTab absent (launcher path) renders the Actions/Done bar and NOT the in-tab Mark Complete button", () => {
    const html = renderToStaticMarkup(
      React.createElement(PostGenerationResult, {
        prototype: READY_PROTO,
      }),
    )
    // The classic launcher bar must be present.
    expect(html).toContain('data-testid="da-actions-toggle"')
    // The in-tab cluster must be absent.
    expect(html).not.toContain('data-testid="da-mark-complete"')
  })

  it("isInTab=true + is_complete=true renders Export/Undo (and NOT the launcher Actions/Done bar)", () => {
    const COMPLETE_PROTO = proto({ bundle_url: "https://cdn/p/42/index.html", is_complete: true })
    const html = renderToStaticMarkup(
      React.createElement(PostGenerationResult, {
        prototype: COMPLETE_PROTO,
        isInTab: true,
      }),
    )
    // Complete in-tab cluster: Export + Undo must be present.
    expect(html).toContain('data-testid="da-export"')
    expect(html).toContain('data-testid="da-undo"')
    // Copy is also rendered in the complete branch.
    expect(html).toContain('data-testid="da-copy"')
    // The launcher bar (old path) must be absent.
    expect(html).not.toContain('data-testid="da-actions-toggle"')
    expect(html).not.toContain('data-testid="da-control-done"')
  })

  it("is_complete=true WITHOUT isInTab (launcher path) renders the Actions toggle and NOT the in-tab Export button", () => {
    const COMPLETE_PROTO = proto({ bundle_url: "https://cdn/p/42/index.html", is_complete: true })
    const html = renderToStaticMarkup(
      React.createElement(PostGenerationResult, {
        prototype: COMPLETE_PROTO,
      }),
    )
    // The classic launcher bar must be present.
    expect(html).toContain('data-testid="da-actions-toggle"')
    // The in-tab complete cluster must be absent.
    expect(html).not.toContain('data-testid="da-export"')
    expect(html).not.toContain('data-testid="da-undo"')
  })
})

// ─── viewer iframe src + remount key: follow the live build path ─────────────

// Pull the inline viewer iframe's `src` out of the SSR markup. The inline viewer
// is the only `da-prototype-iframe` when the full-screen overlay is closed.
function inlineIframeSrc(html: string): string | null {
  const m = html.match(/<iframe[^>]*\bclass="da-prototype-iframe"[^>]*>/)
  if (!m) return null
  const src = m[0].match(/\bsrc="([^"]*)"/)
  return src ? src[1] : null
}

describe("viewer src + remount key — follows the live build path", () => {
  const OLD = "https://cdn/p/54/27/index.html"
  const NEW = "https://cdn/p/54/32/index.html"

  it("test_iframe_src_repoints_on_new_bundle_url — the iframe src base follows a new build path", () => {
    // A completed iterate lands the rebuild at a NEW path and the refetch hands a
    // fresh prop down. The src must reflect the NEW base, not the OLD base with a
    // busted query — fails if the base were captured/cached rather than read live.
    const oldHtml = renderView({ bundleUrl: OLD, bundleReloadNonce: 3 })
    expect(inlineIframeSrc(oldHtml)).toBe(`${OLD}?v=3`)

    const newHtml = renderView({ bundleUrl: NEW, bundleReloadNonce: 3 })
    expect(inlineIframeSrc(newHtml)).toBe(`${NEW}?v=3`)
    expect(newHtml).not.toContain("54/27")
  })

  it("test_viewer_remounts_on_build_swap_even_when_nonce_unchanged — a new build path forces a fresh mount", () => {
    // The reload nonce and the build-path advance need not move in lockstep (the
    // path arrives on the refetch, after the iterate-complete event). Keying on
    // the path means a build swap forces a remount on its own — so the canvas
    // never reuses a frame stuck on the prior build. Fails if the key tracked the
    // nonce alone (both keys would collide at the same nonce).
    expect(viewerRemountKey(OLD, 5)).not.toBe(viewerRemountKey(NEW, 5))
  })

  it("test_same_bundle_url_nonce_bump_still_cache_busts — same path, bumped nonce still busts + remounts", () => {
    expect(viewerSrc(OLD, 1)).toBe(`${OLD}?v=1`)
    expect(viewerSrc(OLD, 2)).toBe(`${OLD}?v=2`)
    // The remount key still changes on a same-path nonce bump, so the frame
    // reloads even when the backend overwrites the bundle in place.
    expect(viewerRemountKey(OLD, 1)).not.toBe(viewerRemountKey(OLD, 2))
  })

  it("test_initial_render_uses_first_bundle_url — first build renders a clean, unbusted src", () => {
    // Nonce 0 (no rebuild yet) → the src is the bare first build path, no query,
    // keeping the initial/SSR output stable.
    expect(viewerSrc(NEW, 0)).toBe(NEW)
    const html = renderView({ bundleUrl: NEW, bundleReloadNonce: 0 })
    expect(inlineIframeSrc(html)).toBe(NEW)
  })

  it("appends the cache-bust with & when the build path already carries a query", () => {
    const withQuery = "https://cdn/p/54/32/index.html?t=abc"
    expect(viewerSrc(withQuery, 4)).toBe(`${withQuery}&v=4`)
  })

  it("returns the null/absent bundle untouched (nothing to render yet)", () => {
    expect(viewerSrc(null, 0)).toBeNull()
    expect(viewerSrc(null, 7)).toBeNull()
  })

  it("test_post_generation_result_reads_reload_signal_not_reload_key — bundleGrantReloadKey (fed by useViewGrant's renamed reloadSignal) still threads into the composite viewer key exactly as the pre-rename reloadKey did", () => {
    // The container now passes the hook's renamed `reloadSignal` field (formerly
    // a differently-named field on the same shape) into this unchanged
    // `bundleGrantReloadKey` prop — a value change here still
    // forces a fresh remount key, proving the rename didn't silently sever the
    // threading contract this view already relies on.
    const htmlBefore = renderView({ bundleUrl: OLD, bundleGrantReloadKey: 0 })
    const htmlAfter = renderView({ bundleUrl: OLD, bundleGrantReloadKey: 1 })
    expect(htmlBefore).not.toBe(htmlAfter)
  })

  it("test_viewer_src_busts_cache_on_grant_reload_signal_alone — a checkpoint-driven grant reload signal alone busts the cache, not just bundleReloadNonce", () => {
    // AC9: the composite cache-bust value (bundleReloadNonce + bundleGrantReloadKey)
    // must change whenever EITHER source changes — a checkpoint-advance reload
    // (bundleGrantReloadKey, sourced from useViewGrant's own reloadSignal) alone,
    // with the caller's manual-refresh nonce (bundleReloadNonce) held constant,
    // still produces a fresh `?v=` value on the inline iframe src.
    const htmlBefore = renderView({
      bundleUrl: OLD,
      bundleReloadNonce: 0,
      bundleGrantReloadKey: 0,
    })
    const htmlAfter = renderView({
      bundleUrl: OLD,
      bundleReloadNonce: 0,
      bundleGrantReloadKey: 1,
    })
    const srcBefore = inlineIframeSrc(htmlBefore)
    const srcAfter = inlineIframeSrc(htmlAfter)
    expect(srcBefore).toBe(OLD) // nonce 0 → clean, unbusted (matches viewerSrc's own contract)
    expect(srcAfter).toBe(`${OLD}?v=1`)
    expect(srcAfter).not.toBe(srcBefore)
  })
})

describe("pin-comment create stays wrapped in the auth-retry (preservation)", () => {
  it("test_pin_comment_create_wrapped_in_auth_retry — the SIGNED-IN onCreate wraps createComment in withAuthRetry", () => {
    // C2b: the create-fn is now INJECTED into usePinMarking per surface. The
    // node-env run cannot exercise the bearer-refresh path, so assert the wrapping
    // as a source invariant (the repo's source-assertion convention). On the
    // signed-in container the onCreate handed to usePinMarking must stay inside
    // withAuthRetry(() => designAgentApi.createComment(prototype.id, …)) so a
    // transient 401 retries once through the refresh instead of silently losing a
    // saved comment. (The PUBLIC surface threads createCommentByToken — asserted
    // in the public-token-states suite.)
    expect(RESULT_SRC).toContain("usePinMarking({")
    const start = RESULT_SRC.indexOf("usePinMarking({")
    const call = RESULT_SRC.slice(start, start + 600)
    expect(call).toMatch(
      /onCreate:\s*\(payload\)\s*=>\s*\n?\s*withAuthRetry\(\s*\(\)\s*=>\s*designAgentApi\.createComment\(prototype\.id,\s*payload\)\s*\)/,
    )
    // the helper is imported from the shared api module (the import must stay intact)
    expect(RESULT_SRC).toMatch(
      /import\s*\{[^}]*\bwithAuthRetry\b[^}]*\}\s*from\s*["']\.\.\/\.\.\/lib\/api["']/,
    )
  })
})

// ─── Mark-and-comment pin flow ────────────────────────────────────────────────

import type { PinComment } from "../PostGenerationResult"

const BUNDLE = "https://cdn/p/42/index.html"

function pinComment(over: Partial<PinComment> = {}): PinComment {
  return {
    n: 1,
    xPct: 50,
    yPct: 50,
    draft: "",
    body: "",
    saved: false,
    busy: false,
    error: null,
    anchor: null,
    xPctInEl: null,
    yPctInEl: null,
    elementFriendly: null,
    elementTechnical: null,
    ...over,
  }
}

describe("Mark-and-comment pin flow — view layer", () => {
  it("test_mark_mode_activates_overlay — markMode=true renders .da-mark-overlay.active", () => {
    const html = renderView({ bundleUrl: BUNDLE, markMode: true })
    // The overlay must carry the `active` class when in mark mode.
    expect(html).toContain('class="da-mark-overlay active"')
  })

  it("test_stage_click_drops_numbered_pin_and_composer — a pin in the pins array renders its numbered badge and comment composer", () => {
    const pin = pinComment({ n: 1, xPct: 40, yPct: 60, draft: "" })
    const html = renderView({
      bundleUrl: BUNDLE,
      pins: [pin],
    })
    // The numbered pin badge in the pin layer
    expect(html).toContain('data-testid="da-pin-1"')
    // The composer form for the unsaved pin
    expect(html).toContain('data-testid="da-pin-input-1"')
    expect(html).toContain('data-testid="da-pin-submit-1"')
  })

  it("test_saved_pin_row_shows_author_avatar_time — a saved pin row renders author + avatar + relative-time + Apply/Ignore", () => {
    const pin = pinComment({
      n: 2,
      saved: true,
      body: "Looks great",
      author: "Carol D",
      createdAt: "2026-06-06T08:00:00Z",
    })
    const html = renderView({ bundleUrl: BUNDLE, pins: [pin] })
    // author label
    expect(html).toContain("Carol D")
    // avatar chip
    expect(html).toContain('data-testid="comment-avatar"')
    // relative timestamp class present and ISO string in time element (title or dateTime attribute)
    expect(html).toContain('class="proto-comment-time"')
    expect(html).toContain("2026-06-06T08:00:00Z")
    // Apply + Ignore buttons (saved + not resolved)
    expect(html).toContain('data-testid="da-pin-apply-2"')
    expect(html).toContain('data-testid="da-pin-ignore-2"')
  })

  it("test_pin_submit_uses_auth_retry_create_with_pin_anchor — handlePinSubmit sends anchor_id=pin-N (synthetic marker) + the signed-in onCreate is auth-retried", () => {
    // C2b: handlePinSubmit moved VERBATIM into usePinMarking. It still uses the
    // synthetic pin-<n> anchor_id marker and now calls the injected onCreate; the
    // withAuthRetry wrapping is the signed-in container's onCreate seam.
    expect(HOOK_SRC).toContain("async function handlePinSubmit")
    const start = HOOK_SRC.indexOf("async function handlePinSubmit")
    const body = HOOK_SRC.slice(start, start + 1200)
    // anchor_id is the unchanged synthetic pin marker — back-compat with the list keying
    expect(body).toMatch(/anchor_id:\s*`pin-\$\{n\}`/)
    // the submit calls the injected create-fn (per-surface transport)
    expect(body).toMatch(/await onCreate\(/)
    // the SIGNED-IN container threads the auth-retried createComment into onCreate
    expect(RESULT_SRC).toMatch(/withAuthRetry\(\s*\(\)\s*=>\s*designAgentApi\.createComment\(prototype\.id,\s*payload\)/)
  })

  it("test_create_comment_body_sends_position_fields — handlePinSubmit includes pin_x_pct, pin_y_pct, resolved_anchor_id", () => {
    // Verify that the submit function (now in usePinMarking) sends all three
    // durable position fields alongside the unchanged synthetic anchor_id and
    // body. Pin position is persisted so every viewer sees the same pin location.
    const start = HOOK_SRC.indexOf("async function handlePinSubmit")
    const fnBody = HOOK_SRC.slice(start, start + 1400)
    // anchor_id (synthetic pin marker) and body are still present — back-compat.
    expect(fnBody).toContain("anchor_id:")
    expect(fnBody).toContain("body:")
    // Position fields are now included in the create payload.
    expect(fnBody).toContain("pin_x_pct:")
    expect(fnBody).toContain("pin_y_pct:")
    expect(fnBody).toContain("resolved_anchor_id:")
  })
})

// ─── C1 Slice B: pin-anchor threading survives the container→view→leaf split ──
// After extracting the overlay + pin layer + pin-comment rows into
// PrototypeMarkLayer, the load-bearing risk is the "prop dropped mid-tree" class
// of bug: a leaf (PrototypeMarkLayer) test passes while the container silently
// stops threading onStageClick / onPinSubmit into MarkOverlay / PrototypeMarkLayer.
//
// node-env vitest has no DOM and can't simulate a real click. So we guard the
// whole path two ways: (1) render the REAL PostGenerationResult container via
// renderToStaticMarkup and assert the extracted leaves actually mount inside it
// (the overlay element + the pin-comment rows for a real pin) — proving the
// container→view→leaf wiring renders end to end, not just in an isolated leaf
// test; and (2) source-invariants that handleStageClick captures the anchor +
// per-element offsets and handlePinSubmit ships them, since the click→state→
// payload data flow itself can't be driven without a DOM.
describe("PostGenerationResult container — pin-anchor threading through the leaf split", () => {
  it("the REAL container renders the center stage (overlay/pin layer mount once the granted bundle loads)", () => {
    const html = renderToStaticMarkup(
      React.createElement(PostGenerationResult, {
        prototype: proto({ bundle_url: BUNDLE }),
      }),
    )
    // The center stage always renders. The <MarkOverlay> inside it is gated on
    // the viewer, which is gated on the now-async-granted bundle url — so the
    // overlay-mounts-inside-the-stage assertion moved to the DOM companion
    // (PostGenerationResult.grant.dom.test.tsx), which drives the grant + waits.
    expect(html).toContain('data-testid="da-canvas-center"')
  })

  it("handleStageClick captures the resolved anchor + the per-element click offsets (source invariant)", () => {
    // The whole point of the slice: the stage click must still capture the anchor
    // and the click position WITHIN the anchored element so the persisted comment
    // can re-anchor the pin. If handleStageClick stopped capturing these, the
    // payload below would have nothing to ship. C2b: this logic now lives in the
    // shared usePinMarking hook (moved verbatim).
    const start = HOOK_SRC.indexOf("function handleStageClick")
    expect(start).toBeGreaterThanOrEqual(0)
    const body = HOOK_SRC.slice(start, start + 1400)
    expect(body).toContain("getClickOffsetInElement")
    expect(body).toContain("xPctInEl")
    expect(body).toContain("yPctInEl")
    // the anchor is stored on the new pin
    expect(body).toMatch(/anchor[,\s}]/)
  })

  it("handlePinSubmit ships anchor_id + pin_x_pct + pin_y_pct + resolved_anchor_id (the captured anchor reaches the create)", () => {
    // End of the thread: the fields captured at click time are sent on create.
    // C2b: handlePinSubmit lives in the shared usePinMarking hook now.
    const start = HOOK_SRC.indexOf("async function handlePinSubmit")
    const body = HOOK_SRC.slice(start, start + 1400)
    expect(body).toMatch(/anchor_id:\s*`pin-\$\{n\}`/)
    expect(body).toContain("pin_x_pct:")
    expect(body).toContain("pin_y_pct:")
    expect(body).toMatch(/resolved_anchor_id:\s*serializeAnchor\(pin\.anchor\)/)
  })

  it("the VIEW threads the capture callbacks into the extracted leaves (onStageClick→MarkOverlay, onPinSubmit→PrototypeMarkLayer onSubmitComment)", () => {
    // WHY this test exists: the two integration tests above prove the leaves
    // MOUNT and the handlers EXIST — but a leaf renders fine and the handler
    // stays defined even if PostGenerationResultView stops passing the callback
    // down. That's the load-bearing "prop dropped mid-tree" bug: the click→submit
    // thread is silently severed while every other test stays green. The ONLY
    // thing that proves the view→leaf wiring is the wiring itself in the source.
    //
    // Tolerant against the ACTUAL formatting in PostGenerationResult.tsx:
    //   MarkOverlay is a single-line element; PrototypeMarkLayer is multi-line
    //   (so [\s\S]*? bridges the tag name and the prop). Both fail if the
    //   respective prop wiring is removed.
    const markOverlayWiring =
      /<MarkOverlay[\s\S]*?onStageClick=\{onStageClick\}[\s\S]*?\/>/
    const markLayerWiring =
      /<PrototypeMarkLayer[\s\S]*?onSubmitComment=\{onPinSubmit\}[\s\S]*?\/>/
    expect(RESULT_SRC).toMatch(markOverlayWiring)
    expect(RESULT_SRC).toMatch(markLayerWiring)
  })
})

// ─── In-tab title-bar restructure tests ──────────────────────────────────────
describe("DaControlBar + PostGenerationResultView — isInTab title-bar restructure", () => {
  it("isInTab: renders the back button (da-titlebar-back) in the title bar", () => {
    const html = renderToStaticMarkup(
      React.createElement(DaControlBar, {
        prototypeId: 42,
        isComplete: false,
        shareMode: "private" as const,
        shareToken: null,
        platform: "desktop" as const,
        commentsOpen: false,
        markMode: false,
        canOpen: false,
        isInTab: true,
        onBack: () => {},
      }),
    )
    expect(html).toContain('data-testid="da-titlebar-back"')
  })

  it("isInTab: the right-cluster icon buttons are present", () => {
    const html = renderToStaticMarkup(
      React.createElement(DaControlBar, {
        prototypeId: 42,
        isComplete: false,
        shareMode: "private" as const,
        shareToken: null,
        platform: "desktop" as const,
        commentsOpen: false,
        markMode: false,
        canOpen: false,
        isInTab: true,
      }),
    )
    expect(html).toContain('data-testid="da-mark-toggle"')
    expect(html).toContain('data-testid="da-comments-toggle"')
    expect(html).toContain('data-testid="proto-fullscreen-trigger"')
  })

  it("isInTab: only Share keeps its label; Mark/Comments stay icon-only", () => {
    const html = renderToStaticMarkup(
      React.createElement(DaControlBar, {
        prototypeId: 42,
        isComplete: false,
        shareMode: "private" as const,
        shareToken: null,
        platform: "desktop" as const,
        commentsOpen: false,
        markMode: false,
        canOpen: false,
        isInTab: true,
      }),
    )
    // Share renders icon + "Share" label even in-tab (no bare glyph).
    expect(html).toContain('<span class="da-ctl-label">Share</span>')
    // The other tool labels remain hidden in-tab.
    expect(html).not.toContain('<span class="da-ctl-label">Mark</span>')
    expect(html).not.toContain('<span class="da-ctl-label">Comments</span>')
  })

  it("NOT isInTab: the back button is ABSENT and the classic labeled bar is present", () => {
    const html = renderToStaticMarkup(
      React.createElement(DaControlBar, {
        prototypeId: 42,
        isComplete: false,
        shareMode: "private" as const,
        shareToken: null,
        platform: "desktop" as const,
        commentsOpen: false,
        markMode: false,
        canOpen: false,
        isInTab: false,
      }),
    )
    expect(html).not.toContain('data-testid="da-titlebar-back"')
    expect(html).toContain('class="da-ctl-label"')
    expect(html).toContain('data-testid="da-actions-toggle"')
  })

  it("isInTab via PostGenerationResultView: back button present end-to-end", () => {
    const html = renderView({ isInTab: true })
    expect(html).toContain('data-testid="da-titlebar-back"')
  })

  it("NOT isInTab via PostGenerationResultView: back button absent end-to-end", () => {
    const html = renderView({ isInTab: false })
    expect(html).not.toContain('data-testid="da-titlebar-back"')
  })

  it("isInTab + prdTitle: renders the title span with the given title text", () => {
    const html = renderToStaticMarkup(
      React.createElement(PostGenerationResult, {
        prototype: proto(),
        isInTab: true,
        prdTitle: "My Cool PRD",
      }),
    )
    expect(html).toContain('data-testid="da-titlebar-title"')
    expect(html).toContain("My Cool PRD")
  })

  it("NOT isInTab: the title-bar title span is absent", () => {
    const html = renderToStaticMarkup(
      React.createElement(PostGenerationResult, {
        prototype: proto(),
        isInTab: false,
        prdTitle: "My Cool PRD",
      }),
    )
    expect(html).not.toContain('data-testid="da-titlebar-title"')
  })
})

// ─── manual Refresh-preview button — reuses the bundleReloadNonce seam ────────
// The signed-in editor control bar gains a Refresh button to the right of the
// Desktop/Mobile toggle. It must (a) render with its accessible label only when
// `onRefreshBundle` is provided, (b) call that callback exactly once on click,
// and (c) be wired to the SAME nonce bump the iterate-complete path uses — NOT a
// new fetch/poll. We walk the element tree (node-env vitest, no DOM) the same way
// the onShared forwarding tests do, then invoke the button's onClick directly.
describe("DaControlBar — manual Refresh-preview button (reuses bundleReloadNonce seam)", () => {
  const baseProps = {
    prototypeId: 42,
    isComplete: false,
    shareMode: "private" as const,
    shareToken: null,
    platform: "desktop" as const,
    commentsOpen: false,
    markMode: false,
    canOpen: true,
    isInTab: true,
  }

  function findByTestId(
    node: React.ReactNode,
    testId: string,
  ): React.ReactElement | undefined {
    for (const child of React.Children.toArray(node) as React.ReactElement[]) {
      if (!child || typeof child !== "object") continue
      const props = (child.props ?? {}) as {
        "data-testid"?: string
        children?: React.ReactNode
        trigger?: (open: boolean) => React.ReactNode
      }
      if (props["data-testid"] === testId) return child
      const found =
        (props.children ? findByTestId(props.children, testId) : undefined) ??
        (typeof props.trigger === "function"
          ? findByTestId(props.trigger(false), testId)
          : undefined)
      if (found) return found
    }
    return undefined
  }

  function findByType(
    node: React.ReactNode,
    type: React.ElementType,
  ): React.ReactElement | undefined {
    for (const child of React.Children.toArray(node) as React.ReactElement[]) {
      if (!child || typeof child !== "object") continue
      if (child.type === type) return child
      const props = (child.props ?? {}) as {
        children?: React.ReactNode
        trigger?: (open: boolean) => React.ReactNode
      }
      const found =
        (props.children ? findByType(props.children, type) : undefined) ??
        (typeof props.trigger === "function"
          ? findByType(props.trigger(false), type)
          : undefined)
      if (found) return found
    }
    return undefined
  }

  it("renders the Refresh button with its accessible label when onRefreshBundle is provided", () => {
    const html = renderToStaticMarkup(
      React.createElement(DaControlBar, {
        ...baseProps,
        onRefreshBundle: () => {},
      }),
    )
    expect(html).toContain('data-testid="da-refresh-preview"')
    expect(html).toContain('aria-label="Refresh preview"')
  })

  it("clicking Refresh calls onRefreshBundle exactly once", () => {
    const onRefreshBundle = vi.fn()
    const tree = DaControlBar({ ...baseProps, onRefreshBundle }) as React.ReactElement
    const btn = findByTestId(tree, "da-refresh-preview")
    expect(btn).toBeTruthy()
    const onClick = (btn!.props as { onClick?: () => void }).onClick
    expect(typeof onClick).toBe("function")
    onClick!()
    expect(onRefreshBundle).toHaveBeenCalledTimes(1)
  })

  it("the wired callback bumps the existing reload nonce (reuses the seam, no new loop)", () => {
    // Mirror PrototypeRoute: onRefreshBundle = () => setBundleReloadNonce((n) => n + 1).
    // Clicking it must drive the SAME setter the iterate-complete path uses, passing
    // an incrementer updater — proving the manual refresh rides the existing nonce
    // seam rather than introducing a parallel fetch/poll.
    const setBundleReloadNonce = vi.fn<(updater: (n: number) => number) => void>()
    const onRefreshBundle = () => setBundleReloadNonce((n) => n + 1)
    const tree = DaControlBar({ ...baseProps, onRefreshBundle }) as React.ReactElement
    const btn = findByTestId(tree, "da-refresh-preview")
    const onClick = (btn!.props as { onClick?: () => void }).onClick
    onClick!()
    expect(setBundleReloadNonce).toHaveBeenCalledTimes(1)
    const updater = setBundleReloadNonce.mock.calls[0]![0]
    expect(updater(0)).toBe(1)
    expect(updater(7)).toBe(8)
  })

  it("the Refresh button is ABSENT when onRefreshBundle is not provided (public / fullscreen path)", () => {
    const html = renderToStaticMarkup(
      React.createElement(DaControlBar, { ...baseProps }),
    )
    expect(html).not.toContain('data-testid="da-refresh-preview"')
  })

  it("end-to-end: the View threads onRefreshBundle down to DaControlBar", () => {
    const onRefreshBundle = vi.fn()
    const tree = PostGenerationResultView({
      prototypeId: 42,
      isComplete: false,
      shareMode: "private",
      shareToken: null,
      bundleUrl: null,
      onRefreshBundle,
    }) as React.ReactElement
    const bar = findByType(tree, DaControlBar)
    expect(bar).toBeTruthy()
    expect((bar!.props as { onRefreshBundle?: unknown }).onRefreshBundle).toBe(onRefreshBundle)
  })
})

// ─── bundle-cover label semantics (Glitch B) ─────────────────
// The bundle-not-ready cover must NOT claim "Applying changes…" on a passive
// (re)load or readiness cover — only a genuine iterate/apply says that. A passive
// cover shows the neutral "Loading…". (The persistent iframe means the fullscreen
// toggle no longer triggers this cover at all; this guards the copy defensively.)
describe("PostGenerationResultView — bundle-cover label is iterate-aware", () => {
  const BUNDLE = "https://cdn/x/bundle/index.html"

  it("shows the neutral 'Loading…' (NOT 'Applying changes…') on a passive readiness cover", () => {
    const html = renderView({
      bundleUrl: BUNDLE,
      bundleNotReady: true,
      iterateRunning: false,
    })
    expect(html).toContain('data-testid="da-bundle-loading"')
    expect(html).toContain("Loading…")
    expect(html).not.toContain("Applying changes…")
  })

  it("shows 'Applying changes…' ONLY during a genuine iterate/apply", () => {
    const html = renderView({
      bundleUrl: BUNDLE,
      bundleNotReady: true,
      iterateRunning: true,
    })
    expect(html).toContain('data-testid="da-bundle-loading"')
    expect(html).toContain("Applying changes…")
  })
})
