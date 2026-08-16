"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { ApiError, customArtifactsApi, type CustomArtifactDoc } from "../../../lib/api"
import {
  createSaveScheduler,
  SaveConflict,
  type ConflictDoc,
  type SaveState,
  type Scheduler,
} from "../../../lib/documentSave"
import { AppLayout } from "../../../components/screens/app/AppLayout"
import { documentFailureCopy } from "../../../lib/documentFailure"
import { DocumentEditor } from "./DocumentEditor"

// ── The team-document surface ────────────────────────────────────────────────
//
// One custom artifact, open and editable — the "Others" library's document
// page. Reading was slice 3; this is the editing half.
//
// A DOCUMENT NEEDS A PAGE, not a rail. The other artifacts open into the chat's
// right-hand panel because they are read ALONGSIDE a conversation. A leadership
// update is WRITTEN, and writing wants the page: full measure, no competing
// column, an 800px reading width because this is the one surface in the app
// that is pure prose.
//
// IT KEEPS THE APP'S CHROME. The first cut of this page rendered bare, with no
// left nav — which looked like a focused writing surface and behaved like a
// dead end: every other authed screen has the nav, and losing it meant the only
// way out was one small back-link. Wrapped in AppLayout like its neighbours.
//
// STATUS IS A REAL STATE. A document created by a chat generation exists before
// its content does, so this renders four things: a document, a document being
// written, a generation that died, and a document someone else just changed
// under you. Polling stops the moment it leaves `generating`.

const POLL_MS = 2500

