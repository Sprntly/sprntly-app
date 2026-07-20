"use client"

import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react"
import { stripHtmlCodeFence } from "../../lib/htmlBrief"
import { prdApi } from "../../lib/api"
import { applyEvidenceTruncation, stripEvidenceTruncation } from "../../lib/prdEvidenceTruncate"

export type PrdSaveStatus = "saved" | "saving" | "unsaved"

export interface PrdHtmlHandle {
  /** Force an immediate save of the current iframe document. */
  save: () => Promise<void>
}

/** Panel-only presentation overrides injected into the iframe document.
 *  NEVER persisted: readDoc strips this tag before serializing, so the stored
 *  PRD stays byte-clean of viewer styling. Keep in sync with the panel's
 *  `.cpanel-prd-wrap .prd-title` sizing in globals.css. */
const PANEL_STYLE_ID = "sprntly-panel-overrides"
const PANEL_OVERRIDE_CSS = `
  h1 { font-size: 20px !important; line-height: 1.25 !important; }
  body { padding: 0 0 80px !important; }
  .frame { max-width: 990px !important; }
  .page { padding: 25px 25px !important; border-radius: 0 !important; }
`

const HTML_DRAFT_KEY = (prdId: number) => `sprntly_prd_html_draft_${prdId}`
function loadHtmlDraft(prdId: number): string | null {
  try { return localStorage.getItem(HTML_DRAFT_KEY(prdId)) } catch { return null }
}
function saveHtmlDraft(prdId: number, html: string) {
  try { localStorage.setItem(HTML_DRAFT_KEY(prdId), html) } catch { /* ignore */ }
}

/**
 * Renders the v3 PRD artifact — the `prd-author` skill's self-contained,
 * editable HTML page (inline <style> + `contenteditable` document) — inside a
 * SANDBOXED iframe, and persists edits back to the PRD row.
 *
 * Security: `sandbox="allow-same-origin"` WITHOUT `allow-scripts`. The page's
 * inline CSS renders and its `contenteditable` body edits natively (a browser
 * behavior, not JS), but any <script> in the model-generated HTML cannot execute
 * and inline handlers never fire — XSS-safe by construction. allow-same-origin
 * lets the parent read the document to (a) size the iframe to its content and
 * (b) read the edited HTML back to persist it.
 *
 * Editing model: unlike the markdown PRD (which flattened edits to innerText),
 * the HTML page round-trips as HTML — the full edited document is stored in
 * `payload_md`, so the visual system survives an edit. Autosaves on input
 * (debounced) and exposes an imperative `save()` for the panel's "Save now".
 */
export const PrdHtmlView = forwardRef<PrdHtmlHandle, {
  html: string
  prdId: number
  title: string
  onStatus?: (s: PrdSaveStatus) => void
  /** When provided AND the PRD's Evidence list has >3 items, the panel shows
   *  only the top few and injects a "View more evidence" link that calls this
   *  (the panel switches to its Evidence tab). Omitted → no truncation, so the
   *  full list renders (e.g. when the Evidence tab is unavailable). */
  onViewMoreEvidence?: () => void
}>(function PrdHtmlView({ html, prdId, title, onStatus, onViewMoreEvidence }, ref) {
  const frameRef = useRef<HTMLIFrameElement>(null)
  const [height, setHeight] = useState(720)
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const titleRef = useRef(title)
  titleRef.current = title

  // The initial document: a local draft (a prior unsaved edit) wins over the
  // server copy so an in-progress edit survives a remount. Resolved once per
  // prdId and fed to `srcDoc` — never updated on parent re-render, so keystrokes
  // inside the iframe are not clobbered by a reset.
  const initialDoc = useRef<string>("")
  const [docReady, setDocReady] = useState(false)
  useEffect(() => {
    initialDoc.current = loadHtmlDraft(prdId) ?? stripHtmlCodeFence(html)
    setDocReady(true)
    return () => setDocReady(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prdId])

  const readDoc = useCallback((): string | null => {
    const cdoc = frameRef.current?.contentDocument
    if (!cdoc?.documentElement) return null
    // Serialize a CLONE with the panel's injected presentation overrides
    // removed — viewer styling must never leak into the persisted document.
    const root = cdoc.documentElement.cloneNode(true) as HTMLElement
    root.querySelector(`#${PANEL_STYLE_ID}`)?.remove()
    // Strip the viewer-only Evidence-truncation artifacts too, so the stored PRD
    // keeps ALL of its evidence (the top-3 fold is a panel view, never an edit).
    stripEvidenceTruncation(root)
    // Preserve the doctype the srcDoc rendered from — outerHTML drops it.
    return `<!DOCTYPE html>\n${root.outerHTML}`
  }, [])

  const persist = useCallback(async () => {
    const doc = readDoc()
    if (doc == null) return
    onStatus?.("saving")
    saveHtmlDraft(prdId, doc)
    try {
      await prdApi.update(prdId, { title: titleRef.current, payload_md: doc })
      onStatus?.("saved")
    } catch {
      // Local draft is preserved; surface as saved so the UI isn't stuck.
      onStatus?.("saved")
    }
  }, [prdId, onStatus, readDoc])

  useImperativeHandle(ref, () => ({ save: persist }), [persist])

  const resize = useCallback(() => {
    const cdoc = frameRef.current?.contentDocument
    if (!cdoc?.body) return
    const h = Math.max(cdoc.body.scrollHeight, cdoc.documentElement?.scrollHeight ?? 0)
    if (h > 0) setHeight(h)
  }, [])

  // On load, wire an input listener on the (same-origin) iframe document so
  // native contenteditable edits debounce-persist through `persist`.
  const onLoad = useCallback(() => {
    const cdoc = frameRef.current?.contentDocument
    if (!cdoc) return
    // Inject the panel presentation overrides (idempotent), BEFORE the first
    // resize so the measured height reflects the final layout.
    if (!cdoc.getElementById(PANEL_STYLE_ID)) {
      const style = cdoc.createElement("style")
      style.id = PANEL_STYLE_ID
      style.textContent = PANEL_OVERRIDE_CSS
      ;(cdoc.head ?? cdoc.documentElement).appendChild(style)
    }
    // Fold a long Evidence list to its top 3 with a "View more evidence" link
    // (viewer-only — stripped in readDoc). Guarded so a malformed doc can't break
    // the resize/autosave wiring below.
    if (onViewMoreEvidence) {
      try {
        applyEvidenceTruncation(cdoc, onViewMoreEvidence)
      } catch {
        /* non-fatal: fall back to rendering the full evidence list */
      }
    }
    resize()
    const onInput = () => {
      onStatus?.("unsaved")
      resize()
      if (saveTimer.current) clearTimeout(saveTimer.current)
      saveTimer.current = setTimeout(persist, 2000)
    }
    cdoc.addEventListener("input", onInput)
  }, [resize, persist, onStatus, onViewMoreEvidence])

  useEffect(() => () => { if (saveTimer.current) clearTimeout(saveTimer.current) }, [])

  if (!docReady) return null

  return (
    <iframe
      ref={frameRef}
      title={title || "PRD"}
      srcDoc={initialDoc.current}
      onLoad={onLoad}
      sandbox="allow-same-origin"
      style={{
        width: "100%",
        height,
        border: "none",
        borderRadius: 10,
        display: "block",
        colorScheme: "light",
        background: "#fbfaf6",
      }}
    />
  )
})
