import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { afterEach, describe, expect, it, vi } from "vitest"
import {
  DesignAgentLauncher,
  DesignAgentLauncherView,
  failureFromGeneration,
  pendingKey,
  pollUntilAdvanced,
  refreshShareTokenStep,
  resultFromGeneration,
  type LauncherDrawerProps,
} from "../DesignAgentLauncher"
import { IterateComposer } from "../IterateComposer"
import { ClarifyingQuestionSurface } from "../ClarifyingQuestionSurface"
import { CommentsPanel } from "../CommentsPanel"
import { PostGenerationResult } from "../PostGenerationResult"
import { GenerationErrorBanner } from "../GenerationErrorBanner"
import { PrototypePreviewCard } from "../PrototypePreviewCard"
import type { PrototypeRecord } from "../../../lib/api"
import type { DesignAgentGenResult } from "../../../lib/runDesignAgentGeneration"
import type { PrdSection } from "../../../types/content"

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

describe("DesignAgentLauncher — surface wrapper markup", () => {
  it("renders the design-agent-surface wrapper without a direct Generate button (test_launcher_renders_surface_wrapper)", () => {
    const { renderDrawer } = makeDrawerSpy()
    const html = renderToStaticMarkup(
      React.createElement(DesignAgentLauncher, {
        prdId: 1,
        figmaFileKey: null,
        renderDrawer,
      }),
    )
    expect(html).toContain('class="design-agent-surface prd-design-launcher"')
    expect(html).toMatch(/contenteditable="false"/i)
    // The generation trigger moved to the Approve modal — no direct button here.
    expect(html).not.toContain("Generate Prototype")
  })

  it("the surface wrapper has contentEditable={false} — clickable inside the PRD editable region (test_launcher_content_editable_wrapper)", () => {
    const { renderDrawer } = makeDrawerSpy()
    const html = renderToStaticMarkup(
      React.createElement(DesignAgentLauncher, {
        prdId: 1,
        figmaFileKey: null,
        renderDrawer,
      }),
    )
    // The wrapper div carries contentEditable="false" — load-bearing for
    // Sprntly's PRD editable region. Generation lives in the Approve modal now.
    expect(html).toMatch(/contenteditable="false"/i)
    expect(html).not.toContain("Generate Prototype")
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
  it("the preview card's onOpen calls onOpenExisting — existing-prototype open path (test_launcher_preview_card_opens_canvas)", () => {
    const onOpenExisting = vi.fn()
    // The view is pure (no hooks); call it directly to inspect the element tree.
    const tree = DesignAgentLauncherView({
      prdId: 1,
      figmaFileKey: null,
      open: false,
      setOpen: vi.fn(),
      existing: { id: 7, status: "ready", bundle_url: "https://cdn/x/bundle/index.html", error: null },
      onOpenExisting,
      renderDrawer: () => null,
    }) as React.ReactElement
    const children = React.Children.toArray(
      (tree.props as { children: React.ReactNode }).children,
    ) as React.ReactElement[]
    const card = children.find((c) => c.type === PrototypePreviewCard)
    expect(card).toBeTruthy()
    ;(card!.props as { onOpen: () => void }).onOpen()
    expect(onOpenExisting).toHaveBeenCalledTimes(1)
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

describe("DesignAgentLauncher — post-generation result (P2-12)", () => {
  const samplePrototype: PrototypeRecord = {
    id: 7,
    status: "ready",
    bundle_url: "https://cdn/x/bundle/index.html",
    error: null,
    is_complete: false,
    share_mode: "private",
    share_token: null,
  }

  it("renders PostGenerationResult once a generation has succeeded (test_launcher_renders_result_on_generation_success)", () => {
    const { renderDrawer } = makeDrawerSpy()
    const html = renderToStaticMarkup(
      React.createElement(DesignAgentLauncherView, {
        prdId: 1,
        figmaFileKey: null,
        open: false,
        setOpen: noop,
        result: samplePrototype,
        renderDrawer,
      }),
    )
    expect(html).toContain('data-testid="post-generation-result"')
    // The editable control bar (not the public read-only badge) is mounted.
    expect(html).toContain('data-testid="da-controlbar"')
    expect(html).not.toContain('data-testid="completion-bar-readonly"')
  })

  it("renders no result view when generation has not succeeded (test_launcher_renders_no_result_on_generation_failure)", () => {
    const { renderDrawer } = makeDrawerSpy()
    const html = renderToStaticMarkup(
      React.createElement(DesignAgentLauncherView, {
        prdId: 1,
        figmaFileKey: null,
        open: false,
        setOpen: noop,
        result: null,
        renderDrawer,
      }),
    )
    expect(html).not.toContain('data-testid="post-generation-result"')
    // No Generate button — the generation trigger moved to the Approve modal.
    expect(html).not.toContain("Generate Prototype")
  })

  it("forwards onGenerated to the drawer so a success can populate the result", () => {
    const { calls, renderDrawer } = makeDrawerSpy()
    const onGenerated = vi.fn()
    DesignAgentLauncherView({
      prdId: 1,
      figmaFileKey: null,
      open: false,
      setOpen: noop,
      onGenerated,
      renderDrawer,
    })
    expect(calls[0].onGenerated).toBe(onGenerated)
  })

  it("maps a successful outcome to the prototype (resultFromGeneration)", () => {
    expect(resultFromGeneration({ ok: true, prototype: samplePrototype })).toBe(
      samplePrototype,
    )
  })

  it("maps a failed outcome to null — no result view (AC5)", () => {
    expect(resultFromGeneration({ ok: false, message: "timed out" })).toBeNull()
  })
})

// ─── P6-05 (#5): race-safe post-iterate/clarify re-poll + prop threading ─────

function rec(over: Partial<PrototypeRecord> = {}): PrototypeRecord {
  return { id: 7, status: "ready", bundle_url: null, error: null, ...over }
}

/** Call the pure view directly and return its flattened child elements (no DOM
 *  render → real child components are NOT invoked, just inspected as elements). */
function viewChildren(
  over: Partial<Parameters<typeof DesignAgentLauncherView>[0]> = {},
): React.ReactElement[] {
  const tree = DesignAgentLauncherView({
    prdId: 1,
    figmaFileKey: null,
    open: false,
    setOpen: noop,
    renderDrawer: () => null,
    ...over,
  }) as React.ReactElement
  return React.Children.toArray(
    (tree.props as { children: React.ReactNode }).children,
  ) as React.ReactElement[]
}

describe("pendingKey (pure)", () => {
  it("extracts the pending question text, or null when none is pending", () => {
    expect(pendingKey({ pending_question: { question: "Why dark mode?" } })).toBe(
      "Why dark mode?",
    )
    expect(pendingKey({ pending_question: null })).toBeNull()
    expect(pendingKey({})).toBeNull()
  })
})

describe("pollUntilAdvanced — race-safe re-poll (AC4/AC5)", () => {
  const noSleep = async () => {}
  const frozenNow = () => 0

  it("ignores a stale pre-iterate ready and lands on the new bundle (test_refresh_ignores_stale_preiterate_ready, AC4)", async () => {
    const OLD = "https://cdn/OLD/index.html"
    const NEW = "https://cdn/NEW/index.html"
    const seq: DesignAgentGenResult[] = [
      // 1) stale: the row hasn't flipped off the pre-iterate checkpoint yet.
      { ok: true, prototype: rec({ bundle_url: OLD, status: "ready" }) },
      // 2) flipped to generating, not yet built (still OLD bundle).
      { ok: true, prototype: rec({ bundle_url: OLD, status: "generating" }) },
      // 3) new checkpoint built.
      { ok: true, prototype: rec({ bundle_url: NEW, status: "ready" }) },
    ]
    let i = 0
    const runGeneration = vi.fn(async () => seq[Math.min(i++, seq.length - 1)])

    const fresh = await pollUntilAdvanced(7, OLD, null, {
      runGeneration,
      sleep: noSleep,
      now: frozenNow,
    })

    expect(fresh?.bundle_url).toBe(NEW)
    // Did NOT resolve on the first (stale) read — it re-sampled past it.
    expect(runGeneration).toHaveBeenCalledTimes(3)
  })

  it("clarify refetch resolves on a new bundle, not the stale pre-answer read (test_clarify_answer_triggers_refetch, AC5)", async () => {
    const seq: DesignAgentGenResult[] = [
      // stale: same bundle AND the same pre-answer question still observed.
      {
        ok: true,
        prototype: rec({
          bundle_url: "OLD",
          status: "ready",
          pending_question: { question: "Q1" },
        }),
      },
      // advanced: a new checkpoint built, question cleared.
      {
        ok: true,
        prototype: rec({ bundle_url: "NEW", status: "ready", pending_question: null }),
      },
    ]
    let i = 0
    const runGeneration = vi.fn(async () => seq[Math.min(i++, seq.length - 1)])

    const fresh = await pollUntilAdvanced(7, "OLD", "Q1", {
      runGeneration,
      sleep: noSleep,
      now: frozenNow,
    })

    expect(fresh?.bundle_url).toBe("NEW")
    expect(runGeneration).toHaveBeenCalledTimes(2)
  })

  it("clarify refetch resolves on a re-pause with a NEW question (pending_question transition, AC5)", async () => {
    const seq: DesignAgentGenResult[] = [
      {
        ok: true,
        prototype: rec({
          bundle_url: "OLD",
          status: "ready",
          pending_question: { question: "Q1" },
        }),
      },
      {
        ok: true,
        prototype: rec({
          bundle_url: "OLD",
          status: "ready",
          pending_question: { question: "Q2" },
        }),
      },
    ]
    let i = 0
    const runGeneration = vi.fn(async () => seq[Math.min(i++, seq.length - 1)])

    const fresh = await pollUntilAdvanced(7, "OLD", "Q1", {
      runGeneration,
      sleep: noSleep,
      now: frozenNow,
    })

    expect(pendingKey(fresh as PrototypeRecord)).toBe("Q2")
    expect(runGeneration).toHaveBeenCalledTimes(2)
  })

  it("returns null on a failed poll (failure handed off to the existing path)", async () => {
    const runGeneration = vi.fn(async () => ({ ok: false as const, message: "boom" }))
    const fresh = await pollUntilAdvanced(7, "OLD", null, {
      runGeneration,
      sleep: noSleep,
      now: frozenNow,
    })
    expect(fresh).toBeNull()
    expect(runGeneration).toHaveBeenCalledTimes(1)
  })

  it("returns null when the deadline passes without an advance (bounded)", async () => {
    const runGeneration = vi.fn(async () =>
      ({ ok: true as const, prototype: rec({ bundle_url: "OLD", status: "ready" }) }),
    )
    let t = 0
    const fresh = await pollUntilAdvanced(7, "OLD", null, {
      runGeneration,
      sleep: noSleep,
      now: () => {
        const v = t
        t += 200_000
        return v
      },
      deadlineMs: 300_000,
    })
    expect(fresh).toBeNull()
  })
})

describe("post-iterate / clarify callback threading (AC4/AC5 wiring)", () => {
  const base: PrototypeRecord = {
    id: 7,
    status: "ready",
    bundle_url: "https://cdn/x/index.html",
    error: null,
    is_complete: false,
    share_mode: "private",
    share_token: null,
  }

  it("forwards onIterated to the IterateComposer mount", () => {
    // UX-EXPLORE (throwaway — REVERT): IterateComposer now lives in
    // PostGenerationResult's `iterate` slot (the LEFT region of the 3-region
    // canvas), not as a direct launcher child — read it off the slot.
    const onIterated = vi.fn()
    const children = viewChildren({ result: base, onIterated })
    const pgr = children.find((c) => c.type === PostGenerationResult)
    expect(pgr).toBeTruthy()
    const iterate = (pgr!.props as { iterate?: React.ReactElement }).iterate
    expect(iterate).toBeTruthy()
    expect(iterate!.type).toBe(IterateComposer)
    expect((iterate!.props as { onIterated?: () => void }).onIterated).toBe(
      onIterated,
    )
  })

  it("forwards onAnswered to the ClarifyingQuestionSurface mount", () => {
    const onAnswered = vi.fn()
    const clarifyProto = rec({
      bundle_url: "https://cdn/x/index.html",
      is_complete: false,
      pending_question: { question: "Mobile or desktop first?" },
    })
    const children = viewChildren({ result: clarifyProto, onAnswered })
    const clarify = children.find((c) => c.type === ClarifyingQuestionSurface)
    expect(clarify).toBeTruthy()
    expect((clarify!.props as { onAnswered?: () => void }).onAnswered).toBe(
      onAnswered,
    )
  })
})

describe("CommentsPanel relocated into PostGenerationResult's `comments` prop (AC3/AC6c, #14)", () => {
  // P6-13 (UX-3): CommentsPanel moved OUT of its post-PostGenerationResult
  // sibling position and is now passed DOWN as the `comments` prop so a
  // two-column `design-pane` grid can wrap viewer-left + comments-right. The
  // share-token gate, `key`, `token`, `prototypeId`, and `onApply` wiring are
  // carried byte-identical — only the LOCATION changed. These assertions REPLACE
  // the pre-move `viewChildren(...).find(c => c.type === CommentsPanel)` checks,
  // which would go red now that CommentsPanel is no longer a direct launcher child.
  const base: PrototypeRecord = {
    id: 7,
    status: "ready",
    bundle_url: "https://cdn/x/index.html",
    error: null,
    is_complete: false,
    share_mode: "private",
    share_token: null,
  }

  /** Locate the <PostGenerationResult> element the launcher renders and read its
   *  `comments` slot (the relocated CommentsPanel element, or null). */
  function commentsSlot(
    over: Partial<Parameters<typeof DesignAgentLauncherView>[0]> = {},
  ): React.ReactElement | null {
    const children = viewChildren(over)
    const pgr = children.find((c) => c.type === PostGenerationResult)
    expect(pgr).toBeTruthy()
    return (pgr!.props as { comments?: React.ReactNode })
      .comments as React.ReactElement | null
  }

  it("never renders CommentsPanel as a DIRECT launcher child anymore (relocation, inverted assertion)", () => {
    const children = viewChildren({
      result: { ...base, share_token: "tok-xyz-123" },
    })
    expect(children.find((c) => c.type === CommentsPanel)).toBeFalsy()
  })

  it("passes NO comments node while share_token is null — gate preserved (test_comments_gate_preserved)", () => {
    const slot = commentsSlot({ result: { ...base, share_token: null } })
    expect(slot).toBeFalsy()
  })

  it("passes a CommentsPanel as the `comments` prop once share_token is present, addressed by the new token (test_launcher_passes_comments_as_prop_not_sibling)", () => {
    const slot = commentsSlot({ result: { ...base, share_token: "tok-xyz-123" } })
    expect(slot).toBeTruthy()
    expect(slot!.type).toBe(CommentsPanel)
    expect((slot!.props as { token: string }).token).toBe("tok-xyz-123")
    expect((slot!.props as { prototypeId: number }).prototypeId).toBe(7)
    // The Apply→IterateComposer handoff (onApply → setApplyTarget) is preserved
    // on the relocated node, byte-identical to the pre-move sibling.
    expect(typeof (slot!.props as { onApply: unknown }).onApply).toBe("function")
  })
})

describe("IterateComposer is the canvas LEFT region; ClarifyingQuestionSurface stays a sibling (UX-EXPLORE)", () => {
  // UX-EXPLORE (throwaway — REVERT): the 3-region canvas relocates IterateComposer
  // INTO PostGenerationResult's `iterate` slot (the LEFT region) and CommentsPanel
  // INTO its `comments` slot (the RIGHT region). ClarifyingQuestionSurface remains a
  // launcher-level sibling rendered AFTER <PostGenerationResult> (the clarify flow
  // is outside the approved 3-region scope, so it is left where it was).
  const base: PrototypeRecord = {
    id: 7,
    status: "ready",
    bundle_url: "https://cdn/x/index.html",
    error: null,
    is_complete: false,
    share_mode: "private",
    share_token: "tok-xyz-123",
    pending_question: { question: "Mobile or desktop first?" },
  }

  it("mounts IterateComposer in the iterate slot, CommentsPanel in the comments slot, and Clarify as a trailing sibling, wiring unchanged", () => {
    const children = viewChildren({ result: base, applyTarget: null })
    const pgrIdx = children.findIndex((c) => c.type === PostGenerationResult)
    const clarifyIdx = children.findIndex(
      (c) => c.type === ClarifyingQuestionSurface,
    )
    expect(pgrIdx).toBeGreaterThanOrEqual(0)
    expect(clarifyIdx).toBeGreaterThan(pgrIdx)
    const pgrProps = children[pgrIdx].props as {
      comments?: React.ReactElement | null
      iterate?: React.ReactElement | null
    }
    // RIGHT region: CommentsPanel.
    expect(pgrProps.comments?.type).toBe(CommentsPanel)
    // LEFT region: IterateComposer, wiring byte-unchanged.
    const iterate = pgrProps.iterate
    expect(iterate?.type).toBe(IterateComposer)
    expect((iterate!.props as { prototypeId: number }).prototypeId).toBe(7)
    expect((iterate!.props as { isComplete: boolean }).isComplete).toBe(false)
    // IterateComposer is NOT also a direct launcher child anymore.
    expect(children.find((c) => c.type === IterateComposer)).toBeFalsy()
  })
})

// ─── P6-20 (#14): share-success → single-shot re-seed → CommentsPanel mounts ──

describe("refreshShareTokenStep — share-success single-shot re-seed (AC3)", () => {
  it("resolves on the FIRST get with the live token even though bundle_url is unchanged (test_share_only_repoll_resolves_without_bundle_advance)", async () => {
    // A bare Share changes NEITHER bundle_url NOR pending_question, so
    // `pollUntilAdvanced` would hang. The share endpoint sets share_token
    // synchronously, so a single get() of the same id returns the live token.
    const get = vi.fn(async () =>
      rec({ id: 7, bundle_url: "SAME", share_token: "tok-new" }),
    )
    const fresh = await refreshShareTokenStep(7, { get })
    expect(get).toHaveBeenCalledTimes(1)
    expect(get).toHaveBeenCalledWith(7)
    expect(fresh?.share_token).toBe("tok-new")
    expect(fresh?.bundle_url).toBe("SAME") // no bundle advance required
  })

  it("returns null when there is no current prototype id — no fetch", async () => {
    const get = vi.fn()
    const fresh = await refreshShareTokenStep(null, { get })
    expect(fresh).toBeNull()
    expect(get).not.toHaveBeenCalled()
  })

  it("returns null (silent) when the re-fetch fails — the local share link still stands (AC6/AC7)", async () => {
    const get = vi.fn(async () => {
      throw new Error("network boom")
    })
    const fresh = await refreshShareTokenStep(7, { get })
    expect(fresh).toBeNull()
  })
})

describe("share-success → launcher refresh mounts CommentsPanel (AC3/AC4, #14 regression)", () => {
  const base: PrototypeRecord = {
    id: 7,
    status: "ready",
    bundle_url: "https://cdn/x/index.html",
    error: null,
    is_complete: false,
    share_mode: "private",
    share_token: null,
  }

  /** Read the `comments` slot the launcher passes to <PostGenerationResult>. */
  function commentsSlot(
    over: Partial<Parameters<typeof DesignAgentLauncherView>[0]> = {},
  ): React.ReactElement | null {
    const pgr = viewChildren(over).find((c) => c.type === PostGenerationResult)
    expect(pgr).toBeTruthy()
    return (pgr!.props as { comments?: React.ReactNode })
      .comments as React.ReactElement | null
  }

  it("a share-success re-poll advances result null→token (same id) → CommentsPanel mounts, no re-mount (test_share_success_triggers_launcher_refresh_mounts_comments)", async () => {
    // BEFORE the Share: share_token is null → the gate holds the comments node
    // closed (exactly the stuck state #14 reports).
    expect(commentsSlot({ result: { ...base, share_token: null } })).toBeFalsy()

    // The share-success single-shot re-poll returns the SAME id with a live token.
    const get = vi.fn(async () => ({ ...base, share_token: "tok-new" }))
    const fresh = await refreshShareTokenStep(base.id, { get })
    expect(fresh?.id).toBe(base.id) // same prototype id → no re-mount
    expect(fresh?.share_token).toBe("tok-new")

    // AFTER: feeding the refreshed record as `result` mounts the share-gated
    // CommentsPanel, addressed by the new token.
    const after = commentsSlot({ result: fresh! })
    expect(after).toBeTruthy()
    expect(after!.type).toBe(CommentsPanel)
    expect((after!.props as { token: string }).token).toBe("tok-new")
    expect((after!.props as { prototypeId: number }).prototypeId).toBe(7)
  })

  it("forwards onShared to the <PostGenerationResult> mount (test_launcher_passes_on_shared_to_post_generation_result, AC2)", () => {
    const onShared = vi.fn()
    const pgr = viewChildren({ result: base, onShared }).find(
      (c) => c.type === PostGenerationResult,
    )
    expect(pgr).toBeTruthy()
    expect((pgr!.props as { onShared?: unknown }).onShared).toBe(onShared)
  })

  it("does NOT alter the iterate/clarify re-poll wiring (AC5: onIterated/onAnswered still threaded)", () => {
    // P6-20 adds a parallel share caller; the iterate/clarify forwarding is untouched.
    // UX-EXPLORE (throwaway — REVERT): IterateComposer now lives in
    // PostGenerationResult's `iterate` slot, so read onIterated off the slot.
    const onIterated = vi.fn()
    const children = viewChildren({ result: base, onIterated })
    const pgr = children.find((c) => c.type === PostGenerationResult)
    expect(pgr).toBeTruthy()
    const iterate = (pgr!.props as { iterate?: React.ReactElement }).iterate
    expect(iterate).toBeTruthy()
    expect(iterate!.type).toBe(IterateComposer)
    expect((iterate!.props as { onIterated?: () => void }).onIterated).toBe(onIterated)
  })
})

// ─── P6-08 (Fix #11 visibility half): fail-loud error surface ────────────────

describe("failureFromGeneration (pure, AC1/AC9)", () => {
  const proto: PrototypeRecord = {
    id: 7,
    status: "ready",
    bundle_url: "https://cdn/x/index.html",
    error: null,
  }

  it("maps a failed outcome to a non-null failure message (test_banner_replaces_silent_revert, AC1)", () => {
    // Regression: on unfixed code there is no `failureFromGeneration` and
    // `handleGenerated` discards the failure → this asserts the failure is now
    // CAPTURED (non-null) rather than silently dropped.
    expect(
      failureFromGeneration({ ok: false, message: "ViteBuildError: boom" }),
    ).toEqual({ message: "ViteBuildError: boom" })
  })

  it("maps a successful outcome to null — clears any prior banner (AC4)", () => {
    expect(failureFromGeneration({ ok: true, prototype: proto })).toBeNull()
  })

  it("returns a single slot so a second failure REPLACES the first (test_second_failure_replaces_banner, AC9)", () => {
    // The state is a single `{ message } | null` slot; consecutive failures
    // each map to a fresh single object holding the LATEST message — no array,
    // no accumulation.
    const first = failureFromGeneration({ ok: false, message: "first" })
    const second = failureFromGeneration({ ok: false, message: "second" })
    expect(first).toEqual({ message: "first" })
    expect(second).toEqual({ message: "second" })
  })
})

describe("DesignAgentLauncherView — fail-loud banner (P6-08, Fix #11)", () => {
  const base: PrototypeRecord = {
    id: 7,
    status: "ready",
    bundle_url: "https://cdn/x/index.html",
    error: null,
    is_complete: false,
    share_mode: "private",
    share_token: null,
  }

  function viewHtml(
    over: Partial<Parameters<typeof DesignAgentLauncherView>[0]> = {},
  ): string {
    const { renderDrawer } = makeDrawerSpy()
    return renderToStaticMarkup(
      React.createElement(DesignAgentLauncherView, {
        prdId: 1,
        figmaFileKey: null,
        open: false,
        setOpen: noop,
        renderDrawer,
        ...over,
      }),
    )
  }

  it("renders the banner (not the bare button alone) on a failure (test_failed_generation_renders_banner_not_bare_button, AC1)", () => {
    // On unfixed code the view has no `failure` prop and renders no banner — the
    // user is left with the bare Generate button. This asserts the banner is now
    // mounted in the launcher view.
    const children = viewChildren({
      failure: { message: "ViteBuildError: boom" },
      onRetry: noop,
    })
    const banner = children.find((c) => c.type === GenerationErrorBanner)
    expect(banner).toBeTruthy()
  })

  it("maps the raw message to human copy before handing it to the banner (AC2)", () => {
    const children = viewChildren({
      failure: { message: "UnresolvedImportRepairExhausted: <Dashboard>" },
      onRetry: noop,
    })
    const banner = children.find(
      (c) => c.type === GenerationErrorBanner,
    ) as React.ReactElement
    expect((banner.props as { reason: string }).reason).toBe(
      "A referenced screen couldn't be built. Try regenerating — describe the screens you want explicitly.",
    )
  })

  it("never lets the raw backend error reach the DOM (AC2)", () => {
    const html = viewHtml({
      failure: {
        message:
          "ViteBuildError: /srv/internal/secret/App.tsx exit=1 stderr-tail",
      },
      onRetry: noop,
    })
    expect(html).toContain("The prototype failed to build. Try regenerating.")
    expect(html).not.toContain("/srv/internal/secret")
    expect(html).not.toContain("stderr-tail")
  })

  it("threads onRetry into the banner so its Retry control re-kicks (test_retry_clears_failure_and_reopens_drawer wiring, AC3)", () => {
    const onRetry = vi.fn()
    const children = viewChildren({
      failure: { message: "boom" },
      onRetry,
    })
    const banner = children.find(
      (c) => c.type === GenerationErrorBanner,
    ) as React.ReactElement
    expect((banner.props as { onRetry: () => void }).onRetry).toBe(onRetry)
  })

  it("shows the banner AND retains the prior result view when both are present (test_failure_and_prior_result_coexist, AC5)", () => {
    const html = viewHtml({
      result: base,
      failure: { message: "ViteBuildError: a retry failed" },
      onRetry: noop,
    })
    expect(html).toContain('data-testid="generation-error-banner"')
    expect(html).toContain('data-testid="post-generation-result"')
  })

  it("renders NO banner on the happy path (test_happy_path_unchanged_no_banner, AC4)", () => {
    const children = viewChildren({ result: base, failure: null })
    expect(children.find((c) => c.type === GenerationErrorBanner)).toBeFalsy()
    const html = viewHtml({ result: base, failure: null })
    expect(html).not.toContain('data-testid="generation-error-banner"')
    expect(html).toContain('data-testid="post-generation-result"')
  })

  it("renders exactly ONE banner per failure — no stacking (test_second_failure_replaces_banner render, AC9)", () => {
    const html = viewHtml({
      failure: { message: "ViteBuildError: latest" },
      onRetry: noop,
    })
    const count = html.split('data-testid="generation-error-banner"').length - 1
    expect(count).toBe(1)
  })

  it("mounts the banner in the launcher view, NOT the drawer (test_no_drawer_edit, AC7)", () => {
    // The drawer is injected via `renderDrawer` (here a spy that renders null), so
    // a banner appearing in the view tree proves it attaches at the launcher
    // level — not inside DesignAgentDrawer.tsx (the P6-05-owned, untouched file).
    const children = viewChildren({
      failure: { message: "boom" },
      onRetry: noop,
      renderDrawer: () => null,
    })
    expect(children.find((c) => c.type === GenerationErrorBanner)).toBeTruthy()
  })
})

describe("DesignAgentLauncher — exported signatures unchanged (test_launcher_signatures_unchanged, AC8)", () => {
  const proto: PrototypeRecord = {
    id: 7,
    status: "ready",
    bundle_url: "https://cdn/x/index.html",
    error: null,
  }

  it("resultFromGeneration still (result) => PrototypeRecord | null", () => {
    expect(typeof resultFromGeneration).toBe("function")
    expect(resultFromGeneration({ ok: true, prototype: proto })).toBe(proto)
    expect(resultFromGeneration({ ok: false, message: "x" })).toBeNull()
  })

  it("DesignAgentLauncher / DesignAgentLauncherView remain exported components", () => {
    expect(typeof DesignAgentLauncher).toBe("function")
    expect(typeof DesignAgentLauncherView).toBe("function")
  })
})

// ─── Condensed PRD panel threading on the launcher open paths ─────────────────

describe("condensed PRD panel is forwarded on both launcher open paths", () => {
  const sampleResult: PrototypeRecord = {
    id: 7,
    status: "ready",
    bundle_url: "https://cdn/x/bundle/index.html",
    error: null,
    is_complete: false,
    share_mode: "private",
    share_token: null,
  }

  // One tl;dr block: provides Problem/Fix/Impact cards without triggering the
  // "View full PRD" expander (which would recursively render PrdSections).
  const sampleSections = [
    {
      type: "prd-tldr",
      problem: "Users drop off at the checkout step",
      fix: "Streamline to a single-page flow",
      impact: "Estimated 15 percent conversion lift",
    },
  ] as unknown as PrdSection[]

  function panelHtml(
    over: Partial<Parameters<typeof DesignAgentLauncherView>[0]> = {},
  ): string {
    return renderToStaticMarkup(
      React.createElement(DesignAgentLauncherView, {
        prdId: 1,
        figmaFileKey: null,
        open: false,
        setOpen: noop,
        renderDrawer: () => null,
        ...over,
      }),
    )
  }

  it("renders the condensed panel on the in-session result path when sections are provided (test_launcher_in_session_mount_renders_condensed_panel)", () => {
    const html = panelHtml({ result: sampleResult, prdSections: sampleSections })
    expect(html).toContain('data-testid="da-prd-condensed"')
    expect(html).toContain('data-testid="da-prd-pcx-problem"')
    expect(html).toContain('data-testid="da-prd-pcx-fix"')
    expect(html).toContain('data-testid="da-prd-pcx-impact"')
    expect(html).not.toContain("PRD content unavailable.")
  })

  it("threads prdMetaLine into the panel subtitle on the in-session result path (test_meta_line_threads_into_panel_subtitle)", () => {
    const metaLine = "Redesign initiative, Sprint 5"
    const inSessionHtml = panelHtml({
      result: sampleResult,
      prdSections: sampleSections,
      prdMetaLine: metaLine,
    })
    expect(inSessionHtml).toContain(metaLine)
  })

  it("falls back to the empty-state on the in-session path when sections are absent (test_launcher_no_sections_renders_empty_state)", () => {
    const html = panelHtml({ result: sampleResult })
    expect(html).toContain("PRD content unavailable.")
    expect(html).not.toContain('data-testid="da-prd-condensed"')
  })

  it("renders without error and shows the empty-state when the new props are omitted (test_optional_props_keep_callers_type_safe)", () => {
    // All new props are optional — omitting them renders the empty-state, no throw.
    const html = panelHtml({ result: sampleResult })
    expect(html).toContain('data-testid="post-generation-result"')
    expect(html).toContain("PRD content unavailable.")
  })
})
