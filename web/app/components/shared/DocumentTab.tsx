"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { ApiError, customArtifactsApi, type CustomArtifactDoc } from "../../lib/api"
import {
  createSaveScheduler,
  SaveConflict,
  type ConflictDoc,
  type SaveState,
  type Scheduler,
} from "../../lib/documentSave"
import { DocumentEditor } from "../../(app)/artifacts/doc/DocumentEditor"
import { documentFailureCopy } from "../../lib/documentFailure"

// ── The chat panel's Document tab ────────────────────────────────────────────
//
// A team document, open beside the conversation that produced it. "Draft a
// leadership update" writes one and opens it HERE, which is where the user
// asked for it and where every other generated artifact in this product lands.
//
// WHY BOTH THIS AND THE /artifacts/doc PAGE. They answer different questions.
// The panel is for the document you are TALKING ABOUT — it sits beside the
// thread, so you can ask a follow-up and watch the document change. The page is
// for the document you are WRITING — full measure, no competing column. The
// library links to the page; chat opens the panel. Neither is a fallback for
// the other, and the same editor renders both, so they cannot drift.
//
// The save layer is shared too (`lib/documentSave.ts`), which is the part that
// matters: this surface has exactly the same lost-update problem as the page,
// and solving it twice would mean solving it differently twice.

const POLL_MS = 2500

/** The longest passage that goes into the composer whole. Beyond this the
 *  middle is elided: the point of quoting is to say WHICH passage, and a
 *  composer holding three screens of someone's own document is unreadable and
 *  crowds out the thing they actually came to type. Both ends are kept so the
 *  quote still reads as the start and end of what was highlighted. */
const QUOTE_MAX_CHARS = 600

export function quoteForComposer(raw: string): string {
  const text = raw.replace(/\s+/g, " ").trim()
  if (text.length <= QUOTE_MAX_CHARS) return text
  const half = Math.floor((QUOTE_MAX_CHARS - 1) / 2)
  return `${text.slice(0, half).trimEnd()} … ${text.slice(-half).trimStart()}`
}

