"use client"

/**
 * P4-01 — F13 light visual property editor (manual edit mode, P1-light per AD13).
 *
 * An internal-only overlay mounted into the `<PrototypeViewer>` chrome slot
 * (P2-05). A signed-in user toggles "edit mode", clicks any element carrying a
 * `data-anchor-id` (AD4) inside the prototype iframe, and tweaks a fixed set of
 * visual properties (text / font-size / padding / color / background) in a small
 * property panel. Each tweak is applied to the live DOM in-place for immediate
 * feedback (AD23 — the LLM is NOT called per keystroke); on "Save edits" the
 * accumulated `{anchor_id, property, old_value, new_value}` triples are POSTed to
 * `POST /v1/design-agent/{id}/manual-edit` (P4-02), whose backend agent loop is
 * what actually commits the change to source. This file only collects intent and
 * renders feedback.
 *
 * Testability split mirrors `CommentsPanel.tsx` exactly: the repo's vitest runs
 * in a `node` env with no jsdom / @testing-library, so the pure markup lives in
 * `ManualEditOverlayView` (SSR-renderable via `renderToStaticMarkup`) and the
 * logic lives in exported pure dependency-injected helpers (`captureEditTarget`,
 * `findEditTargets`, `readElementProperties`, `applyMutationToDom`,
 * `collectEdits`, `runSaveEdits`, `saveErrorMessage`). The container wires React
 * state to those units and reaches the iframe `contentDocument` for selection +
 * mutation.
 *
 * Per BUILD.md §6 this file adds NO CSS to the hot `globals.css`; it uses
 * component-scoped class strings only (`manual-edit-overlay`, `manual-edit-*`).
 *
 * AD4 collision (see [[ad4-collision-by-design]]): one `data-anchor-id` can match
 * N>1 structurally-identical elements (canonical: a ContactForm's Name + Email
 * inputs both hash to `fb3007b5`). Selection keys on the CLICKED concrete node
 * (read + live-mutated for feedback), but the SAVED triple keys on `anchor_id` —
 * so P4-02 commits the change to all N matches. The panel surfaces a "shares an
 * id with N others" affordance when `findEditTargets(doc, anchorId).length > 1`.
 */

import { useEffect, useRef, useState } from "react"
import {
  designAgentApi,
  type EditableProperty,
  type ManualEditResponse,
  type ManualEditTriple,
} from "../../lib/api"

// ---- types ------------------------------------------------------------------

/** The current value bag for the fixed property set, read off a selected
 *  element. UI-only (the wire carries `ManualEditTriple`s). */
export type EditableProps = Record<EditableProperty, string>

/** A single mutation recorded in session order. `collectEdits` folds these to
 *  one triple per (anchor_id, property). Identical shape to the wire triple. */
export type PendingEdit = ManualEditTriple

/** The closed, ordered property set the overlay exposes (AC3 — exactly five). */
export const EDITABLE_PROPERTIES: readonly EditableProperty[] = [
  "text",
  "font-size",
  "padding",
  "color",
  "background",
] as const

/** Spec affordance surfaced when a save fails because an anchor vanished after
 *  an iterate (AC8). */
export const STALE_ANCHOR_MESSAGE =
  "This element no longer exists in the current version — reload and try again"

/** F14 locked-state affordance — matches `IterateComposer.LOCKED_AFFORDANCE`
 *  (P3-14). A complete prototype cannot enter edit mode (AC9). */
export const LOCKED_AFFORDANCE = "Resume iteration to make changes"

// ---- pure helpers (dependency-injected, SSR-free) ---------------------------

/**
 * Walk up from the clicked target to the nearest element carrying a
 * `data-anchor-id` (auto-applied by the Vite plugin, AD4 — the agent never emits
 * it manually) and return that id. Identical primitive to
 * `CommentsPanel.captureAnchorId`. The iframe sandbox is
 * `allow-scripts allow-same-origin` (P2-05), so same-origin DOM is reachable.
 * Returns null when no ancestor carries an anchor id (P4-06 e2e surfaces it if
 * the real build's sandbox blocks `contentDocument`).
 */
