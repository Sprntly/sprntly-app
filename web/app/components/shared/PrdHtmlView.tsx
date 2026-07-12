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

export type PrdSaveStatus = "saved" | "saving" | "unsaved"

export interface PrdHtmlHandle {
  /** Force an immediate save of the current iframe document. */
  save: () => Promise<void>
}

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
}>(function PrdHtmlView({ html, prdId, title, onStatus }, ref) {
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
    // Preserve the doctype the srcDoc rendered from — outerHTML drops it.
    return `<!DOCTYPE html>\n${cdoc.documentElement.outerHTML}`
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
    resize()
    const cdoc = frameRef.current?.contentDocument
    if (!cdoc) return
    const onInput = () => {
      onStatus?.("unsaved")
      resize()
      if (saveTimer.current) clearTimeout(saveTimer.current)
      saveTimer.current = setTimeout(persist, 2000)
    }
    cdoc.addEventListener("input", onInput)
  }, [resize, persist, onStatus])

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
