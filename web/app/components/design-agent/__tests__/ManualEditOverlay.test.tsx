// P4-01 — ManualEditOverlay tests. Node-env vitest (no DOM, no router, no
// testing-library), so — following the CommentsPanel / CompletionBar convention —
// we SSR-render the pure view via renderToStaticMarkup and unit-test the
// extracted helpers (captureEditTarget / findEditTargets / readElementProperties
// / applyMutationToDom / collectEdits / runSaveEdits / saveErrorMessage) with
// injected deps + tiny mock elements.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it, vi } from "vitest"

// Sprntly components carry no `import React`; vitest's esbuild transform uses the
// classic runtime, so expose React globally (CommentsPanel/page test convention)
// rather than touch the shared vitest config.
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import {
  ManualEditOverlay,
  ManualEditOverlayView,
  type ManualEditOverlayViewProps,
  type SelectedTarget,
  EDITABLE_PROPERTIES,
  STALE_ANCHOR_MESSAGE,
  LOCKED_AFFORDANCE,
  captureEditTarget,
  findEditTargets,
  readElementProperties,
  applyMutationToDom,
  collectEdits,
  runSaveEdits,
  saveErrorMessage,
  type PendingEdit,
} from "../ManualEditOverlay"
import { ApiError } from "../../../lib/api"

function sel(over: Partial<SelectedTarget> = {}): SelectedTarget {
  return {
    anchorId: "fb3007b5",
    props: {
      text: "Hello",
      "font-size": "16px",
      padding: "8px",
      color: "rgb(0, 0, 0)",
      background: "rgb(255, 255, 255)",
    },
    collisionCount: 1,
    ...over,
  }
}

function render(props: ManualEditOverlayViewProps): string {
  return renderToStaticMarkup(React.createElement(ManualEditOverlayView, props))
}

/** A tiny element mock: textContent + a style bag + a fixed data-anchor-id. */
function fakeEl(anchorId = "fb3007b5", textContent = "original"): Element {
  const style: Record<string, string> = {}
  return {
    textContent,
    style,
    getAttribute: (k: string) => (k === "data-anchor-id" ? anchorId : null),
  } as unknown as Element
}

// ---- Creation ---------------------------------------------------------------

