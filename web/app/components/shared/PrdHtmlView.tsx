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
  /**
   * Run a formatting command against the document INSIDE the iframe.
   *
   * The toolbar lives in the panel, the text lives in a child browsing
   * context, and `execCommand` only ever acts on the document that owns the
   * current selection — so it has to be invoked on `contentDocument`, not on
   * the parent. Same-origin (`allow-same-origin`) is what makes that legal;
   * the iframe still runs no scripts of its own.
   *
   * Returns false when there is nothing to act on yet (document not ready, or
   * read-only), so the caller can stay silent rather than pretend it worked.
   */
  exec: (command: string, value?: string) => boolean
}

/** Panel-only presentation overrides injected into the iframe document.
 *  NEVER persisted: readDoc strips this tag before serializing, so the stored
 *  PRD stays byte-clean of viewer styling. Keep in sync with the panel's
 *  `.cpanel-prd-wrap .prd-title` sizing in globals.css. */
/** Debounce between an edit and the autosave it triggers. Named because two
 *  paths now share it — native typing and the toolbar. */
const AUTOSAVE_MS = 2000

const PANEL_STYLE_ID = "sprntly-panel-overrides"
const PANEL_OVERRIDE_CSS = `
  h1 { font-size: 20px !important; line-height: 1.25 !important; }
  body { padding: 0 0 80px !important; }
  .frame { max-width: 990px !important; }
  .page { padding: 25px 25px !important; border-radius: 0 !important; }
  /* The iframe is sized to its content and the PANEL scrolls it, so this
     document must never grow a scrollbar of its own — one lagging measurement
     (a font swapping in, an image landing) was enough to put a second bar
     right beside the panel's. The ResizeObserver below keeps the height
     honest; this makes sure a transient mismatch is invisible rather than a
     stray rail. */
  html, body { scrollbar-width: none !important; }
  html::-webkit-scrollbar, body::-webkit-scrollbar { width: 0 !important; height: 0 !important; display: none !important; }
`

// Ceiling on how many times one load (or one edit) may re-size the frame to
// its content — see `resizeBudget`. Generous for real reflow, finite against a
// document that feeds its own height back.
const RESIZE_BUDGET = 24

const HTML_DRAFT_KEY = (prdId: number) => `sprntly_prd_html_draft_${prdId}`

/** A draft is an edit that has NOT reached the server yet, so it records the
 *  server document it was based on (`base`) alongside the edited text (`doc`).
 *  Without `base` a draft is indistinguishable from a stale shadow copy: it
 *  wins over the server forever, so a PRD another user has since edited keeps
 *  rendering the local copy — and the next keystroke autosaves that stale doc
 *  back over their saved work. Comparing against `base` scopes the draft to
 *  exactly what it is for: recovering unsaved work on a document nobody else
 *  has moved on. */
type HtmlDraft = { base: string; doc: string }

function loadHtmlDraft(prdId: number): HtmlDraft | null {
  try {
    const raw = localStorage.getItem(HTML_DRAFT_KEY(prdId))
    if (!raw) return null
    const parsed: unknown = JSON.parse(raw)
    if (
      typeof parsed === "object" && parsed !== null &&
      typeof (parsed as HtmlDraft).base === "string" &&
      typeof (parsed as HtmlDraft).doc === "string"
    ) return parsed as HtmlDraft
    // Legacy drafts (a bare HTML string) carry no base, so there is no way to
    // tell a genuine unsaved edit from the stale shadow copy described above.
    // Drop them — the server copy is the one that is definitely current.
    return null
  } catch { return null }
}

function saveHtmlDraft(prdId: number, draft: HtmlDraft) {
  try { localStorage.setItem(HTML_DRAFT_KEY(prdId), JSON.stringify(draft)) } catch { /* ignore */ }
}