export function DocumentTab({
  documentId,
  onQuote,
}: {
  documentId: number
  /** Hand a highlighted passage to the chat composer. Supplied by ContentPanel,
   *  which is where this component's navigation context lives.
   *
   *  A PROP RATHER THAN `useNavigation()` HERE, and the tests said so: reaching
   *  into the context from this leaf broke eleven cases in two suites that
   *  render it directly, because they treat it as a pure component and give it
   *  no provider. They are right to — this tab renders a document and knows
   *  nothing about routing. Optional, so the surfaces that only READ a document
   *  need not pass it. */
  onQuote?: (excerpt: string) => void
}) {
  const [doc, setDoc] = useState<CustomArtifactDoc | null>(null)
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)
  const [saveState, setSaveState] = useState<SaveState>({ kind: "idle" })

  const schedulerRef = useRef<Scheduler | null>(null)
  const versionRef = useRef(1)
  const currentHtmlRef = useRef("")
  const bodyDirtyRef = useRef(false)

  // ── Highlight a passage, ask about it in the chat beside it ────────────────
  //
  // The requirement, verbatim: "ability to highlight a section and it comes up
  // in the chat text field and ask questions about it or ask for an edit".
  //
  // ONE CLICK, NOT AUTOMATIC. Inserting on every selection would be the
  // literal reading and the wrong behaviour in an editor: selecting text is
  // also how you bold it, move it, or just follow a line while reading, and
  // each of those would shove a quote into the composer the user never asked
  // for. The button appears at the selection and does nothing until pressed.
  //
  // PANEL ONLY, deliberately. This lives in the tab that sits BESIDE a chat;
  // the full-page editor (/artifacts/doc) has no composer to send the passage
  // to, and a button that hands text to a field that is not on screen is worse
  // than no button.
  const bodyRef = useRef<HTMLDivElement | null>(null)
  const [quoteAt, setQuoteAt] = useState<{ top: number; left: number; text: string } | null>(null)

  useEffect(() => {
    // The ref is read INSIDE the handler, not captured when the effect runs.
    // On mount this tab renders "Loading document…" and the body does not
    // exist yet, so an effect that bailed on a null ref would never attach a
    // listener at all — the button would simply never appear.
    const onSelect = () => {
      const container = bodyRef.current
      // No consumer, no button, no work — the page surface passes no handler.
      if (!container || !onQuote) return
      const sel = typeof window !== "undefined" ? window.getSelection() : null
      const text = sel?.toString() ?? ""
      // Anchored to THIS document, WHOLE range. `anchorNode` alone was
      // asymmetric: a drag that STARTED in the document and ended in the chat
      // beside it passed the check, while `toString()` serialized both — so the
      // thread's own text would have been quoted as if it came from the
      // document. `commonAncestorContainer` is inside the container only when
      // BOTH ends are. Most reachable while the document is `generating`, where
      // the body is a plain div and the browser does not confine the selection
      // to an editor host.
      const range = sel && sel.rangeCount > 0 ? sel.getRangeAt(0) : null
      if (!text.trim() || !range ||
          !container.contains(range.commonAncestorContainer)) {
        setQuoteAt(null)
        return
      }
      const rect = range.getBoundingClientRect()
      const base = container.getBoundingClientRect()
      // CLAMPED ON BOTH AXES. Unclamped, a selection on the first line placed
      // the button at a negative offset — over the title row — and a phrase
      // near the right edge pushed a `nowrap` button past the container, which
      // the panel renders as a horizontal scrollbar (`.cpanel-body` sets
      // `overflow-y: auto`, so `overflow-x` computes to auto).
      const CTA_W = 96
      setQuoteAt({
        top: Math.max(0, rect.top - base.top - 34),
        left: Math.min(Math.max(0, rect.left - base.left),
                       Math.max(0, base.width - CTA_W)),
        // RAW text, shaped only when the button is pressed: this runs on every
        // `selectionchange` — per mouse-move while dragging — and collapsing
        // whitespace across an ever-growing string on each one is work nobody
        // asked for.
        text,
      })
    }
    document.addEventListener("selectionchange", onSelect)
    return () => document.removeEventListener("selectionchange", onSelect)
  }, [onQuote])

  const load = useCallback(async () => {
    try {
      // Nothing here resets `loading`, deliberately — a poll must not blank the
      // document it is refreshing. A CHANGED id is handled by the effect below,
      // which tears the whole tab down.
      const fresh = await customArtifactsApi.get(documentId)
      setDoc(fresh)
      versionRef.current = fresh.version
      // The user's in-progress text is NOT clobbered by a reload. Overwriting
      // it here made "Keep mine" send the SERVER's body back — saving theirs
      // and destroying the edits the button exists to preserve, every time.
      // Only seeded when the user has not typed.
      if (!bodyDirtyRef.current) currentHtmlRef.current = fresh.body_html
      setFailed(false)

      // Re-base the scheduler onto the version we just read, but ONLY when
      // nothing is queued — otherwise a poll would silently drop pending text.
      //
      // This is what makes the generating -> ready transition safe: the tab
      // opens while the row is still being written (version 1), and
      // `finish_artifact` bumps it to 2 on completion. Without this, the first
      // keystroke after the document lands saves against version 1, matches no
      // row, and raises "Someone else saved this document while you were
      // editing" on a document nobody else has touched.
      const sched = schedulerRef.current
      if (sched && sched.pendingKeys().length === 0) sched.reset(fresh.version)
    } catch {
      setFailed(true)
    } finally {
      setLoading(false)
    }
  }, [documentId])

  useEffect(() => { void load() }, [load])

  // Poll only while it is being written — a generation opens this tab before
  // its content exists, and a finished document must not keep polling.
  useEffect(() => {
    if (doc?.status !== "generating") return
    const t = setInterval(() => void load(), POLL_MS)
    return () => clearInterval(t)
  }, [doc?.status, load])

  // Built only ONCE THE DOCUMENT HAS LOADED, so it writes against the real
  // version. Creating it on mount captures a placeholder and makes the user's
  // own first keystroke look like someone else's edit — the defect fixed on
  // the page surface, not repeated here.
  // Rebuilt per DOCUMENT. `documentId` is in the deps because a second
  // "draft a …" in the same thread swaps the prop on a mounted tab: without
  // this, the scheduler's save closure targets the NEW id while holding the
  // OLD document's version, and a keystroke writes A's body into B. Both
  // freshly generated documents sit at version 2, so the compare-and-set can
  // match and the overwrite lands.
  useEffect(() => {
    if (doc == null || doc.id !== documentId || schedulerRef.current) return
    const scheduler = createSaveScheduler({
      baseVersion: doc.version,
      onState: setSaveState,
      save: async (payload) => {
        try {
          const saved = await customArtifactsApi.update(documentId, payload)
          versionRef.current = saved.version
          return { version: saved.version }
        } catch (err) {
          const conflict = asConflict(err)
          if (conflict !== undefined) throw new SaveConflict(conflict)
          throw err
        }
      },
    })
    schedulerRef.current = scheduler
    return () => {
      void scheduler.flush()
      scheduler.dispose()
      schedulerRef.current = null
    }
  }, [doc?.id, documentId])

  const onChange = useCallback((html: string) => {
    currentHtmlRef.current = html
    bodyDirtyRef.current = true
    schedulerRef.current?.schedule({ body_html: html })
  }, [])

  const resolveConflict = useCallback(async (keep: "theirs" | "mine") => {
    // CAPTURED BEFORE THE RELOAD. `load()` may seed `currentHtmlRef` from the
    // server, so reading it afterwards is reading THEIR document — which is
    // how "Keep mine" came to save their version over the user's.
    const mine = currentHtmlRef.current
    const hadEdits = bodyDirtyRef.current

    if (keep === "theirs") {
      bodyDirtyRef.current = false
      await load()
      schedulerRef.current?.reset(versionRef.current)
      return
    }
    await load()
    schedulerRef.current?.reset(versionRef.current)
    // Only re-send the body when it was actually edited: sending a ref that was
    // never written is how "keep mine" ends up storing an empty document.
    if (hadEdits) {
      currentHtmlRef.current = mine
      schedulerRef.current?.schedule({ body_html: mine })
      await schedulerRef.current?.flush()
    }
  }, [load])

  if (loading) return <div style={S.muted}>Loading document…</div>
  if (failed || !doc) return <div style={S.muted}>This document could not be loaded.</div>

  return (
    <div data-document-tab style={{ padding: "4px 2px 24px" }}>
      <div style={S.head}>
        <div style={S.title}>{doc.title.trim() || "Untitled document"}</div>
        <SaveIndicator state={saveState} />
      </div>
      {doc.kind.trim() && <div style={S.kind}>{doc.kind.trim()}</div>}

      {doc.status === "generating" && (
        <div data-document-writing style={S.notice}>Writing this document…</div>
      )}
      {doc.status === "failed" && (
        <div data-document-failed data-failure-code={doc.error_code ?? "unknown"} style={S.notice}>
          {documentFailureCopy(doc.error_code)}
        </div>
      )}
      {saveState.kind === "conflict" && (
        <div data-document-conflict style={S.conflict}>
          <div style={{ fontWeight: 600, marginBottom: 6 }}>
            Someone else saved this document while you were editing
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button type="button" data-testid="doc-tab-conflict-theirs"
                    style={S.btn} onClick={() => void resolveConflict("theirs")}>
              Use their version
            </button>
            <button type="button" data-testid="doc-tab-conflict-mine"
                    style={S.btnPrimary} onClick={() => void resolveConflict("mine")}>
              Keep mine
            </button>
          </div>
        </div>
      )}

      <div ref={bodyRef} data-document-body style={{ position: "relative" }}>
      {quoteAt && onQuote && (
        <button
          type="button"
          data-document-quote-cta
          style={{ ...S.quoteCta, top: quoteAt.top, left: quoteAt.left }}
          // MOUSEDOWN, not click: pressing a button clears the selection, so a
          // click handler would fire with nothing left to quote. Preventing
          // the default keeps the highlight visible while the composer fills.
          onMouseDown={(e) => {
            // MOUSEDOWN, not click: pressing a button clears the selection, so
            // a click handler would fire with nothing left to quote.
            // `preventDefault` keeps the highlight — and so this button —
            // alive, which also means the same passage can be quoted AGAIN
            // (clear the composer, press again) rather than having to be
            // re-selected. Clearing the offer here while the highlight stayed
            // put those two out of step.
            e.preventDefault()
            onQuote(quoteForComposer(quoteAt.text))
          }}
        >
          Ask in chat
        </button>
      )}
      {doc.status === "generating" ? (
        // Read-only while it writes: an editable buffer over a document being
        // replaced would have every keystroke overwritten by the next poll.
        <div style={S.body} dangerouslySetInnerHTML={{ __html: doc.body_html }} />
      ) : (
        <>
          {!doc.body_html.trim() && (
            <p data-document-empty style={S.muted}>
              This document is empty. Start typing, or ask in chat for a draft.
            </p>
          )}
          <DocumentEditor
            key={`${doc.id}:${doc.status}:${doc.version}`}
            initialHtml={doc.body_html}
            editable={doc.status === "ready"}
            onChange={onChange}
            onBlur={() => void schedulerRef.current?.flush()}
          />
        </>
      )}
      </div>
    </div>
  )
}

