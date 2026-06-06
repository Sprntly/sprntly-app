// P3-14 — IterateComposer tests. Node-env vitest (no DOM, no testing-library),
// so — following the repo's renderToStaticMarkup convention — pure views are
// SSR-rendered for markup assertions. For the load-bearing AD14 cost-gate
// invariant (AC3) and the B4 handoff we DRIVE THE REAL CONTAINER HANDLERS (not
// the extracted free helpers) against spies on the REAL designAgentApi methods,
// so the gate is genuinely locked: a future edit that moved an `iterate` call
// into Submit would make `onSubmit` call iterate and fail these tests.
//
// Driving the container in node-env: the components read the classic JSX factory
// from `globalThis.React`, so `driveContainer` wraps that `createElement` to
// capture the props the container passes to its View (including the live
// `onSubmit`/`onContinue`/`onCancel` = the container's handleSubmit/handleContinue/
// handleCancel closures), renders the container with renderToStaticMarkup, then
// restores. useState setters fired by those handlers post-render are no-ops in the
// server renderer (verified), so the handlers' API calls run while their setState
// calls harmlessly no-op — exactly what we assert against.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { readFileSync, readdirSync } from "node:fs"
import { join } from "node:path"
import { afterEach, describe, expect, it, vi } from "vitest"

// Sprntly components carry no `import React`; the classic JSX runtime reads
// `globalThis.React`, so expose it (repo-wide test convention).
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
import { CommentsPanel } from "../CommentsPanel"
import { DesignAgentLauncherView } from "../DesignAgentLauncher"
import { PostGenerationResult } from "../PostGenerationResult"
import { designAgentApi } from "../../../lib/api"
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

/**
 * Render the REAL IterateComposer container and return the props it passes to
 * its View — including the live handler closures (`onSubmit` = handleSubmit,
 * `onContinue` = handleContinue, `onCancel` = handleCancel). Wraps the classic
 * JSX factory on `globalThis.React` (the factory the component reads) so we can
 * capture the View props without mocking the same-module View or redefining the
 * non-configurable `React.createElement` export.
 */
function driveContainer(
  props: React.ComponentProps<typeof IterateComposer>,
): React.ComponentProps<typeof IterateComposerView> {
  const realReact = (globalThis as { React?: typeof React }).React!
  const realCreate = realReact.createElement
  const calls: Array<[unknown, Record<string, unknown> | null]> = []
  ;(globalThis as { React?: unknown }).React = {
    ...realReact,
    createElement: (type: unknown, p: Record<string, unknown> | null, ...kids: unknown[]) => {
      calls.push([type, p])
      return (realCreate as (...a: unknown[]) => unknown)(type, p, ...kids)
    },
  }
  try {
    renderToStaticMarkup(
      (realCreate as (...a: unknown[]) => React.ReactElement)(IterateComposer, props),
    )
  } finally {
    ;(globalThis as { React?: unknown }).React = realReact
  }
  const call = calls.find((c) => c[0] === IterateComposerView)
  return (call?.[1] ?? {}) as React.ComponentProps<typeof IterateComposerView>
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
    // Mounted: the container seeds the input from the Apply target (AC2). Asserted
    // against the real container's View props, not just SSR markup.
    const viewProps = driveContainer({
      prototypeId: 7,
      applyTarget: comment({ id: 5, body: "tighten the spacing" }),
    })
    expect(viewProps.mode).toBe("apply")
    expect(viewProps.prompt).toBe("tighten the spacing")
  })
})

// ---- AD14 gate (AC3) — driven through the REAL container handlers ------------

