"use client"

import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type ReactNode,
} from "react"
import { prdApi } from "../../lib/api"
import type { PrdSaveStatus } from "./PrdHtmlView"
import {
  IconGrid,
  IconLinkInsert,
  IconListBullet,
  IconRedo,
  IconUndo,
} from "./app-icons"

export type { PrdSaveStatus } from "./PrdHtmlView"

export interface PrdMarkdownHandle {
  /** Force an immediate save of the current editable document. Throws on
   *  failure so a caller (the panel's "Save now") can surface a toast — matches
   *  the pre-extraction inline behavior. */
  save: () => Promise<void>
}

// Draft key mirrors the pre-extraction PrdPanelContent key EXACTLY (no scope
// suffix) so a main-chat draft recovers byte-for-byte. A second consumer (the
// project drawer) passes `draftScope` to get a DISTINCT key and never collide
// with the main-chat draft for the same prd_id.
const PRD_DRAFT_KEY = (prdId: number, scope?: string) =>
  scope ? `sprntly_prd_draft_${prdId}_${scope}` : `sprntly_prd_draft_${prdId}`
function loadDraft(prdId: number, scope?: string): string | null {
  try { return localStorage.getItem(PRD_DRAFT_KEY(prdId, scope)) } catch { return null }
}
function saveDraft(prdId: number, html: string, scope?: string) {
  try { localStorage.setItem(PRD_DRAFT_KEY(prdId, scope), html) } catch { /* ignore */ }
}

/** The execCommand formatting toolbar — undo/redo/bold/italic/underline/H1/H2/
 *  bullet-list/link/table + a live save-status pip. Extracted verbatim from
 *  PrdPanelContent so both consumers render the identical control. Exported so
 *  the panel can also render the DISABLED no-document variant it shows in the
 *  empty / generating states (unchanged pre-extraction behavior). */
export function PrdToolbar({ hasDoc, saveStatus, exec }: { hasDoc: boolean; saveStatus: PrdSaveStatus; exec: (cmd: string, value?: string) => void }) {
  const statusLabel = saveStatus === "saving" ? "Saving…" : saveStatus === "unsaved" ? "Unsaved" : "Saved · Draft"
  const statusColor = saveStatus === "saving" ? "var(--accent)" : saveStatus === "unsaved" ? "var(--ink-3)" : "var(--accent)"
  return (
    <div className="prd-toolbar">
      <div className="prd-tools-l">
        <button type="button" className="prd-tool" disabled={!hasDoc} title="Undo" onClick={() => exec("undo")}><IconUndo size={16} /></button>
        <button type="button" className="prd-tool" disabled={!hasDoc} title="Redo" onClick={() => exec("redo")}><IconRedo size={16} /></button>
        <div className="prd-tool-divider" />
        <button type="button" className="prd-tool" disabled={!hasDoc} title="Bold" onClick={() => exec("bold")}><strong>B</strong></button>
        <button type="button" className="prd-tool" disabled={!hasDoc} title="Italic" onClick={() => exec("italic")}><em>I</em></button>
        <button type="button" className="prd-tool" disabled={!hasDoc} title="Underline" onClick={() => exec("underline")}><u>U</u></button>
        <div className="prd-tool-divider" />
        <button type="button" className="prd-tool" disabled={!hasDoc} title="Heading 1" onClick={() => exec("formatBlock", "h1")}>H1</button>
        <button type="button" className="prd-tool" disabled={!hasDoc} title="Heading 2" onClick={() => exec("formatBlock", "h2")}>H2</button>
        <button type="button" className="prd-tool" disabled={!hasDoc} title="Bullet list" onClick={() => exec("insertUnorderedList")}><IconListBullet size={16} /></button>
        <div className="prd-tool-divider" />
        <button type="button" className="prd-tool" disabled={!hasDoc} title="Insert link" style={{ display: "inline-flex", alignItems: "center" }} onClick={() => { const url = prompt("Enter URL"); if (url) exec("createLink", url) }}>
          <IconLinkInsert size={15} /><span style={{ marginLeft: 5 }}>Link</span>
        </button>
        <button type="button" className="prd-tool" disabled={!hasDoc} title="Insert table" style={{ display: "inline-flex", alignItems: "center" }}>
          <IconGrid size={15} /><span style={{ marginLeft: 5 }}>Table</span>
        </button>
      </div>
      <div className="prd-status">
        <span style={{ width: 6, height: 6, borderRadius: "50%", background: hasDoc ? statusColor : "var(--muted)", transition: "background 0.3s" }} />
        {hasDoc ? statusLabel : "No draft"}
      </div>
    </div>
  )
}

