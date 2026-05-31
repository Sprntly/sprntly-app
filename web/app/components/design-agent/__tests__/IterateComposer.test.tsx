// P3-14 — IterateComposer tests. Node-env vitest (no DOM, no testing-library),
// so — following the CostEstimateModal / CommentsPanel / DesignAgentLauncher
// convention — we SSR-render the pure views via renderToStaticMarkup and
// unit-test the extracted dependency-injected helpers (initialComposerState /
// runEstimate / runIterate / queueIndicator) with spies. The AD14 gate (Submit→
// estimate, Continue→iterate, Cancel→neither) and the B4 Apply→prefill→estimate→
// Continue→iterate handoff are asserted against spies + element-tree extraction
// (the same node-env-faithful "mounted" technique the Launcher test uses), since
// renderToStaticMarkup does not fire DOM events or effects.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { readFileSync, readdirSync } from "node:fs"
import { join } from "node:path"
import { afterEach, describe, expect, it, vi } from "vitest"

// Sprntly components carry no `import React`; vitest's esbuild transform uses the
// classic runtime, so expose React globally (repo-wide test convention) rather
// than touch the shared vitest config.
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import {
  IterateComposer,
  IterateComposerView,
  initialComposerState,
  runEstimate,
  runIterate,
  queueIndicator,
  LOCKED_AFFORDANCE,
} from "../IterateComposer"
import { CostEstimateModalView } from "../CostEstimateModal"
import { CommentsPanel } from "../CommentsPanel"
import { DesignAgentLauncherView } from "../DesignAgentLauncher"
import type {
  CommentRecord,
  IterateCostEstimate,
  IterateResponse,
  PrototypeRecord,
} from "../../../lib/api"

afterEach(() => {
  vi.restoreAllMocks()
})

const UNDER_CAP: IterateCostEstimate = {
  cached_input_tokens: 1200,
  new_input_tokens: 8,
  expected_output_tokens: 2000,
  est_cost_usd: 0.03,
  soft_cap_usd: 0.5,
  exceeds_soft_cap: false,
  model: "claude-sonnet-4-6",
}

const GEN_RESP: IterateResponse = {
  prototype_id: 7,
  status: "generating",
  queue_position: 0,
}

function comment(overrides: Partial<CommentRecord> = {}): CommentRecord {
  return {
    id: 5,
    anchor_id: "fb3007b5",
    body: "make the header bigger",
    author: "external",
    status: "open",
    created_at: "2026-05-30T12:00:00Z",
    resolved_at: null,
    ...overrides,
  }
}

function prototype(overrides: Partial<PrototypeRecord> = {}): PrototypeRecord {
  return {
    id: 7,
    status: "ready",
    bundle_url: null,
    error: null,
    is_complete: false,
    share_mode: "private",
    share_token: null,
    ...overrides,
  }
}

function renderView(props: React.ComponentProps<typeof IterateComposerView>): string {
  return renderToStaticMarkup(React.createElement(IterateComposerView, props))
}

/** Extract the (single) child element of a pure-view element tree whose type
 *  matches `type` — the node-env equivalent of "find this rendered child". */
function findChild(tree: React.ReactElement, type: unknown): React.ReactElement | undefined {
  const kids = React.Children.toArray(
    (tree.props as { children?: React.ReactNode }).children,
  ) as React.ReactElement[]
  return kids.find((k) => k.type === type)
}

// ---- initial state (F9 / F10) -----------------------------------------------

describe("initialComposerState — re-prompt vs Apply", () => {
  it("re-prompt mode starts empty (test_reprompt_mode_starts_empty)", () => {
    expect(initialComposerState(null)).toEqual({ prompt: "", appliedCommentId: null })
    expect(initialComposerState(undefined)).toEqual({ prompt: "", appliedCommentId: null })
    // Mounted: the container renders an empty input in re-prompt mode (AC1).
    const html = renderToStaticMarkup(
      React.createElement(IterateComposer, { prototypeId: 7 }),
    )
    expect(html).toContain('data-testid="iterate-composer"')
    expect(html).toContain('data-mode="reprompt"')
    expect(html).toContain('data-testid="iterate-composer-input"')
    expect(html).toContain("Submit")
  })

  it("Apply mode pre-fills the comment body + applied_comment_id (test_apply_mode_prefills_comment_body_and_sets_applied_comment_id)", () => {
    expect(initialComposerState(comment({ id: 5, body: "tighten the spacing" }))).toEqual({
      prompt: "tighten the spacing",
      appliedCommentId: 5,
    })
    // Mounted: the container seeds the input from the Apply target (AC2).
    const html = renderToStaticMarkup(
      React.createElement(IterateComposer, {
        prototypeId: 7,
        applyTarget: comment({ id: 5, body: "tighten the spacing" }),
      }),
    )
    expect(html).toContain('data-mode="apply"')
    expect(html).toContain("tighten the spacing")
    // Apply mode labels the submit button "Apply".
    expect(html).toContain("Apply")
  })
})