export function captureEditTarget(target: Element | null): string | null {
  return target?.closest("[data-anchor-id]")?.getAttribute("data-anchor-id") ?? null
}

/** A document-like surface exposing only the query we need — keeps the helper
 *  testable with a tiny mock (no jsdom) and works against an iframe's
 *  contentDocument. */
export type AnchorQueryable = Pick<Document, "querySelectorAll">

/**
 * Find every element in `doc` carrying `data-anchor-id === anchorId`. Returns an
 * array (possibly length 0, 1, or N>1 — the AD4 collision case). Same shape +
 * defensiveness as `CommentsPanel.findAnchorMatches`: a missing doc or malformed
 * selector yields `[]` rather than throwing, so one bad anchor never breaks the
 * panel.
 */
export function findEditTargets(
  doc: AnchorQueryable | null | undefined,
  anchorId: string,
): Element[] {
  if (!doc || !anchorId) return []
  try {
    return Array.from(
      doc.querySelectorAll(`[data-anchor-id="${anchorId.replace(/"/g, '\\"')}"]`),
    )
  } catch {
    return []
  }
}

/** A getComputedStyle-like reader — injected so the helper is testable without a
 *  real DOM. */
export type StyleReader = (el: Element) => Pick<CSSStyleDeclaration, "getPropertyValue">

/** Map each non-text editable property to its CSS read name (kebab, for
 *  getPropertyValue) + its inline-style write key (camel, for el.style). */
const CSS_PROP: Record<
  Exclude<EditableProperty, "text">,
  { read: string; style: "fontSize" | "padding" | "color" | "backgroundColor" }
> = {
  "font-size": { read: "font-size", style: "fontSize" },
  padding: { read: "padding", style: "padding" },
  color: { read: "color", style: "color" },
  background: { read: "background-color", style: "backgroundColor" },
}

function defaultStyleReader(el: Element): Pick<CSSStyleDeclaration, "getPropertyValue"> {
  if (typeof window !== "undefined" && typeof window.getComputedStyle === "function") {
    return window.getComputedStyle(el as HTMLElement)
  }
  // No computed-style engine (SSR / node-env): fall back to inline style.
  const style = (el as HTMLElement).style
  return {
    getPropertyValue: (p: string) => (style ? style.getPropertyValue(p) : ""),
  } as Pick<CSSStyleDeclaration, "getPropertyValue">
}

/**
 * Read the current values for the fixed property set off a selected element.
 * `text` → `el.textContent`; the four style properties → the resolved computed
 * value the user sees (via the injected `getStyle`, default `getComputedStyle`).
 * These resolved strings become the pristine `old_value` of any edit.
 */
export function readElementProperties(
  el: Element,
  getStyle: StyleReader = defaultStyleReader,
): EditableProps {
  const cs = getStyle(el)
  return {
    text: el.textContent ?? "",
    "font-size": cs.getPropertyValue(CSS_PROP["font-size"].read),
    padding: cs.getPropertyValue(CSS_PROP.padding.read),
    color: cs.getPropertyValue(CSS_PROP.color.read),
    background: cs.getPropertyValue(CSS_PROP.background.read),
  }
}

/**
 * Apply one property change to the live element in-place for immediate visual
 * feedback (AD23). `text` sets `textContent`; the rest set the corresponding
 * inline style. MUST NOT touch `data-anchor-id` (AC4) — it only ever writes
 * textContent or `el.style.*`.
 */
export function applyMutationToDom(
  el: Element,
  property: EditableProperty,
  newValue: string,
): void {
  if (property === "text") {
    el.textContent = newValue
    return
  }
  const target = el as HTMLElement
  if (!target.style) return
  target.style[CSS_PROP[property].style] = newValue
}

