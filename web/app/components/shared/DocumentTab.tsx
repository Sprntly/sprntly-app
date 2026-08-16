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

export function DocumentTab({ documentId }: { documentId: number }) {
  const [doc, setDoc] = useState<CustomArtifactDoc | null>(null)
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)
  const [saveState, setSaveState] = useState<SaveState>({ kind: "idle" })

  const schedulerRef = useRef<Scheduler | null>(null)
  const versionRef = useRef(1)
  const currentHtmlRef = useRef("")
  const bodyDirtyRef = useRef(false)

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
}