/** Pull a 409's payload out of the api client's error. `undefined` means this
 *  was not a conflict at all, so the caller treats it as retryable. */
function asConflict(err: unknown): ConflictDoc | null | undefined {
  if (!(err instanceof ApiError) || err.status !== 409) return undefined
  const detail = (err.body as { detail?: { error?: string; current?: ConflictDoc } } | null)?.detail
  if (detail?.error !== "version_conflict") return undefined
  return detail.current ?? null
}

function SaveIndicator({ state }: { state: SaveState }) {
  const text =
    state.kind === "saving" ? "Saving…"
      : state.kind === "saved" ? "Saved"
      : state.kind === "error" ? "Not saved — keep typing to retry"
      : state.kind === "conflict" ? "Someone else edited this"
      : ""
  if (!text) return null
  return (
    <span data-document-save-state={state.kind} style={{
      fontSize: 11.5,
      color: state.kind === "error" || state.kind === "conflict"
        ? "var(--danger, #DC2626)" : "var(--ink-3, #8C8A84)",
    }}>{text}</span>
  )
}

const S: Record<string, React.CSSProperties> = {
  head: { display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 12 },
  title: { fontSize: 17, fontWeight: 700, color: "var(--ink, #1A1A17)" },
  kind: {
    fontSize: 11, textTransform: "uppercase", letterSpacing: "0.05em",
    color: "var(--ink-3, #8C8A84)", marginBottom: 12,
  },
  notice: {
    fontSize: 12.5, color: "var(--ink-2, #5A5853)",
    background: "var(--surface-2, #F4F1EA)", border: "1px solid var(--line, #E8E6E0)",
    borderRadius: 8, padding: "8px 12px", margin: "8px 0 12px",
  },
  conflict: {
    fontSize: 12.5, background: "var(--danger-bg, #FEF2F2)",
    border: "1px solid var(--danger-line, #FCA5A5)",
    borderRadius: 8, padding: "10px 12px", margin: "8px 0 12px",
  },
  btn: {
    fontSize: 12, fontWeight: 600, padding: "5px 10px", borderRadius: 6,
    border: "1px solid var(--line, #E8E6E0)", background: "var(--surface, #fff)", cursor: "pointer",
  },
  btnPrimary: {
    fontSize: 12, fontWeight: 600, padding: "5px 10px", borderRadius: 6,
    border: "none", background: "var(--accent, #179463)", color: "#fff", cursor: "pointer",
  },
  body: { fontSize: 14, lineHeight: 1.7, color: "var(--ink, #1A1A17)" },
  muted: { fontSize: 13, color: "var(--ink-3, #8C8A84)" },
  quoteCta: {
    position: "absolute", zIndex: 20,
    fontSize: 12, fontWeight: 600, padding: "5px 10px", borderRadius: 6,
    border: "none", background: "var(--ink, #1A1A17)", color: "#fff",
    cursor: "pointer", boxShadow: "0 2px 8px rgba(0,0,0,0.18)",
    whiteSpace: "nowrap",
  },
}