/**
 * Fold every recorded mutation to ONE triple per (anchor_id, property): the
 * pristine `old_value` is the value at FIRST selection (first occurrence in
 * session order), the `new_value` is the LATEST value. A property edited then
 * reverted to its pristine value (`old_value === new_value`) is dropped — it is
 * a no-op and never reaches the wire (AC5).
 */
export function collectEdits(pending: PendingEdit[]): ManualEditTriple[] {
  const folded = new Map<string, ManualEditTriple>()
  for (const e of pending) {
    const key = `${e.anchor_id} ${e.property}`
    const existing = folded.get(key)
    if (existing) {
      // Keep the pristine old_value (first seen); take the latest new_value.
      existing.new_value = e.new_value
    } else {
      folded.set(key, { ...e })
    }
  }
  return Array.from(folded.values()).filter((t) => t.old_value !== t.new_value)
}

/**
 * POST the collected triples to the manual-edit route (P4-02). Pure async, deps
 * injected (mirrors `CommentsPanel.runCreateComment`). Rejections propagate to
 * the caller, which maps them via `saveErrorMessage` into the view's error slot
 * (AC8) — this helper never swallows an error.
 */
export async function runSaveEdits({
  prototypeId,
  edits,
  api,
}: {
  prototypeId: number
  edits: ManualEditTriple[]
  api: Pick<typeof designAgentApi, "manualEdit">
}): Promise<ManualEditResponse> {
  return api.manualEdit(prototypeId, { edits })
}

/**
 * Map a save failure to a user-facing message (AC8). A stale-anchor error from
 * P4-02 (the anchor_id vanished after an iterate) gets the spec reload
 * affordance; anything else surfaces the backend message (or a generic
 * fallback). Never returns empty → never a silent failure.
 */
export function saveErrorMessage(err: unknown): string {
  const raw = err instanceof Error ? err.message : ""
  if (/no longer exist|stale anchor|anchor.*not found|not found in the current/i.test(raw)) {
    return STALE_ANCHOR_MESSAGE
  }
  return raw || "Could not save edits. Please try again."
}

/** Queue-position indicator text (mirrors `IterateComposer.queueIndicator`). */
export function queueIndicator(
  resp: { queue_position?: number } | null | undefined,
): string | null {
  const pos = resp?.queue_position ?? 0
  return pos > 0 ? `Queued — position ${pos}` : null
}

// ---- pure view --------------------------------------------------------------

export type SelectedTarget = {
  anchorId: string
  props: EditableProps
  /** N matching elements for this anchor id (AD4). `> 1` → collision affordance. */
  collisionCount: number
}

export type ManualEditOverlayViewProps = {
  /** The overlay renders its toggle only on the internal mount (a prototypeId is
   *  supplied). On the public mount this is false → renders NOTHING (AC10). */
  enabled: boolean
  editMode: boolean
  /** F14: the prototype is complete/locked → toggle disabled with the
   *  "Resume iteration to make changes" affordance, Save cannot fire (AC9). */
  locked?: boolean
  selected?: SelectedTarget | null
  /** True when there is at least one non-no-op pending edit → Save enabled. */
  dirty?: boolean
  busy?: boolean
  error?: string | null
  queued?: string | null
  onToggleEditMode?: () => void
  onPropertyChange?: (property: EditableProperty, value: string) => void
  onSave?: () => void
  onCancel?: () => void
}

const PROPERTY_LABELS: Record<EditableProperty, string> = {
  text: "Text",
  "font-size": "Font size",
  padding: "Padding",
  color: "Color",
  background: "Background",
}

/** Pure presentational overlay — no hooks, no I/O → SSR-renderable in node-env
 *  vitest. The container threads live state + handlers into it. */