function clearHtmlDraft(prdId: number) {
  try { localStorage.removeItem(HTML_DRAFT_KEY(prdId)) } catch { /* ignore */ }
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
 *
 * `readOnly`: the model-generated document carries its own native
 * `contenteditable` markup (part of the page's HTML, not something this
 * component adds) — a guest-mode caller can't rely on an outer prop to make
 * arbitrary generated HTML non-editable, so when `readOnly` is set this
 * component (a) force-disables every `[contenteditable]` element it finds
 * once the iframe loads, (b) never wires the debounced input→persist
 * listener, and (c) makes `persist` itself (and therefore the imperative
 * `save()`) refuse to call `prdApi.update` at all — three independent stops
 * on the same write path, not just a UI omission.
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
  /** Guest-mode / read-only rendering — see the doc comment above. Defaults
   *  to false (existing editable behavior), so every non-guest caller is
   *  byte-for-byte unchanged. */
  readOnly?: boolean
  /** AD-P13b (one editor, two consumers): the save HANDLER the editor calls
   *  with the full serialized `<!DOCTYPE html>…` document + title. When
   *  OMITTED (every main-chat caller) `persist` keeps calling `prdApi.update`
   *  byte-for-byte as before — this prop is purely additive. The PROJECT
   *  drawer injects a project-scoped, ★ cross-project-IDOR-gated save here
   *  (`projectsApi.savePrdContent`) so a project edit never writes through the
   *  global cross-tenant-only path. */
  onSave?: (fullHtml: string, title: string) => Promise<void>
}>(function PrdHtmlView({ html, prdId, title, onStatus, onViewMoreEvidence, readOnly = false, onSave }, ref) {
  const frameRef = useRef<HTMLIFrameElement>(null)
  const [height, setHeight] = useState(720)
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  // Watches the iframe document so the frame keeps matching its content (see
  // onLoad). Held in a ref so a re-load replaces it and unmount disconnects it.
  const observerRef = useRef<ResizeObserver | null>(null)
  const titleRef = useRef(title)
  titleRef.current = title

  // The initial document: a local draft (a prior UNSAVED edit) wins over the
  // server copy so an in-progress edit survives a remount — but only while the
  // server still holds the document that draft was based on. Once anyone else
  // has saved, the server copy is newer and wins, otherwise a collaborator's
  // edits stay invisible here and get overwritten by the next autosave.
  // Resolved once per prdId and fed to `srcDoc` — never updated on parent
  // re-render, so keystrokes inside the iframe are not clobbered by a reset.
  const initialDoc = useRef<string>("")
  // The server document this editing session started from — the `base` stamped
  // onto any draft written below.
  const baseDoc = useRef<string>("")
  const [docReady, setDocReady] = useState(false)
  useEffect(() => {
    const server = stripHtmlCodeFence(html)
    const draft = loadHtmlDraft(prdId)
    if (draft && draft.base !== server) {
      // Someone else saved since this draft was taken — drop it rather than
      // shadow (and later clobber) their work.
      clearHtmlDraft(prdId)
    }
    baseDoc.current = server
    initialDoc.current = draft && draft.base === server ? draft.doc : server
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
    // The real backstop: even if something upstream still calls save() (the
    // imperative handle, a stray input listener), this refuses to reach
    // prdApi.update for a guest — never just a UI-level omission.
    if (readOnly) return
    const doc = readDoc()
    if (doc == null) return
    onStatus?.("saving")
    // Written BEFORE the request so a crash or closed tab mid-flight still
    // recovers the edit; cleared again the moment the server has it.
    saveHtmlDraft(prdId, { base: baseDoc.current, doc })
    try {
      // AD-P13b: an injected save handler (the project drawer's gated save)
      // takes precedence; absent it, the byte-for-byte pre-existing main-chat
      // path (`prdApi.update`) runs unchanged.
      if (onSave) {
        await onSave(doc, titleRef.current)
      } else {
        await prdApi.update(prdId, { title: titleRef.current, payload_md: doc })
      }
      // Saved — this is no longer an unsaved edit, so the draft must go. Left
      // behind, it outranks the server copy on every later open, which is how a
      // collaborator's saved edits became invisible to whoever edited last.
      clearHtmlDraft(prdId)
      // The server now holds `doc`; subsequent drafts in this session are based
      // on it, not on the document we originally loaded.
      baseDoc.current = doc
      onStatus?.("saved")
    } catch {
      // SAY SO. This used to report "saved" on a failed request, on the
      // reasoning that the UI shouldn't look stuck — but the status line is the
      // only thing telling anyone whether their work reached the server, and a
      // save that 4xx'd or timed out then read as done. The edit was still in
      // the local draft and would come back on the next open, but nobody knew
      // to wait for that: it looked saved, the tab got closed or refreshed, and
      // the work looked lost.
      //
      // "Unsaved" is the truth, the draft is still on disk (deliberately NOT
      // cleared above), and the next edit re-arms the debounce so it retries.
      onStatus?.("unsaved")
    }
  }, [prdId, onStatus, readDoc, readOnly, onSave])

  /** Shared by the toolbar and the native `input` listener: an edit happened,
   *  so mark it unsaved and re-arm the debounce. Declared here so both paths
   *  land on ONE definition of "the document changed". */
  const markEdited = useCallback(() => {
    onStatus?.("unsaved")
    if (saveTimer.current) clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(persist, AUTOSAVE_MS)
  }, [onStatus, persist])

  const exec = useCallback((command: string, value?: string): boolean => {
    if (readOnly) return false
    const cdoc = frameRef.current?.contentDocument
    const target = cdoc?.querySelector<HTMLElement>("[contenteditable='true']")
    if (!cdoc || !target) return false
    // The command applies to whatever is selected INSIDE the iframe, so the
    // iframe has to hold focus first — clicking a toolbar button in the parent
    // moves focus out of it, and an unfocused document has no selection for
    // `execCommand` to act on. Focusing restores the caret the browser kept.
    try {
      frameRef.current?.contentWindow?.focus()
      target.focus()
      const ok = cdoc.execCommand(command, false, value)
      if (ok) markEdited()
      return ok
    } catch {
      // Deprecated API on an engine that has dropped it, or a command this
      // document can't take — the panel simply reports nothing happened.
      return false
    }
  }, [readOnly, markEdited])

  useImperativeHandle(ref, () => ({ save: persist, exec }), [persist, exec])

  // How many more observer-driven height changes to accept. Normally a reflow
  // converges in one or two (scrollHeight is max(content, viewport), so once
  // the frame matches the content the next measurement is identical and React
  // bails out). A document whose own CSS is viewport-relative — `min-height:
  // 100vh` on a page wrapper, say — would instead grow by our injected padding
  // on every pass, so the budget stops that at a bounded number of frames
  // instead of running away. Refilled on load and on a user edit.
  const resizeBudget = useRef(RESIZE_BUDGET)

  const resize = useCallback(() => {
    const cdoc = frameRef.current?.contentDocument
    if (!cdoc?.body) return
    const h = Math.max(cdoc.body.scrollHeight, cdoc.documentElement?.scrollHeight ?? 0)
    if (h <= 0) return
    setHeight((prev) => {
      if (Math.abs(h - prev) <= 1) return prev
      if (resizeBudget.current <= 0) return prev
      resizeBudget.current -= 1
      return h
    })
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
    // Guest mode: the document's own `contenteditable` markup is part of the
    // model-generated HTML, not something this component set — force every
    // such element to non-editable rather than trusting the source content to
    // already be safe. Also make designMode explicit (belt-and-suspenders;
    // designMode defaults to "off" but a same-origin doc can flip it).
    if (readOnly) {
      cdoc.querySelectorAll("[contenteditable]").forEach((el) => {
        el.setAttribute("contenteditable", "false")
      })
      try {
        cdoc.designMode = "off"
      } catch {
        /* not fatal — the per-element attribute above is the real guard */
      }
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
    resizeBudget.current = RESIZE_BUDGET
    resize()
    // Keep tracking the document's height after load. A single measurement on
    // load is taken before web fonts swap in and before any image resolves, so
    // it under-reports and the document overflows the iframe — which is what
    // put a scrollbar INSIDE the frame next to the panel's own. Re-measuring on
    // every reflow converges instead: scrollHeight is max(content, viewport),
    // so once the iframe matches the content the observer settles.
    observerRef.current?.disconnect()
    if (typeof ResizeObserver !== "undefined" && cdoc.body) {
      const ro = new ResizeObserver(() => resize())
      ro.observe(cdoc.body)
      if (cdoc.documentElement) ro.observe(cdoc.documentElement)
      observerRef.current = ro
    }
    // Fonts land after load and shift line counts; ResizeObserver catches most
    // of it, but this covers engines that don't reflow the observed box.
    cdoc.fonts?.ready.then(() => resize()).catch(() => { /* best effort */ })
    // Guest mode: never wire the autosave listener at all — the elements are
    // already non-editable above, so there's nothing for it to react to, and
    // this keeps the read-only path from scheduling any persist() call.
    if (readOnly) return
    const onInput = () => {
      // Typing genuinely changes the document's height — refill the budget so a
      // long editing session keeps tracking it.
      resizeBudget.current = RESIZE_BUDGET
      resize()
      markEdited()
    }
    cdoc.addEventListener("input", onInput)
  }, [resize, markEdited, onViewMoreEvidence, readOnly])

  useEffect(() => () => {
    if (saveTimer.current) clearTimeout(saveTimer.current)
    observerRef.current?.disconnect()
  }, [])

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
