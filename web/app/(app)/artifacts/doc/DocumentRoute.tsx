"use client"

import { useCallback, useEffect, useState } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { customArtifactsApi, type CustomArtifactDoc } from "../../../lib/api"

// ── The team-document surface ────────────────────────────────────────────────
//
// One custom artifact, open. THIS SLICE RENDERS IT; the next one makes it
// editable, in place, on this same component — the toolbar and autosave attach
// to the body below rather than replacing it.
//
// A DOCUMENT NEEDS A PAGE, not a rail. The other artifacts in this library open
// into the chat's right-hand panel because they are read ALONGSIDE a
// conversation — you ask about a PRD while looking at it. A leadership update
// is written, and writing wants the page: full measure, no competing column.
// That is also why the reading width below is capped at 800px rather than
// filling the viewport — a line of prose past ~90 characters is measurably
// harder to read, and this is the one surface in the app that is pure prose.
//
// STATUS IS A REAL STATE HERE. A document created by a chat generation exists
// before its content does, so this component has to render three things: a
// document, a document being written, and a generation that died. Polling stops
// the moment it leaves `generating`, so a ready document costs nothing.

/** How often to re-check a document that is still being written. Slow enough
 *  that a long generation is not a request storm, fast enough that the page
 *  does not feel stuck — the same order as the PRD panel's poll. */
const POLL_MS = 2500

export function DocumentRoute() {
  const params = useSearchParams()
  const router = useRouter()
  const rawId = params.get("id")
  const docId = rawId && /^\d+$/.test(rawId) ? Number(rawId) : null

  const [doc, setDoc] = useState<CustomArtifactDoc | null>(null)
  const [loading, setLoading] = useState(true)
  // Distinguished from "no document": a failed LOAD is recoverable by retrying,
  // a missing id is not, and telling the user the wrong one wastes their time.
  const [loadFailed, setLoadFailed] = useState(false)

  const load = useCallback(async () => {
    if (docId == null) {
      setLoading(false)
      return
    }
    try {
      setDoc(await customArtifactsApi.get(docId))
      setLoadFailed(false)
    } catch {
      setLoadFailed(true)
    } finally {
      setLoading(false)
    }
  }, [docId])

  useEffect(() => {
    void load()
  }, [load])

  // Poll ONLY while the document is being written. A ready document never
  // re-fetches, so an open tab is not a background request loop.
  useEffect(() => {
    if (doc?.status !== "generating") return
    const t = setInterval(() => void load(), POLL_MS)
    return () => clearInterval(t)
  }, [doc?.status, load])

  if (loading) {
    return (
      <div style={S.page}>
        <div data-doc-skeleton style={{ ...S.sheet, ...S.skeleton }} />
      </div>
    )
  }

  if (docId == null || (loadFailed && !doc)) {
    return (
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
    )
  }

  if (!doc) return null

  return (
    <div style={S.page}>
      <div style={S.sheet}>
        <button
          type="button"
          data-doc-back
          style={S.back}
          onClick={() => router.push("/artifacts")}
        >
          ← Artifacts
        </button>

        <h1 data-doc-title style={S.title}>
          {doc.title.trim() || "Untitled document"}
        </h1>
        {/* The document's own kind, in the user's words. Free text, so it is
            rendered as itself — never matched against a list. */}
        {doc.kind.trim() && <div style={S.kind}>{doc.kind.trim()}</div>}

        {doc.status === "generating" && (
          <div data-doc-writing style={S.notice}>
            Writing this document…
          </div>
        )}
        {doc.status === "failed" && (
          // Said plainly, with the ONE action that helps. The stored error
          // string is for operators and is deliberately not shown — it names
          // internals the reader cannot act on.
          <div data-doc-failed style={S.notice}>
            This document could not be written. Ask for it again in chat.
          </div>
        )}

        {/* The body is sanitized SERVER-SIDE on every write
            (app/custom_artifact_html.py), which is what makes rendering it
            inline safe — an allowlist applied at the point of storage, so
            every reader including this one is covered without repeating it. */}
        <div
          data-doc-body
          style={S.body}
          dangerouslySetInnerHTML={{ __html: doc.body_html }}
        />

        {doc.status === "ready" && !doc.body_html.trim() && (
          <p style={S.muted}>This document is empty.</p>
        )}
      </div>
    </div>
  )
}

const S: Record<string, React.CSSProperties> = {
  page: {
    minHeight: "100%",
    padding: "32px 24px 96px",
    background: "var(--surface-2, #F4F1EA)",
    display: "flex",
    justifyContent: "center",
  },
  sheet: {
    width: "100%",
    // See the note above: prose, not a dashboard.
    maxWidth: 800,
    background: "var(--surface, #fff)",
    border: "1px solid var(--line, #E8E6E0)",
    borderRadius: 12,
    padding: "40px 56px 64px",
  },
  skeleton: { height: 420, opacity: 0.5 },
  back: {
    border: "none", background: "none", padding: 0, cursor: "pointer",
    fontSize: 12.5, color: "var(--ink-3, #8C8A84)", marginBottom: 20,
  },
  title: {
    fontSize: 30, fontWeight: 700, lineHeight: 1.2,
    color: "var(--ink, #1A1A17)", margin: "0 0 6px",
  },
  kind: {
    fontSize: 12, textTransform: "uppercase", letterSpacing: "0.05em",
    color: "var(--ink-3, #8C8A84)", marginBottom: 28,
  },
  notice: {
    fontSize: 13, color: "var(--ink-2, #5A5853)",
    background: "var(--surface-2, #F4F1EA)",
    border: "1px solid var(--line, #E8E6E0)",
    borderRadius: 8, padding: "10px 14px", marginBottom: 24,
  },
  body: { fontSize: 15, lineHeight: 1.7, color: "var(--ink, #1A1A17)" },
  muted: { fontSize: 13.5, color: "var(--ink-3, #8C8A84)" },
  link: {
    border: "none", background: "none", padding: 0, cursor: "pointer",
    fontSize: 13.5, color: "var(--accent, #179463)", fontWeight: 600,
  },
}

/** Path to one team document. Query param, not a segment — this app is a
 *  static export (see page.tsx). Exported so callers never hand-build it. */
export function documentPath(id: number): string {
  return `/artifacts/doc?id=${encodeURIComponent(String(id))}`
}