export function DocumentRoute() {
  const params = useSearchParams()
  const router = useRouter()
  const rawId = params.get("id")
  const docId = rawId && /^\d+$/.test(rawId) ? Number(rawId) : null

  const [doc, setDoc] = useState<CustomArtifactDoc | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadFailed, setLoadFailed] = useState(false)
  const [saveState, setSaveState] = useState<SaveState>({ kind: "idle" })
  const [title, setTitle] = useState("")

  const schedulerRef = useRef<Scheduler | null>(null)
  // The version the scheduler is writing against, kept in a ref because the
  // save callback closes over it and must not be rebuilt on every keystroke.
  const versionRef = useRef(1)

  const load = useCallback(async () => {
    if (docId == null) {
      setLoading(false)
      return
    }
    try {
      const fresh = await customArtifactsApi.get(docId)
      setDoc(fresh)
      setTitle(fresh.title)
      versionRef.current = fresh.version
      setLoadFailed(false)
    } catch {
      setLoadFailed(true)
    } finally {
      setLoading(false)
    }
  }, [docId])

  useEffect(() => { void load() }, [load])

  // Poll ONLY while the document is being written, so a ready document open in
  // a tab is not a background request loop.
  useEffect(() => {
    if (doc?.status !== "generating") return
    const t = setInterval(() => void load(), POLL_MS)
    return () => clearInterval(t)
  }, [doc?.status, load])

  // One scheduler per document, built ONLY ONCE THE DOCUMENT HAS LOADED.
  //
  // Keying this on `docId` alone was a real bug: the effect commits
  // synchronously on mount while `load()` is still awaiting the GET, so
  // `baseVersion` captured the initial `useRef(1)` and never moved. Every
  // generated document is already at version 2 (`finish_artifact` bumps it),
  // so the FIRST keystroke saved against version 1, matched no row, and raised
  // "Someone else saved this document while you were editing" — in a document
  // nobody else had touched. The conflict machinery firing on a user's own
  // first edit is worse than having none.
  useEffect(() => {
    if (docId == null || doc == null) return
    if (schedulerRef.current) return
    const scheduler = createSaveScheduler({
      // The LOADED version, not a placeholder.
      baseVersion: doc.version,
      onState: setSaveState,
      save: async (payload) => {
        try {
          const saved = await customArtifactsApi.update(docId, payload)
          versionRef.current = saved.version
          return { version: saved.version }
        } catch (err) {
          // A 409 is not a failure to retry — retrying would overwrite the
          // colleague the server's check just protected. Translated into the
          // scheduler's terminal conflict state, carrying THEIR document.
          const conflict = asConflict(err)
          if (conflict !== undefined) throw new SaveConflict(conflict)
          throw err
        }
      },
    })
    schedulerRef.current = scheduler
    return () => {
      // Send whatever is pending before tearing down, so navigating away
      // mid-sentence does not lose it.
      void scheduler.flush()
      scheduler.dispose()
      schedulerRef.current = null
    }
    // `doc?.id` rather than `doc`: the poll replaces the object while a
    // generation runs, and rebuilding the scheduler on each poll would drop a
    // debounce mid-sentence.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docId, doc?.id])

  // A tab close cannot await, so this is best-effort by nature: it flushes the
  // debounce rather than guaranteeing delivery. The debounce is short enough
  // that the exposure is about a second of typing.
  useEffect(() => {
    const onHide = () => { void schedulerRef.current?.flush() }
    window.addEventListener("visibilitychange", onHide)
    window.addEventListener("pagehide", onHide)
    return () => {
      window.removeEventListener("visibilitychange", onHide)
      window.removeEventListener("pagehide", onHide)
    }
  }, [])

  const editable = doc?.status === "ready"

  const onBodyChange = useCallback((html: string) => {
    schedulerRef.current?.schedule({ body_html: html })
  }, [])

  const onTitleChange = useCallback((next: string) => {
    setTitle(next)
    schedulerRef.current?.schedule({ title: next })
  }, [])

  const resolveConflict = useCallback(async (keep: "theirs" | "mine") => {
    const theirs = saveState.kind === "conflict" ? saveState.theirs : null
    if (keep === "theirs") {
      // Adopt their document wholesale. The editor remounts on the new
      // `contentKey`, so the user sees what landed. Re-based on what the
      // reload actually returned, NOT on the version in the 409 — see the
      // matching note below.
      await load()
      bodyDirtyRef.current = false
      schedulerRef.current?.reset(versionRef.current)
      return
    }
    // Keep mine: re-base onto their version and save over it. This is an
    // explicit, informed overwrite — the user has been shown that someone else
    // saved and has chosen — which is the only kind this product should do.
    // Re-base onto the version we JUST LOADED rather than the one carried in
    // the 409: a third save landing between the refusal and now would make
    // `theirs.version` already stale, and the next keystroke would conflict
    // again immediately.
    await load()
    const base = versionRef.current
    schedulerRef.current?.reset(base)
    // `body_html` is sent ONLY if the user actually edited the body. Sending
    // the ref unconditionally is what made a title-only edit destructive.
    schedulerRef.current?.schedule(
      bodyDirtyRef.current ? { title, body_html: currentHtmlRef.current } : { title },
    )
    await schedulerRef.current?.flush()
  }, [saveState, load, title])

  // The editor's latest serialization, kept so "keep mine" can re-send it
  // after a conflict without reaching into the editor instance.
  //
  // SEEDED FROM THE LOADED DOCUMENT, and `bodyDirty` tracks whether the user
  // actually typed. Both matter: this ref used to start as "" and was written
  // only by the editor's onChange, so a user who renamed the title and never
  // touched the body — then hit a conflict — sent `body_html: ""` and DELETED
  // THE ENTIRE DOCUMENT with the button whose whole purpose is to preserve
  // their work.
  const currentHtmlRef = useRef("")
  const bodyDirtyRef = useRef(false)
  useEffect(() => {
    if (doc) currentHtmlRef.current = doc.body_html
  }, [doc?.id, doc?.version])
  const handleChange = useCallback((html: string) => {
    currentHtmlRef.current = html
    bodyDirtyRef.current = true
    onBodyChange(html)
  }, [onBodyChange])

  // Remounts the editor when the DOCUMENT changes identity (a reload, a
  // conflict resolved by taking theirs) but never on a keystroke — a remount
  // mid-typing would move the caret to the start.
  const contentKey = useMemo(
    () => `${doc?.id ?? "none"}:${doc?.status}:${doc?.version ?? 0}`,
    [doc?.id, doc?.status, doc?.version],
  )

  if (loading) {
    return (
      <AppLayout>
        <div style={S.page}><div data-doc-skeleton style={{ ...S.sheet, ...S.skeleton }} /></div>
      </AppLayout>
    )
  }

  if (docId == null || (loadFailed && !doc)) {
    return (
      <AppLayout>
        <div style={S.page}>
          <div style={S.sheet}>
            <p style={S.muted}>
              {docId == null
                ? "No document was named in this link."
                : "This document could not be loaded."}
            </p>
            <button type="button" style={S.link} onClick={() => router.push("/artifacts")}>
              Back to Artifacts
            </button>
          </div>
        </div>
      </AppLayout>
    )
  }

  if (!doc) return null

  return (
    <AppLayout>
      <div style={S.page}>
        <div style={S.sheet}>
          <div style={S.topRow}>
            <button type="button" data-doc-back style={S.back} onClick={() => router.push("/artifacts")}>
              ← Artifacts
            </button>
            <SaveIndicator state={saveState} />
          </div>

          <input
            data-doc-title
            aria-label="Document title"
            value={title}
            disabled={!editable}
            placeholder="Untitled document"
            onChange={(e) => onTitleChange(e.target.value)}
            onBlur={() => void schedulerRef.current?.flush()}
            style={S.titleInput}
          />
          {doc.kind.trim() && <div style={S.kind}>{doc.kind.trim()}</div>}

          {doc.status === "generating" && (
            <div data-doc-writing style={S.notice}>Writing this document…</div>
          )}
          {doc.status === "failed" && (
            <div data-doc-failed data-failure-code={doc.error_code ?? "unknown"} style={S.notice}>
              {documentFailureCopy(doc.error_code)}
            </div>
          )}

          {saveState.kind === "conflict" && (
            <ConflictBanner theirs={saveState.theirs} onResolve={resolveConflict} />
          )}

          {doc.status === "ready" && !doc.body_html.trim() && (
            // Slice 3 had this and slice 4 dropped it, leaving a ready-but-
            // empty document rendering as a completely blank page — which
            // reads as broken rather than as new. A cue, not a placeholder
            // attribute: see the note in DocumentEditor's stylesheet.
            <p data-doc-empty style={S.muted}>
              This document is empty. Start typing, or ask in chat for a draft.
            </p>
          )}

          {doc.status === "generating" ? (
            // Read-only while it writes: an editable buffer over a document
            // being replaced would have every keystroke overwritten by the
            // next poll.
            <div data-doc-body style={S.body} dangerouslySetInnerHTML={{ __html: doc.body_html }} />
          ) : (
            <DocumentEditor
              key={contentKey}
              initialHtml={doc.body_html}
              editable={!!editable}
              onChange={handleChange}
              onBlur={() => void schedulerRef.current?.flush()}
            />
          )}
        </div>
      </div>
    </AppLayout>
  )
}

/** Pull a 409's payload out of whatever shape the api client threw.
 *  Returns `undefined` when this was not a conflict at all. */
function asConflict(err: unknown): ConflictDoc | null | undefined {
  if (!(err instanceof ApiError) || err.status !== 409) return undefined
  const detail = (err.body as { detail?: { error?: string; current?: ConflictDoc } } | null)?.detail
  // The `error` discriminator is checked, not assumed: a 409 from some future
  // endpoint that is NOT a version conflict must fall through to the generic
  // retryable path rather than being rendered as "someone else edited this".
  if (detail?.error !== "version_conflict") return undefined
  return detail.current ?? null
}

function SaveIndicator({ state }: { state: SaveState }) {
  const text =
    state.kind === "saving" ? "Saving…"
      : state.kind === "saved" ? "Saved"
      // NOT "will retry": nothing schedules one. The scheduler retries on the
      // next change or flush, so a user who stops typing and leaves the tab
      // open would be told a save was coming that never came. The wording
      // names the action that actually works.
      : state.kind === "error" ? "Not saved — keep typing to retry"
      : state.kind === "conflict" ? "Someone else edited this"
      : ""
  if (!text) return null
  return (
    <span
      data-doc-save-state={state.kind}
      style={{
        fontSize: 12,
        color: state.kind === "error" || state.kind === "conflict"
          ? "var(--danger, #DC2626)" : "var(--ink-3, #8C8A84)",
      }}
    >
      {text}
    </span>
  )
}

/** A conflict is a DECISION, not an error.
 *
 *  The server refused a save because a colleague saved first. Neither version
 *  is automatically right, so the user is shown that it happened and given the
 *  two honest options. Nothing is discarded until they choose: their text is
 *  still in the editor, and the colleague's is still on the server. */
function ConflictBanner({
  theirs, onResolve,
}: {
  theirs: ConflictDoc | null
  onResolve: (keep: "theirs" | "mine") => void
}) {
  return (
    <div data-doc-conflict style={S.conflict}>
      <div style={{ fontWeight: 600, marginBottom: 4 }}>
        Someone else saved this document while you were editing
      </div>
      <div style={{ marginBottom: 10 }}>
        Your changes have not been saved yet. Take their version, or keep yours
        and save over it.
      </div>
      <div style={{ display: "flex", gap: 8 }}>
        <button type="button" data-testid="conflict-theirs" style={S.conflictBtn}
                onClick={() => onResolve("theirs")}>
          Use their version
        </button>
        <button type="button" data-testid="conflict-mine" style={S.conflictBtnPrimary}
                onClick={() => onResolve("mine")}>
          Keep mine
        </button>
      </div>
      {theirs?.updated_by && (
        <div style={{ marginTop: 8, fontSize: 11.5, opacity: 0.8 }}>
          Last saved by another member.
        </div>
      )}
    </div>
  )
}

/** Path to one team document. Query param, not a segment — this app is a
 *  static export (see page.tsx). Exported so callers never hand-build it. */
export function documentPath(id: number): string {
  return `/artifacts/doc?id=${encodeURIComponent(String(id))}`
}

const S: Record<string, React.CSSProperties> = {
  page: {
    minHeight: "100%", padding: "24px 24px 96px",
    background: "var(--surface-2, #F4F1EA)",
    display: "flex", justifyContent: "center",
  },
  sheet: {
    width: "100%", maxWidth: 800,
    background: "var(--surface, #fff)",
    border: "1px solid var(--line, #E8E6E0)",
    borderRadius: 12, padding: "24px 56px 64px",
  },
  skeleton: { height: 420, opacity: 0.5 },
  topRow: {
    display: "flex", alignItems: "center", justifyContent: "space-between",
    marginBottom: 16,
  },
  back: {
    border: "none", background: "none", padding: 0, cursor: "pointer",
    fontSize: 12.5, color: "var(--ink-3, #8C8A84)",
  },
  titleInput: {
    width: "100%", border: "none", outline: "none", background: "transparent",
    fontSize: 30, fontWeight: 700, lineHeight: 1.2,
    color: "var(--ink, #1A1A17)", padding: 0, margin: "0 0 6px",
  },
  kind: {
    fontSize: 12, textTransform: "uppercase", letterSpacing: "0.05em",
    color: "var(--ink-3, #8C8A84)", marginBottom: 20,
  },
  notice: {
    fontSize: 13, color: "var(--ink-2, #5A5853)",
    background: "var(--surface-2, #F4F1EA)",
    border: "1px solid var(--line, #E8E6E0)",
    borderRadius: 8, padding: "10px 14px", marginBottom: 20,
  },
  conflict: {
    fontSize: 13, color: "var(--ink, #1A1A17)",
    background: "var(--danger-bg, #FEF2F2)",
    border: "1px solid var(--danger-line, #FCA5A5)",
    borderRadius: 8, padding: "12px 14px", marginBottom: 20,
  },
  conflictBtn: {
    fontSize: 12.5, fontWeight: 600, padding: "6px 12px", borderRadius: 6,
    border: "1px solid var(--line, #E8E6E0)", background: "var(--surface, #fff)",
    cursor: "pointer",
  },
  conflictBtnPrimary: {
    fontSize: 12.5, fontWeight: 600, padding: "6px 12px", borderRadius: 6,
    border: "none", background: "var(--accent, #179463)", color: "#fff",
    cursor: "pointer",
  },
  body: { fontSize: 15, lineHeight: 1.7, color: "var(--ink, #1A1A17)" },
  muted: { fontSize: 13.5, color: "var(--ink-3, #8C8A84)" },
  link: {
    border: "none", background: "none", padding: 0, cursor: "pointer",
    fontSize: 13.5, color: "var(--accent, #179463)", fontWeight: 600,
  },
}