export function ManualEditOverlayView({
  enabled,
  editMode,
  locked = false,
  selected = null,
  dirty = false,
  busy = false,
  error = null,
  queued = null,
  onToggleEditMode,
  onPropertyChange,
  onSave,
  onCancel,
}: ManualEditOverlayViewProps) {
  // Internal-only (F13): the public mount supplies no prototypeId → nothing
  // renders. External viewers can view + comment but never manual-edit (AC10).
  if (!enabled) return null

  return (
    <aside className="manual-edit-overlay" data-testid="manual-edit-overlay">
      <header className="manual-edit-header">
        {locked ? (
          <>
            <button
              type="button"
              className="btn manual-edit-toggle"
              data-testid="manual-edit-toggle"
              disabled
            >
              Edit
            </button>
            <p className="manual-edit-locked-note" data-testid="manual-edit-locked-note">
              {LOCKED_AFFORDANCE}
            </p>
          </>
        ) : (
          <button
            type="button"
            className={`btn manual-edit-toggle${editMode ? " manual-edit-toggle--on" : ""}`}
            data-testid="manual-edit-toggle"
            aria-pressed={editMode}
            onClick={() => onToggleEditMode?.()}
          >
            {editMode ? "Done editing" : "Edit"}
          </button>
        )}
      </header>

      {enabled && !locked && editMode && selected && (
        <form
          className="manual-edit-panel"
          data-testid="manual-edit-panel"
          onSubmit={(e) => {
            e.preventDefault()
            onSave?.()
          }}
        >
          <p className="manual-edit-anchor" data-testid="manual-edit-anchor">
            Editing <code>{selected.anchorId}</code>
          </p>

          {selected.collisionCount > 1 && (
            <p className="manual-edit-collision-note" data-testid="manual-edit-collision-note">
              This element shares an id with {selected.collisionCount - 1} others; the
              change will be committed to all matching elements.
            </p>
          )}

          {EDITABLE_PROPERTIES.map((property) => (
            <label
              key={property}
              className="manual-edit-field"
              data-testid={`manual-edit-field-${property}`}
            >
              <span className="manual-edit-field-label">{PROPERTY_LABELS[property]}</span>
              <input
                type="text"
                className="manual-edit-input"
                data-testid={`manual-edit-input-${property}`}
                value={selected.props[property]}
                onChange={(e) => onPropertyChange?.(property, e.target.value)}
              />
            </label>
          ))}

          <div className="manual-edit-actions">
            <button
              type="submit"
              className="btn btn-accent manual-edit-save"
              data-testid="manual-edit-save"
              disabled={busy || !dirty}
            >
              Save edits
            </button>
            <button
              type="button"
              className="btn manual-edit-cancel"
              data-testid="manual-edit-cancel"
              onClick={() => onCancel?.()}
            >
              Cancel
            </button>
          </div>
        </form>
      )}

      {queued && (
        <p className="manual-edit-queued" data-testid="manual-edit-queued">
          {queued}
        </p>
      )}

      {error && (
        <p className="manual-edit-error error" data-testid="manual-edit-error">
          {error}
        </p>
      )}
    </aside>
  )
}

// ---- container --------------------------------------------------------------

export type ManualEditOverlayProps = {
  /** Supplied ONLY on the signed-in/internal surface. Undefined on the public
   *  `/p/<token>` mount → the overlay renders nothing (AC10, F13 internal-only). */
  prototypeId?: number
  /** F14 locked state — a complete prototype cannot enter edit mode (AC9). */
  isComplete?: boolean
  /** Test seam: resolve the prototype iframe's contentDocument. Defaults to
   *  querying the parent document for the PrototypeViewer iframe. */
  getPrototypeDoc?: () => Document | null
}

function defaultGetPrototypeDoc(): Document | null {
  if (typeof document === "undefined") return null
  const iframe = document.querySelector(
    "iframe.da-prototype-iframe",
  ) as HTMLIFrameElement | null
  return iframe?.contentDocument ?? null
}

/** Public component. Renders the edit-mode toggle (internal mount only), and when
 *  edit mode is on, listens for clicks in the prototype iframe to select an
 *  anchored element, mutates it live (AD23) as the user types, and POSTs the
 *  collected triples on Save via `runSaveEdits` + the canonical `designAgentApi`.
 *  Delegates rendering to the pure view. */