describe("ManualEditOverlayView — creation", () => {
  it("test_view_renders_toggle_edit_mode_off_inert (AC1)", () => {
    const html = render({ enabled: true, editMode: false })
    expect(html).toContain('data-testid="manual-edit-overlay"')
    expect(html).toContain('data-testid="manual-edit-toggle"')
    expect(html).toContain("Edit")
    // Edit mode OFF → no property panel, no inputs, nothing mutable.
    expect(html).not.toContain('data-testid="manual-edit-panel"')
    expect(html).not.toContain("manual-edit-input-")
  })

  it("test_property_panel_exposes_exactly_five_properties (AC3)", () => {
    const html = render({ enabled: true, editMode: true, selected: sel() })
    for (const property of EDITABLE_PROPERTIES) {
      expect(html).toContain(`data-testid="manual-edit-input-${property}"`)
    }
    // Exactly five inputs — no sixth property (border/margin/gap/… are out).
    const inputCount = (html.match(/data-testid="manual-edit-input-/g) ?? []).length
    expect(inputCount).toBe(5)
    expect(EDITABLE_PROPERTIES).toEqual(["text", "font-size", "padding", "color", "background"])
  })
})

// ---- Selection --------------------------------------------------------------

describe("captureEditTarget — AD4 primitive (AC2)", () => {
  it("test_capture_edit_target_returns_anchor_for_element_and_descendant", () => {
    const anchorEl = {
      getAttribute: (k: string) => (k === "data-anchor-id" ? "fb3007b5" : null),
    }
    // A click on the element itself or any descendant resolves up via closest().
    const target = {
      closest: (s: string) => (s === "[data-anchor-id]" ? anchorEl : null),
    } as unknown as Element
    expect(captureEditTarget(target)).toBe("fb3007b5")
  })

  it("test_capture_edit_target_returns_null_without_anchor_ancestor", () => {
    const target = { closest: () => null } as unknown as Element
    expect(captureEditTarget(target)).toBeNull()
    expect(captureEditTarget(null)).toBeNull()
  })
})

describe("readElementProperties", () => {
  it("reads text from textContent and styles via the injected reader", () => {
    const el = fakeEl("fb3007b5", "Hello")
    const props = readElementProperties(el, () => ({
      getPropertyValue: (p: string) =>
        ({ "font-size": "18px", padding: "4px", color: "rgb(1, 2, 3)", "background-color": "rgb(9, 9, 9)" } as Record<string, string>)[p] ?? "",
    }))
    expect(props).toEqual({
      text: "Hello",
      "font-size": "18px",
      padding: "4px",
      color: "rgb(1, 2, 3)",
      background: "rgb(9, 9, 9)",
    })
  })
})

// ---- Mutation (AD23) --------------------------------------------------------

describe("applyMutationToDom — AD23 live feedback (AC4)", () => {
  it("test_apply_mutation_sets_value_in_place_and_preserves_anchor_id", () => {
    const el = fakeEl("fb3007b5")
    applyMutationToDom(el, "color", "rgb(10, 20, 30)")
    expect((el as unknown as { style: Record<string, string> }).style.color).toBe("rgb(10, 20, 30)")
    // text mutation writes textContent in-place.
    applyMutationToDom(el, "text", "new copy")
    expect(el.textContent).toBe("new copy")
    // data-anchor-id is never touched by a mutation.
    expect(el.getAttribute("data-anchor-id")).toBe("fb3007b5")
  })
})

// ---- Edit collection --------------------------------------------------------

describe("collectEdits — fold + de-dup (AC5)", () => {
  it("test_collect_edits_folds_repeat_edits_keeps_pristine_old_value", () => {
    const pending: PendingEdit[] = [
      { anchor_id: "a", property: "color", old_value: "red", new_value: "blue" },
      { anchor_id: "a", property: "color", old_value: "blue", new_value: "green" },
    ]
    const triples = collectEdits(pending)
    expect(triples).toHaveLength(1)
    // pristine old_value (red) kept; latest new_value (green) kept.
    expect(triples[0]).toEqual({
      anchor_id: "a",
      property: "color",
      old_value: "red",
      new_value: "green",
    })
  })

  it("test_collect_edits_drops_noop_when_new_equals_old", () => {
    const pending: PendingEdit[] = [
      { anchor_id: "a", property: "text", old_value: "hi", new_value: "yo" },
      { anchor_id: "a", property: "text", old_value: "yo", new_value: "hi" },
    ]
    // Edited then reverted to pristine → no-op, dropped from the payload.
    expect(collectEdits(pending)).toEqual([])
  })
})

// ---- Save -------------------------------------------------------------------

describe("runSaveEdits + Save affordance (AC6)", () => {
  it("test_save_calls_manual_edit_once_with_triples", async () => {
    const manualEdit = vi
      .fn()
      .mockResolvedValue({ prototype_id: 5, status: "generating", queue_position: 0 })
    const edits = [
      { anchor_id: "a", property: "color", old_value: "red", new_value: "blue" } as const,
    ]
    const resp = await runSaveEdits({ prototypeId: 5, edits, api: { manualEdit } })
    expect(manualEdit).toHaveBeenCalledTimes(1)
    expect(manualEdit).toHaveBeenCalledWith(5, { edits })
    expect(resp.queue_position).toBe(0)
  })

  it("test_save_disabled_for_empty_edit_set", () => {
    // View: with no dirty edits the Save button is disabled (user cannot fire it).
    const disabled = render({ enabled: true, editMode: true, selected: sel(), dirty: false })
    expect(disabled).toMatch(/data-testid="manual-edit-save"[^>]*disabled/)
    const enabled = render({ enabled: true, editMode: true, selected: sel(), dirty: true })
    expect(enabled).not.toMatch(/data-testid="manual-edit-save"[^>]*disabled/)
    // Helper: an empty pending set folds to no triples → handleSave early-returns.
    expect(collectEdits([])).toEqual([])
  })
})

// ---- Edge cases -------------------------------------------------------------

describe("AD4 collision (AC7)", () => {
  it("test_ad4_collision_renders_affordance_and_anchor_only_triple", () => {
    // findEditTargets returns N>1 for a collided anchor (canonical fb3007b5).
    const fake = {} as Element
    const doc = {
      querySelectorAll: vi.fn(() => [fake, fake] as unknown as NodeListOf<Element>),
    }
    expect(findEditTargets(doc, "fb3007b5")).toHaveLength(2)

    // Panel renders the "shares an id with N others" affordance without throwing.
    const html = render({ enabled: true, editMode: true, selected: sel({ collisionCount: 2 }) })
    expect(html).toContain('data-testid="manual-edit-collision-note"')
    expect(html).toMatch(/shares an id with 1 others/)

    // The saved triple keys ONLY on anchor_id — no per-element selector key.
    const triples = collectEdits([
      { anchor_id: "fb3007b5", property: "color", old_value: "red", new_value: "blue" },
    ])
    expect(Object.keys(triples[0]).sort()).toEqual([
      "anchor_id",
      "new_value",
      "old_value",
      "property",
    ])
  })

  it("findEditTargets is defensive: empty doc / no anchor → [] (no throw)", () => {
    expect(findEditTargets(null, "x")).toEqual([])
    expect(
      findEditTargets({ querySelectorAll: vi.fn(() => [] as unknown as NodeListOf<Element>) }, ""),
    ).toEqual([])
  })
})

describe("locked-state F14 (AC9)", () => {
  it("test_locked_prototype_disables_edit_mode", () => {
    const html = render({ enabled: true, editMode: true, locked: true, selected: sel() })
    expect(html).toContain('data-testid="manual-edit-locked-note"')
    expect(html).toContain(LOCKED_AFFORDANCE)
    // Toggle is disabled, and the property panel does not render while locked.
    expect(html).toMatch(/data-testid="manual-edit-toggle"[^>]*disabled/)
    expect(html).not.toContain('data-testid="manual-edit-panel"')
  })

  it("container with isComplete renders the disabled toggle (SSR, effects skipped)", () => {
    const html = renderToStaticMarkup(
      React.createElement(ManualEditOverlay, { prototypeId: 5, isComplete: true }),
    )
    expect(html).toContain('data-testid="manual-edit-locked-note"')
    expect(html).toMatch(/data-testid="manual-edit-toggle"[^>]*disabled/)
  })
})

describe("internal-only F13 (AC10)", () => {
  it("test_overlay_renders_nothing_without_prototype_id", () => {
    // Public mount: no prototypeId supplied → the container renders nothing.
    const html = renderToStaticMarkup(React.createElement(ManualEditOverlay, {}))
    expect(html).toBe("")
    // The view honours the same gate directly.
    expect(render({ enabled: false, editMode: true, selected: sel() })).toBe("")
  })

  it("renders the toggle when a prototypeId is supplied (signed-in mount)", () => {
    const html = renderToStaticMarkup(React.createElement(ManualEditOverlay, { prototypeId: 5 }))
    expect(html).toContain('data-testid="manual-edit-toggle"')
  })
})

// ---- Error handling ---------------------------------------------------------

describe("stale-anchor error surfacing (AC8)", () => {
  it("test_stale_anchor_error_surfaces_visible_message", async () => {
    // The route signals the anchor vanished after an iterate.
    const err = new ApiError(400, {
      detail: "anchor fb3007b5 no longer exists in the current bundle",
    })
    // saveErrorMessage maps it to the spec reload affordance (not silent).
    expect(saveErrorMessage(err)).toBe(STALE_ANCHOR_MESSAGE)

    // runSaveEdits propagates the rejection so the container's catch can fire.
    const manualEdit = vi.fn().mockRejectedValue(err)
    await expect(
      runSaveEdits({
        prototypeId: 5,
        edits: [{ anchor_id: "fb3007b5", property: "text", old_value: "a", new_value: "b" }],
        api: { manualEdit },
      }),
    ).rejects.toBe(err)

    // The view renders the message in its error slot — visible, not silent.
    const html = render({ enabled: true, editMode: false, error: STALE_ANCHOR_MESSAGE })
    expect(html).toContain('data-testid="manual-edit-error"')
    expect(html).toContain(STALE_ANCHOR_MESSAGE)
  })

  it("saveErrorMessage falls back to a generic message for unknown errors", () => {
    expect(saveErrorMessage(new Error(""))).toBe("Could not save edits. Please try again.")
    expect(saveErrorMessage(new Error("queue full"))).toBe("queue full")
  })
})