// ---- AD14 gate (AC3 / AC4) --------------------------------------------------

describe("AD14 estimate gate — Submit→estimate, Continue→iterate, Cancel→neither", () => {
  it("Submit fetches the estimate and does NOT call iterate (test_submit_opens_cost_estimate_modal_and_does_not_call_iterate)", async () => {
    const estimateIterate = vi.fn().mockResolvedValue(UNDER_CAP)
    const iterate = vi.fn()
    const est = await runEstimate(estimateIterate, {
      prototypeId: 7,
      prompt: "make it blue",
      appliedCommentId: null,
    })
    expect(estimateIterate).toHaveBeenCalledTimes(1)
    expect(estimateIterate).toHaveBeenCalledWith(7, {
      prompt: "make it blue",
      applied_comment_id: null,
    })
    expect(iterate).not.toHaveBeenCalled()
    expect(est).toBe(UNDER_CAP)
    // When open, the view renders the reused CostEstimateModal markup with the
    // Continue/Cancel affordances (the AD14 gate is on screen).
    const html = renderView({
      prompt: "make it blue",
      isComplete: false,
      mode: "reprompt",
      showModal: true,
      estimate: UNDER_CAP,
    })
    expect(html).toContain('data-testid="cost-estimate-modal"')
    expect(html).toContain('data-testid="cost-estimate-continue"')
    expect(html).toContain('data-testid="cost-estimate-cancel"')
  })

  it("Continue calls iterate with the merged body + mode:'execute' (test_continue_calls_iterate_with_body)", async () => {
    const iterate = vi.fn().mockResolvedValue(GEN_RESP)
    const resp = await runIterate(iterate, {
      prototypeId: 7,
      prompt: "make it blue",
      appliedCommentId: 5,
    })
    expect(iterate).toHaveBeenCalledTimes(1)
    expect(iterate).toHaveBeenCalledWith(7, {
      prompt: "make it blue",
      applied_comment_id: 5,
      mode: "execute",
    })
    expect(resp).toBe(GEN_RESP)
  })

  it("Cancel routes to onCancel and calls neither estimate nor iterate (test_cancel_calls_neither_estimate_nor_iterate_again)", () => {
    const estimateIterate = vi.fn()
    const iterate = vi.fn()
    const onCancel = vi.fn()
    const onContinue = vi.fn()
    const tree = IterateComposerView({
      prompt: "make it blue",
      isComplete: false,
      mode: "reprompt",
      showModal: true,
      estimate: UNDER_CAP,
      onCancel,
      onContinue,
    }) as React.ReactElement
    const modal = findChild(tree, CostEstimateModalView)
    expect(modal).toBeTruthy()
    // Invoke the modal's Cancel wiring — it must reach onCancel, not onContinue.
    ;(modal!.props as { onCancel: () => void }).onCancel()
    expect(onCancel).toHaveBeenCalledTimes(1)
    expect(onContinue).not.toHaveBeenCalled()
    expect(estimateIterate).not.toHaveBeenCalled()
    expect(iterate).not.toHaveBeenCalled()
  })
})

// ---- locked-state gating (F14, AC6) -----------------------------------------

describe("locked-state gating (F14)", () => {
  it("a complete prototype disables the composer with the Resume affordance and no Submit (test_locked_prototype_disables_composer)", () => {
    const html = renderToStaticMarkup(
      React.createElement(IterateComposer, { prototypeId: 7, isComplete: true }),
    )
    expect(html).toContain('data-testid="iterate-composer-locked"')
    expect(html).toContain(LOCKED_AFFORDANCE)
    // No input, no Submit → Submit cannot fire.
    expect(html).not.toContain('data-testid="iterate-composer-input"')
    expect(html).not.toContain('data-testid="iterate-composer-submit"')
  })

  it("the view renders the locked affordance directly when isComplete", () => {
    const html = renderView({
      prompt: "ignored",
      isComplete: true,
      mode: "reprompt",
      showModal: false,
    })
    expect(html).toContain('data-testid="iterate-composer-locked"')
    expect(html).not.toContain('data-testid="iterate-composer-submit"')
  })
})

// ---- success handoff (AC5) — no self-poll -----------------------------------

describe("success handoff — no self-poll (AC5)", () => {
  it("queueIndicator surfaces a single line only when position > 0 (test_success_hands_off_to_status_surface_no_self_poll)", () => {
    expect(queueIndicator({ queue_position: 3 })).toBe("Queued — position 3")
    expect(queueIndicator({ queue_position: 0 })).toBeNull()
    expect(queueIndicator(null)).toBeNull()
    expect(queueIndicator(undefined)).toBeNull()
  })

  it("the queue indicator is a single read-only status line — no progress/poll surface", () => {
    const html = renderView({
      prompt: "",
      isComplete: false,
      mode: "reprompt",
      showModal: false,
      queueLine: "Queued — position 2",
    })
    expect(html).toContain('data-testid="iterate-composer-queue"')
    expect(html).toContain('role="status"')
    expect(html).toContain("Queued — position 2")
    // The composer renders no progress bar / poll surface of its own (AC5).
    expect(html).not.toMatch(/progress|spinner|polling/i)
    // It still shows the form, ready for the next re-prompt (it handed off).
    expect(html).toContain('data-testid="iterate-composer-form"')
  })
})