export function ManualEditOverlay({
  prototypeId,
  isComplete = false,
  getPrototypeDoc = defaultGetPrototypeDoc,
}: ManualEditOverlayProps) {
  const enabled = prototypeId != null
  const locked = isComplete

  const [editMode, setEditMode] = useState(false)
  const [selected, setSelected] = useState<SelectedTarget | null>(null)
  const [pending, setPending] = useState<PendingEdit[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [queued, setQueued] = useState<string | null>(null)

  // The live clicked element (a DOM node, not serializable state).
  const selectedElRef = useRef<Element | null>(null)
  // Pristine baseline per `${anchorId}\0${property}`, captured at first
  // selection so old_value is the value the user started from (AC5).
  const pristineRef = useRef<Record<string, string>>({})

  // Leaving edit mode (or losing the internal mount) clears any selection.
  useEffect(() => {
    if (!editMode || !enabled || locked) {
      setSelected(null)
      selectedElRef.current = null
    }
  }, [editMode, enabled, locked])

  // While edit mode is on, clicks inside the prototype iframe select the nearest
  // data-anchor-id ancestor and open the property panel. Same-origin DOM is
  // reachable (P2-05 sandbox); P4-06 e2e surfaces it if the real build blocks it.
  useEffect(() => {
    if (!enabled || locked || !editMode) return
    const doc = getPrototypeDoc()
    if (!doc) return

    function onClick(e: Event) {
      const anchorId = captureEditTarget(e.target as Element | null)
      if (!anchorId) return
      const el = (e.target as Element | null)?.closest("[data-anchor-id]") ?? null
      if (!el) return
      e.preventDefault()
      const props = readElementProperties(el)
      const collisionCount = findEditTargets(doc as AnchorQueryable, anchorId).length
      // Seed pristine baselines for any property not yet recorded this session.
      for (const property of EDITABLE_PROPERTIES) {
        const key = `${anchorId} ${property}`
        if (!(key in pristineRef.current)) {
          pristineRef.current[key] = props[property]
        }
      }
      selectedElRef.current = el
      setSelected({ anchorId, props, collisionCount })
    }

    doc.addEventListener("click", onClick, true)
    return () => doc.removeEventListener("click", onClick, true)
  }, [enabled, locked, editMode, getPrototypeDoc])

  function handlePropertyChange(property: EditableProperty, value: string) {
    const target = selectedElRef.current
    const current = selected
    if (!target || !current) return
    // Immediate visual feedback — bypasses the LLM (AD23).
    applyMutationToDom(target, property, value)
    const key = `${current.anchorId} ${property}`
    const oldValue = pristineRef.current[key] ?? current.props[property]
    setPending((prev) => [
      ...prev,
      { anchor_id: current.anchorId, property, old_value: oldValue, new_value: value },
    ])
    setSelected((s) => (s ? { ...s, props: { ...s.props, [property]: value } } : s))
  }

  async function handleSave() {
    const edits = collectEdits(pending)
    if (edits.length === 0 || prototypeId == null) return
    setBusy(true)
    setError(null)
    try {
      const resp = await runSaveEdits({ prototypeId, edits, api: designAgentApi })
      setPending([])
      setSelected(null)
      selectedElRef.current = null
      pristineRef.current = {}
      setEditMode(false)
      setQueued(queueIndicator(resp))
    } catch (e) {
      setError(saveErrorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  function handleCancel() {
    setSelected(null)
    selectedElRef.current = null
    setEditMode(false)
  }

  const dirty = collectEdits(pending).length > 0

  return (
    <ManualEditOverlayView
      enabled={enabled}
      editMode={editMode}
      locked={locked}
      selected={selected}
      dirty={dirty}
      busy={busy}
      error={error}
      queued={queued}
      onToggleEditMode={() => {
        setError(null)
        setQueued(null)
        setEditMode((v) => !v)
      }}
      onPropertyChange={handlePropertyChange}
      onSave={handleSave}
      onCancel={handleCancel}
    />
  )
}