describe("AD14 estimate gate (AC3) — Submit→estimate, Continue→iterate, Cancel→neither", () => {
  it("Submit (container handleSubmit) calls estimateIterate and NOT iterate (test_submit_opens_cost_estimate_modal_and_does_not_call_iterate)", async () => {
    const est = vi.spyOn(designAgentApi, "estimateIterate").mockResolvedValue(UNDER_CAP)
    const iter = vi.spyOn(designAgentApi, "iterate").mockResolvedValue(GEN_RESP)

    const viewProps = driveContainer({ prototypeId: 7, applyTarget: null })
    // re-prompt: nothing to submit on an empty body — type something first by
    // driving with an Apply target so prompt is non-empty (Submit is gated on a
    // non-empty body).
    const filled = driveContainer({
      prototypeId: 7,
      applyTarget: comment({ id: 5, body: "make it blue" }),
    })
    expect(typeof filled.onSubmit).toBe("function")

    await filled.onSubmit!()
    expect(est).toHaveBeenCalledTimes(1)
    expect(est).toHaveBeenCalledWith(7, { prompt: "make it blue", applied_comment_id: 5 })
    // The load-bearing invariant: Submit reaches estimate but NEVER iterate.
    expect(iter).not.toHaveBeenCalled()

    // An empty re-prompt body cannot Submit (the button is disabled): driving
    // handleSubmit with an empty body is a no-op (no estimate call).
    est.mockClear()
    await viewProps.onSubmit!()
    expect(est).not.toHaveBeenCalled()

    // The modal markup (Continue/Cancel) is the on-screen AD14 gate.
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

  it("Continue (container handleContinue) calls iterate with the merged body + mode:'execute' (test_continue_calls_iterate_with_body)", async () => {
    const est = vi.spyOn(designAgentApi, "estimateIterate").mockResolvedValue(UNDER_CAP)
    const iter = vi.spyOn(designAgentApi, "iterate").mockResolvedValue(GEN_RESP)

    const viewProps = driveContainer({
      prototypeId: 7,
      applyTarget: comment({ id: 5, body: "make it blue" }),
    })
    expect(typeof viewProps.onContinue).toBe("function")

    await viewProps.onContinue!()
    expect(iter).toHaveBeenCalledTimes(1)
    expect(iter).toHaveBeenCalledWith(7, {
      prompt: "make it blue",
      applied_comment_id: 5,
      mode: "execute",
    })
    // Continue is the ONLY iterate caller — it does not also re-estimate.
    expect(est).not.toHaveBeenCalled()
  })

  it("Cancel (container handleCancel) calls neither estimate (again) nor iterate (test_cancel_calls_neither_estimate_nor_iterate_again)", async () => {
    const est = vi.spyOn(designAgentApi, "estimateIterate").mockResolvedValue(UNDER_CAP)
    const iter = vi.spyOn(designAgentApi, "iterate").mockResolvedValue(GEN_RESP)

    const viewProps = driveContainer({
      prototypeId: 7,
      applyTarget: comment({ id: 5, body: "make it blue" }),
    })
    // Submit once → one estimate.
    await viewProps.onSubmit!()
    expect(est).toHaveBeenCalledTimes(1)
    // Cancel → no API call at all (no second estimate, no iterate).
    expect(typeof viewProps.onCancel).toBe("function")
    viewProps.onCancel!()
    expect(est).toHaveBeenCalledTimes(1)
    expect(iter).not.toHaveBeenCalled()
  })
})

// ---- iterate is reached ONLY via Continue — regression guard -----------------

describe("AD14 gate is genuinely locked", () => {
  it("driving Submit then Continue calls estimate strictly before iterate, each exactly once", async () => {
    const est = vi.spyOn(designAgentApi, "estimateIterate").mockResolvedValue(UNDER_CAP)
    const iter = vi.spyOn(designAgentApi, "iterate").mockResolvedValue(GEN_RESP)

    const viewProps = driveContainer({
      prototypeId: 7,
      applyTarget: comment({ id: 5, body: "make it blue" }),
    })
    await viewProps.onSubmit!()
    expect(iter).not.toHaveBeenCalled() // gate holds: no iterate until Continue
    await viewProps.onContinue!()

    expect(est).toHaveBeenCalledTimes(1)
    expect(iter).toHaveBeenCalledTimes(1)
    // estimate fired before iterate.
    expect(est.mock.invocationCallOrder[0]).toBeLessThan(iter.mock.invocationCallOrder[0])
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

  it("the locked container exposes NO onSubmit handler (Submit cannot fire)", () => {
    const viewProps = driveContainer({ prototypeId: 7, isComplete: true })
    expect(viewProps.isComplete).toBe(true)
    expect(viewProps.onSubmit).toBeUndefined()
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
    // P6-13 (UX-3): CommentsPanel was relocated OUT of its direct-sibling position
    // into PostGenerationResult's `comments` prop (so a two-column design-pane grid
    // can wrap viewer + comments). The Apply→applyTarget wiring is preserved
    // byte-identical through the move — locate the panel via the `comments` prop,
    // not as a direct launcher child.
    const pgr = findChild(tree, PostGenerationResult)
    expect(pgr).toBeTruthy()
    const panel = (pgr!.props as { comments?: React.ReactElement | null })
      .comments as React.ReactElement | null
    expect(panel).toBeTruthy()
    expect(panel!.type).toBe(CommentsPanel)
    // Fire the panel's Apply handoff — it must set the lifted applyTarget.
    ;(panel!.props as { onApply: (c: CommentRecord) => void }).onApply(c)
    expect(setApplyTarget).toHaveBeenCalledWith(c)
  })

  it("end-to-end via the REAL container: Apply prefill → Submit→estimate → Continue→iterate (test_apply_to_iterate_mounted_handoff_end_to_end)", async () => {
    const est = vi.spyOn(designAgentApi, "estimateIterate").mockResolvedValue(UNDER_CAP)
    const iter = vi.spyOn(designAgentApi, "iterate").mockResolvedValue(GEN_RESP)

    // 1. Apply on a comment → the container pre-fills from the Apply target (F10).
    const c = comment({ id: 5, body: "make the header bigger" })
    const viewProps = driveContainer({ prototypeId: 7, applyTarget: c })
    expect(viewProps.mode).toBe("apply")
    expect(viewProps.prompt).toBe("make the header bigger")

    // 2. Submit → estimate (AD14 gate); iterate NOT called yet.
    await viewProps.onSubmit!()
    expect(est).toHaveBeenCalledWith(7, {
      prompt: "make the header bigger",
      applied_comment_id: 5,
    })
    expect(iter).not.toHaveBeenCalled()

    // 3. Continue → iterate with the merged body + mode:'execute'.
    await viewProps.onContinue!()
    expect(iter).toHaveBeenCalledWith(7, {
      prompt: "make the header bigger",
      applied_comment_id: 5,
      mode: "execute",
    })

    // The whole handoff happened in the AD14-mandated order.
    expect(est.mock.invocationCallOrder[0]).toBeLessThan(iter.mock.invocationCallOrder[0])
  })
})

// ---- P6-05 (#5): onIterated re-poll callback (AC9) ---------------------------

describe("onIterated callback (P6-05 #5, AC9)", () => {
  it("fires onIterated after a successful runIterate, without re-estimating (test_iterate_composer_fires_on_iterated)", async () => {
    const est = vi.spyOn(designAgentApi, "estimateIterate").mockResolvedValue(UNDER_CAP)
    const iter = vi.spyOn(designAgentApi, "iterate").mockResolvedValue(GEN_RESP)
    const onIterated = vi.fn()

    const viewProps = driveContainer({
      prototypeId: 7,
      applyTarget: comment({ id: 5, body: "make it blue" }),
      onIterated,
    })
    await viewProps.onContinue!()

    expect(iter).toHaveBeenCalledTimes(1)
    expect(onIterated).toHaveBeenCalledTimes(1)
    // AD14 flow unchanged: Continue is still the only iterate path (no estimate here).
    expect(est).not.toHaveBeenCalled()
  })

  it("does NOT fire onIterated when runIterate throws", async () => {
    vi.spyOn(designAgentApi, "iterate").mockRejectedValue(new Error("boom"))
    const onIterated = vi.fn()
    const viewProps = driveContainer({
      prototypeId: 7,
      applyTarget: comment({ id: 5, body: "x" }),
      onIterated,
    })
    await viewProps.onContinue!()
    expect(onIterated).not.toHaveBeenCalled()
  })

  it("existing callers omitting onIterated still type-check and Continue still works (test_iterate_composer_existing_callers_typecheck, AC9)", async () => {
    const iter = vi.spyOn(designAgentApi, "iterate").mockResolvedValue(GEN_RESP)
    // No onIterated prop — the optional/defaulted prop keeps the old call valid.
    const viewProps = driveContainer({
      prototypeId: 7,
      applyTarget: comment({ id: 5, body: "x" }),
    })
    await viewProps.onContinue!()
    expect(iter).toHaveBeenCalledTimes(1)
  })
})

// ---- helper-level contract (kept as cheap unit coverage) --------------------

describe("helpers — runEstimate / runIterate body shaping", () => {
  it("runEstimate posts prompt + applied_comment_id, never mode", async () => {
    const estimateIterate = vi.fn().mockResolvedValue(UNDER_CAP)
    await runEstimate(estimateIterate, { prototypeId: 7, prompt: "x", appliedCommentId: 9 })
    expect(estimateIterate).toHaveBeenCalledWith(7, { prompt: "x", applied_comment_id: 9 })
  })

  it("runIterate pins mode:'execute' and forwards applied_comment_id", async () => {
    const iterate = vi.fn().mockResolvedValue(GEN_RESP)
    await runIterate(iterate, { prototypeId: 7, prompt: "x", appliedCommentId: null })
    expect(iterate).toHaveBeenCalledWith(7, {
      prompt: "x",
      applied_comment_id: null,
      mode: "execute",
    })
  })
})

// ---- lock / unlock state threading -------------------------------------------

describe("Lock/Unlock state threading (regression + state)", () => {
  it("isComplete=false renders the active form; isComplete=true renders the locked surface (test_b4_is_complete_reaches_iterate_composer_after_mark_complete)", () => {
    const unlocked = renderToStaticMarkup(
      React.createElement(IterateComposer, { prototypeId: 7, isComplete: false }),
    )
    expect(unlocked).toContain('data-testid="iterate-composer"')
    expect(unlocked).not.toContain('data-testid="iterate-composer-locked"')

    const locked = renderToStaticMarkup(
      React.createElement(IterateComposer, { prototypeId: 7, isComplete: true }),
    )
    expect(locked).toContain('data-testid="iterate-composer-locked"')
    expect(locked).not.toContain('data-testid="iterate-composer-form"')
  })

  it("the Unlock button is present when locked (test_b4_unlock_button_shows_when_locked)", () => {
    const html = renderToStaticMarkup(
      React.createElement(IterateComposer, { prototypeId: 7, isComplete: true }),
    )
    expect(html).toContain('data-testid="iterate-composer-unlock"')
  })

  it("Unlock calls designAgentApi.resume and the view renders the active form when unlocked (test_lock_state_local_unlock_optimistic)", async () => {
    const resume = vi
      .spyOn(designAgentApi, "resume")
      .mockResolvedValue({ prototype_id: 7, is_complete: false, handoffs_flagged_stale: 0 })

    // Locked container exposes onUnlock
    const viewProps = driveContainer({ prototypeId: 7, isComplete: true })
    expect(viewProps.isComplete).toBe(true)
    expect(typeof viewProps.onUnlock).toBe("function")

    await viewProps.onUnlock!()
    expect(resume).toHaveBeenCalledWith(7)

    // After the local unlocked flag flips, the composer shows the active form.
    // Verified via the pure view (node-env cannot drive useState changes post-render).
    const activeHtml = renderView({
      prompt: "",
      isComplete: false,
      mode: "reprompt",
      showModal: false,
    })
    expect(activeHtml).toContain('data-testid="iterate-composer-form"')
  })

  it("a fresh render with isComplete=true shows locked regardless of prior unlock (test_lock_state_re_locks_on_prop_flip)", () => {
    // Simulates re-completing after a prior session: new render, state starts fresh.
    const html = renderToStaticMarkup(
      React.createElement(IterateComposer, { prototypeId: 7, isComplete: true }),
    )
    expect(html).toContain('data-testid="iterate-composer-locked"')
    expect(html).not.toContain('data-testid="iterate-composer-form"')
  })

  it("Unlock calls designAgentApi.resume exactly once (test_unlock_calls_resume_api_once)", async () => {
    const resume = vi
      .spyOn(designAgentApi, "resume")
      .mockResolvedValue({ prototype_id: 7, is_complete: false, handoffs_flagged_stale: 0 })

    const viewProps = driveContainer({ prototypeId: 7, isComplete: true })
    await viewProps.onUnlock!()
    expect(resume).toHaveBeenCalledTimes(1)
    expect(resume).toHaveBeenCalledWith(7)
  })

  it("the unlock-error element renders when resume fails (test_unlock_error_shown_on_resume_failure)", () => {
    const html = renderView({
      prompt: "",
      isComplete: true,
      mode: "reprompt",
      showModal: false,
      unlockError: "Could not unlock the prototype",
    })
    expect(html).toContain('data-testid="iterate-composer-unlock-error"')
    expect(html).toContain("Could not unlock the prototype")
  })

  it("no UX-EXPLORE marker remains on the lock-state or handleUnlock lines (test_no_ux_explore_marker_on_lock_state_path)", () => {
    const src = readFileSync(
      join(process.cwd(), "app", "components", "design-agent", "IterateComposer.tsx"),
      "utf8",
    )
    const lines = src.split("\n")
    // The lock-state comment should be the plain-English version — no UX-EXPLORE.
    const lockCommentIdx = lines.findIndex((l) =>
      l.includes("Local unlock state") && l.includes("isComplete"),
    )
    expect(lockCommentIdx).toBeGreaterThan(-1)

    // The handleUnlock comment should be the plain-English version — no UX-EXPLORE.
    const unlockCommentIdx = lines.findIndex((l) =>
      l.includes("The Unlock action:") && l.includes("resumes"),
    )
    expect(unlockCommentIdx).toBeGreaterThan(-1)

    // Neither of those comment lines contains UX-EXPLORE.
    expect(lines[lockCommentIdx]).not.toContain("UX-EXPLORE")
    expect(lines[unlockCommentIdx]).not.toContain("UX-EXPLORE")
  })

  it("the Unlock button is disabled while unlocking is in progress (test_unlock_busy_state_disables_unlock_button)", () => {
    const html = renderView({
      prompt: "",
      isComplete: true,
      mode: "reprompt",
      showModal: false,
      unlockBusy: true,
    })
    expect(html).toContain('data-testid="iterate-composer-unlock"')
    // unlockBusy=true changes the button label to "Unlocking…"
    expect(html).toContain("Unlocking…")
  })
})

// ---- iterate cost-confirm skip (skipCostConfirm) -----------------------------
// The iterate path may run Submit directly, skipping the pre-flight cost-estimate
// confirmation modal. The default (skipCostConfirm = false) keeps the modal for
// any non-iterate caller. These tests lock that the skip is opt-in per mount and
// that the estimate gate is otherwise untouched.

describe("iterate cost-confirm skip — skipCostConfirm bypasses the estimate gate", () => {
  it("Submit WITH skipCostConfirm runs the iteration directly and does not open the estimate modal (test_submit_with_skip_cost_confirm_does_not_open_estimate_modal)", async () => {
    const est = vi.spyOn(designAgentApi, "estimateIterate").mockResolvedValue(UNDER_CAP)
    const iter = vi.spyOn(designAgentApi, "iterate").mockResolvedValue(GEN_RESP)

    // Mounted with skipCostConfirm (no external runner) → Submit calls iterate
    // directly, never estimateIterate.
    const viewProps = driveContainer({
      prototypeId: 7,
      applyTarget: comment({ id: 5, body: "make it blue" }),
      skipCostConfirm: true,
    })
    await viewProps.onSubmit!()
    expect(est).not.toHaveBeenCalled()
    expect(iter).toHaveBeenCalledTimes(1)
    expect(iter).toHaveBeenCalledWith(7, {
      prompt: "make it blue",
      applied_comment_id: 5,
      mode: "execute",
    })

    // No estimate fetched → the cost-estimate modal never renders.
    const html = renderToStaticMarkup(
      React.createElement(IterateComposer, {
        prototypeId: 7,
        applyTarget: comment({ id: 5, body: "make it blue" }),
        skipCostConfirm: true,
      }),
    )
    expect(html).not.toContain('data-testid="cost-estimate-modal"')
  })

  it("Submit WITH skipCostConfirm + a shared external runner delegates to it, calling neither estimate nor iterate here (test_submit_with_skip_cost_confirm_delegates_to_external_runner)", async () => {
    const est = vi.spyOn(designAgentApi, "estimateIterate").mockResolvedValue(UNDER_CAP)
    const iter = vi.spyOn(designAgentApi, "iterate").mockResolvedValue(GEN_RESP)
    const runIterateExternal = vi.fn()

    const viewProps = driveContainer({
      prototypeId: 7,
      applyTarget: comment({ id: 5, body: "make it blue" }),
      skipCostConfirm: true,
      runIterateExternal,
    })
    await viewProps.onSubmit!()
    // The composer hands the run to the shared runner; it does not estimate or
    // POST iterate itself.
    expect(runIterateExternal).toHaveBeenCalledTimes(1)
    expect(runIterateExternal).toHaveBeenCalledWith("make it blue", 5)
    expect(est).not.toHaveBeenCalled()
    expect(iter).not.toHaveBeenCalled()
  })

  it("Submit WITHOUT skipCostConfirm still opens the estimate gate (test_submit_without_skip_cost_confirm_opens_estimate_modal)", async () => {
    const est = vi.spyOn(designAgentApi, "estimateIterate").mockResolvedValue(UNDER_CAP)
    const iter = vi.spyOn(designAgentApi, "iterate").mockResolvedValue(GEN_RESP)

    const viewProps = driveContainer({
      prototypeId: 7,
      applyTarget: comment({ id: 5, body: "make it blue" }),
    })
    await viewProps.onSubmit!()
    // Default path: fetch the estimate, do NOT iterate from Submit.
    expect(est).toHaveBeenCalledTimes(1)
    expect(est).toHaveBeenCalledWith(7, { prompt: "make it blue", applied_comment_id: 5 })
    expect(iter).not.toHaveBeenCalled()

    // The on-screen estimate gate (Continue/Cancel) renders when the modal opens.
    const html = renderView({
      prompt: "make it blue",
      isComplete: false,
      mode: "reprompt",
      showModal: true,
      estimate: UNDER_CAP,
    })
    expect(html).toContain('data-testid="cost-estimate-modal"')
    expect(html).toContain('data-testid="cost-estimate-continue"')
  })

  it("the prop default resolves to false when omitted — Submit gates by default (test_skip_cost_confirm_default_is_false)", async () => {
    const est = vi.spyOn(designAgentApi, "estimateIterate").mockResolvedValue(UNDER_CAP)
    const iter = vi.spyOn(designAgentApi, "iterate").mockResolvedValue(GEN_RESP)

    // Omitting the prop → the default-false gated path (estimate, no direct iterate).
    const viewProps = driveContainer({
      prototypeId: 7,
      applyTarget: comment({ id: 5, body: "x" }),
    })
    await viewProps.onSubmit!()
    expect(est).toHaveBeenCalledTimes(1)
    expect(iter).not.toHaveBeenCalled()

    // The signature default is explicit.
    const src = readFileSync(
      join(process.cwd(), "app", "components", "design-agent", "IterateComposer.tsx"),
      "utf8",
    )
    expect(src).toContain("skipCostConfirm = false")
  })

  it("the skipCostConfirm path carries the durable note, not a throwaway marker, in both files (test_no_ux_explore_marker_on_skip_path)", () => {
    const DURABLE = "intentionally skips the pre-flight cost-estimate confirmation modal"

    // Gather the contiguous comment lines immediately above a target source line.
    function commentBlockAbove(lines: string[], idx: number): string {
      const out: string[] = []
      for (let i = idx - 1; i >= 0; i--) {
        const t = lines[i].trim()
        if (t === "") break
        if (t.startsWith("//") || t.startsWith("*") || t.startsWith("/*")) {
          out.unshift(
            t
              .replace(/^\/\*\*?/, "")
              .replace(/^\*\/?/, "")
              .replace(/^\/\//, "")
              .trim(),
          )
        } else break
      }
      return out.join(" ").replace(/\s+/g, " ").trim()
    }

    // IterateComposer: the JSDoc above the prop declaration.
    const composerSrc = readFileSync(
      join(process.cwd(), "app", "components", "design-agent", "IterateComposer.tsx"),
      "utf8",
    )
    const composerLines = composerSrc.split("\n")
    const propIdx = composerLines.findIndex((l) => l.trim() === "skipCostConfirm?: boolean")
    expect(propIdx).toBeGreaterThan(-1)
    const propDoc = commentBlockAbove(composerLines, propIdx)
    expect(propDoc).toContain(DURABLE)
    expect(propDoc).not.toContain("UX-EXPLORE")

    // ApproveModal: the comment above the prop pass on the primary canvas mount.
    const modalSrc = readFileSync(
      join(process.cwd(), "app", "components", "shared", "ApproveModal.tsx"),
      "utf8",
    )
    const modalLines = modalSrc.split("\n")
    const passIdx = modalLines.findIndex((l) => l.trim() === "skipCostConfirm")
    expect(passIdx).toBeGreaterThan(-1)
    const passDoc = commentBlockAbove(modalLines, passIdx)
    expect(passDoc).toContain(DURABLE)
    expect(passDoc).not.toContain("UX-EXPLORE")

    // No line tying the throwaway marker to the skip path remains in either file.
    for (const src of [composerSrc, modalSrc]) {
      const offending = src
        .split("\n")
        .filter((l) => l.includes("UX-EXPLORE") && l.includes("skipCostConfirm"))
      expect(offending).toEqual([])
    }
  })
})