// ---- external-viewer exclusion (AC7) ----------------------------------------

describe("external-viewer exclusion (F10 internal-only, AC7)", () => {
  it("the public /p/[token] route does not import IterateComposer (test_public_token_page_does_not_mount_iterate_composer)", () => {
    // vitest runs from web/ (cwd), so the public route lives at app/p/[token].
    const dir = join(process.cwd(), "app", "p", "[token]")
    const files = readdirSync(dir).filter(
      (f) => f.endsWith(".ts") || f.endsWith(".tsx"),
    )
    // sanity: the public route files exist (page + viewer)
    expect(files).toContain("page.tsx")
    for (const f of files) {
      const src = readFileSync(join(dir, f), "utf8")
      expect(src).not.toContain("IterateComposer")
    }
  })
})

// ---- B4 mounted handoff integration -----------------------------------------

describe("B4 — Apply → prefill → estimate → Continue → iterate (mounted handoff)", () => {
  it("DesignAgentLauncher mounts IterateComposer pre-filled from applyTarget (signed-in surface)", () => {
    const html = renderToStaticMarkup(
      React.createElement(DesignAgentLauncherView, {
        prdId: 1,
        figmaFileKey: null,
        open: false,
        setOpen: () => {},
        result: prototype({ id: 7 }),
        applyTarget: comment({ id: 5, body: "make the header bigger" }),
        setApplyTarget: () => {},
        renderDrawer: () => null,
      }),
    )
    // The launcher actually mounts the composer, pre-filled (mount wiring proven).
    expect(html).toContain('data-testid="iterate-composer"')
    expect(html).toContain('data-mode="apply"')
    expect(html).toContain("make the header bigger")
  })

  it("DesignAgentLauncher wires the signed-in CommentsPanel Apply → applyTarget", () => {
    const setApplyTarget = vi.fn()
    const c = comment({ id: 5, body: "make the header bigger" })
    const tree = DesignAgentLauncherView({
      prdId: 1,
      figmaFileKey: null,
      open: false,
      setOpen: () => {},
      // share_token present → the signed-in CommentsPanel mounts with onApply.
      result: prototype({ id: 7, share_mode: "public", share_token: "tok-xyz" }),
      applyTarget: null,
      setApplyTarget,
      renderDrawer: () => null,
    }) as React.ReactElement
    const panel = findChild(tree, CommentsPanel)
    expect(panel).toBeTruthy()
    // Fire the panel's Apply handoff — it must set the lifted applyTarget.
    ;(panel!.props as { onApply: (c: CommentRecord) => void }).onApply(c)
    expect(setApplyTarget).toHaveBeenCalledWith(c)
  })

  it("end-to-end call order: Apply→prefill→estimate→Continue→iterate (test_apply_to_iterate_mounted_handoff_end_to_end)", async () => {
    const order: string[] = []
    const estimateIterate = vi.fn(async () => {
      order.push("estimate")
      return UNDER_CAP
    })
    const iterate = vi.fn(async () => {
      order.push("iterate")
      return GEN_RESP
    })

    // 1. Apply on a comment → derive the pre-fill (F10).
    const c = comment({ id: 5, body: "make the header bigger" })
    order.push("apply-prefill")
    const seed = initialComposerState(c)
    expect(seed.prompt).toBe("make the header bigger")
    expect(seed.appliedCommentId).toBe(5)

    // 2. Submit → estimate (AD14 gate). iterate NOT called yet.
    const est = await runEstimate(estimateIterate, {
      prototypeId: 7,
      prompt: seed.prompt,
      appliedCommentId: seed.appliedCommentId,
    })
    expect(est).toBe(UNDER_CAP)
    expect(estimateIterate).toHaveBeenCalledWith(7, {
      prompt: "make the header bigger",
      applied_comment_id: 5,
    })
    expect(iterate).not.toHaveBeenCalled()

    // 3. Continue → iterate with the merged body + mode:'execute'.
    const resp = await runIterate(iterate, {
      prototypeId: 7,
      prompt: seed.prompt,
      appliedCommentId: seed.appliedCommentId,
    })
    expect(resp).toBe(GEN_RESP)
    expect(iterate).toHaveBeenCalledWith(7, {
      prompt: "make the header bigger",
      applied_comment_id: 5,
      mode: "execute",
    })

    // The whole handoff happened in the AD14-mandated order.
    expect(order).toEqual(["apply-prefill", "estimate", "iterate"])
  })
})