/**
 * The markdown PRD editor — a `contenteditable` document region + the
 * execCommand formatting toolbar — as ONE shared, content-agnostic primitive.
 * Extracted from `PrdPanelContent` (AD-P13b: same extraction mold as the HTML
 * editor `PrdHtmlView`) so the main-chat PRD tab and the project artifact
 * drawer edit markdown PRDs through the identical control.
 *
 * Editing model (unchanged from the pre-extraction inline editor): the rendered
 * `children` are edited natively in the contenteditable; on autosave (debounced)
 * or a forced save, the document is FLATTENED to `innerText` and persisted to
 * `payload_md`. A local draft (keyed on prdId) preserves in-progress edits
 * across a remount.
 *
 * `onSave` (AD-P13b — one editor, two consumers): the injected persist HANDLER,
 * called with the flattened text + title. OMITTED (every main-chat caller) →
 * `persist` keeps calling `prdApi.update` byte-for-byte as before; this prop is
 * purely additive. The PROJECT drawer injects a project-scoped, ★ cross-project-
 * IDOR-gated save (`projectsApi.savePrdContent`) so a project edit never writes
 * through the global cross-tenant-only path.
 *
 * `readOnly`: guest / view-only rendering — no toolbar, non-editable body, and
 * `persist` refuses to reach any write path (three independent stops, mirroring
 * PrdHtmlView).
 */
export const PrdMarkdownEditor = forwardRef<PrdMarkdownHandle, {
  prdId: number
  title: string
  /** The document body to render INSIDE the contenteditable region. The
   *  main-chat caller passes its parsed PrdSections tree; the project drawer
   *  passes the PRD's raw markdown source. */
  children: ReactNode
  /** Rendered between the toolbar and the editable body, OUTSIDE the
   *  contenteditable (e.g. the main-chat PRD summary strip). */
  beforeBody?: ReactNode
  onStatus?: (s: PrdSaveStatus) => void
  onSave?: (text: string, title: string) => Promise<void>
  /** Guest / view-only. Defaults false → existing editable behavior. */
  readOnly?: boolean
  /** Distinct-draft namespace for a second consumer (the drawer) so its draft
   *  never collides with the main-chat draft for the same prd_id. Omitted →
   *  the byte-identical main-chat key. */
  draftScope?: string
}>(function PrdMarkdownEditor({ prdId, title, children, beforeBody, onStatus, onSave, readOnly = false, draftScope }, ref) {
  const bodyRef = useRef<HTMLDivElement>(null)
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [saveStatus, setSaveStatus] = useState<PrdSaveStatus>("saved")
  const titleRef = useRef(title)
  titleRef.current = title

  const setStatus = useCallback((s: PrdSaveStatus) => {
    setSaveStatus(s)
    onStatus?.(s)
  }, [onStatus])

  // Recover an in-progress edit (draft innerHTML) over the freshly-rendered
  // children — unchanged from the pre-extraction load-draft effect.
  useEffect(() => {
    if (readOnly || !bodyRef.current) return
    const draft = loadDraft(prdId, draftScope)
    if (draft) bodyRef.current.innerHTML = draft
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prdId])

  const write = useCallback(async (text: string) => {
    // AD-P13b: an injected handler (the drawer's gated save) takes precedence;
    // absent it, the byte-for-byte pre-existing main-chat path runs unchanged.
    if (onSave) await onSave(text, titleRef.current)
    else await prdApi.update(prdId, { title: titleRef.current, payload_md: text })
  }, [onSave, prdId])

  const handleInput = useCallback(() => {
    if (readOnly) return
    setStatus("unsaved")
    if (saveTimer.current) clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(async () => {
      const el = bodyRef.current
      if (!el) return
      setStatus("saving")
      saveDraft(prdId, el.innerHTML, draftScope)
      const text = el.innerText || ""
      try { await write(text); setStatus("saved") }
      catch { setStatus("saved") }
    }, 2000)
  }, [readOnly, setStatus, prdId, draftScope, write])

  const exec = (cmd: string, value?: string) => {
    bodyRef.current?.focus()
    document.execCommand(cmd, false, value)
  }

  // Imperative save (the panel's "Save now"): re-throws on failure so the
  // caller can toast "Save failed" — matches the pre-extraction inline path.
  const persist = useCallback(async () => {
    if (readOnly) return
    const el = bodyRef.current
    if (!el) return
    setStatus("saving")
    saveDraft(prdId, el.innerHTML, draftScope)
    const text = el.innerText || ""
    try { await write(text); setStatus("saved") }
    catch (e) { setStatus("saved"); throw e }
  }, [readOnly, setStatus, prdId, draftScope, write])

  useImperativeHandle(ref, () => ({ save: persist }), [persist])

  useEffect(() => () => {
    if (saveTimer.current) clearTimeout(saveTimer.current)
  }, [])

  return (
    <>
      {!readOnly && <PrdToolbar hasDoc saveStatus={saveStatus} exec={exec} />}
      {beforeBody}
      <div
        className="prd-body"
        contentEditable={!readOnly}
        spellCheck={false}
        suppressContentEditableWarning
        ref={bodyRef}
        onInput={readOnly ? undefined : handleInput}
      >
        {children}
      </div>
    </>
  )
})
