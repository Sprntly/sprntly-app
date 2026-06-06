"use client"

/**
 * P3-03 — F8 anchored-comments panel for a generated prototype.
 *
 * A right-hand Google-Docs-style panel mounted into the `<PrototypeViewer>`
 * chrome slot (P2-05) on the public `/p/<token>` surface. A viewer right-clicks
 * any prototype element to anchor a comment; existing comments render with their
 * author + timestamp, visually distinguished by AD12 lifecycle status:
 *   - `open`     → solid pin + active thread.
 *   - `resolved` → muted/checkmark pin + collapsed thread (resolve is internal-only).
 *   - `orphaned` → de-emphasised section with NO pin (the anchor no longer exists
 *                  in the current bundle; P3-04 sets this status, the panel only
 *                  renders it) + an "anchor removed" affordance.
 *
 * Testability split mirrors `CompletionBar.tsx` / `DesignAgentDrawer.tsx`: the
 * repo's vitest runs in a `node` env with no jsdom / @testing-library, so the
 * pure markup lives in `CommentsPanelView` (SSR-renderable via
 * `renderToStaticMarkup`) and the I/O lives in exported pure async helpers
 * (`runLoadComments`, `runCreateComment`, `runResolveComment`) that take their
 * deps as arguments. The container wires React state to those units.
 *
 * Per BUILD.md §6 this file adds NO CSS to the hot `globals.css`; it uses
 * component-scoped class strings only (`comments-panel`, `comment-thread`,
 * `comment-pin`, `comment--resolved`, `comment--orphaned`, `comment-composer`).
 *
 * AD4 collision (see [[ad4-collision-by-design]]): one `data-anchor-id` can match
 * N>1 structurally-identical elements (canonical: a ContactForm's Name + Email
 * inputs both hash to `fb3007b5`). Pin rendering MUST NOT assume a 1:1
 * anchor↔element mapping — `findAnchorMatches` can return N elements and
 * `buildPinModel` renders a pin for the match set (first match + "+N more")
 * without throwing and without dropping the comment.
 */

import { useEffect, useRef, useState } from "react"
import { designAgentApi, type CommentRecord } from "../../lib/api"

// ---- Author identity helpers -------------------------------------------------
// Comment rows show author label + avatar chip + relative timestamp. The backend
// CommentRecord carries `author` (server-attributed) + `created_at` (ISO). These
// pure helpers derive the display name, the avatar initials, and a short relative
// timestamp. Exported so the pin-comment rows in PostGenerationResult reuse the
// same identity rendering (one source of truth).

/** Initials (1–2 chars, uppercase) from an author label. Falls back to "?". */
export function authorInitials(author: string | null | undefined): string {
  const a = (author ?? "").trim()
  if (!a) return "?"
  const parts = a.split(/[\s._-]+/).filter(Boolean)
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase()
  return a.slice(0, 2).toUpperCase()
}

/** Short relative timestamp ("just now", "5m", "3h", "2d") from an ISO string.
 *  Falls back to the raw string when it can't be parsed (SSR-safe — uses a
 *  caller-supplied `now` so the pure view stays deterministic in tests). */
export function shortRelativeTime(
  iso: string | null | undefined,
  now: number = Date.now(),
): string {
  if (!iso) return ""
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return iso
  const sec = Math.max(0, Math.round((now - t) / 1000))
  if (sec < 45) return "just now"
  const min = Math.round(sec / 60)
  if (min < 60) return `${min}m`
  const hr = Math.round(min / 60)
  if (hr < 24) return `${hr}h`
  const day = Math.round(hr / 24)
  if (day < 7) return `${day}d`
  const wk = Math.round(day / 7)
  if (wk < 5) return `${wk}w`
  return new Date(t).toLocaleDateString()
}

/** A small brand-tinted initials avatar (David's `.pc-av`). Shared by the
 *  CommentsPanel rows and the PostGenerationResult pin-comment rows. */
export function CommentAvatar({ author }: { author: string | null | undefined }) {
  return (
    <span className="pc-av" data-testid="comment-avatar" aria-hidden="true">
      {authorInitials(author)}
    </span>
  )
}

// ---- anchor-id capture (AD4 primitive) --------------------------------------

/**
 * Walk up from the right-clicked target to the nearest element carrying a
 * `data-anchor-id` (auto-applied by the Vite plugin, AD4 — the agent never
 * emits it manually). The iframe sandbox is `allow-scripts allow-same-origin`
 * (P2-05), so same-origin DOM is reachable. Returns null when no ancestor
 * carries an anchor id. For P3 MVP the cross-iframe contextmenu→postMessage
 * bridge is out of scope; capture uses `closest` on same-origin DOM (P3-13 e2e
 * will surface it if the sandbox blocks access in the real build).
 */
export function captureAnchorId(target: Element | null): string | null {
  return target?.closest("[data-anchor-id]")?.getAttribute("data-anchor-id") ?? null
}

// ---- AD4 collision-safe pin model -------------------------------------------

/** A document-like surface exposing only the query we need — keeps the helper
 *  testable with a tiny mock (no jsdom) and works against an iframe's
 *  contentDocument. */
export type AnchorQueryable = Pick<Document, "querySelectorAll">

/**
 * Find every element in `doc` carrying `data-anchor-id === anchorId`. Returns
 * an array (possibly length 0, 1, or N>1 — the AD4 collision case). Defensive:
 * an empty/missing doc or a malformed selector yields `[]` rather than throwing
 * so a single bad anchor never blanks the whole panel.
 */
export function findAnchorMatches(
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

/**
 * Build a render model for the pin(s) of one comment given its match set. When
 * N>1 elements collide on the same anchor id, MVP renders a pin on the first
 * match plus a "+N more" badge (per [[ad4-collision-by-design]]). `count === 0`
 * is the not-yet-rendered / not-in-DOM case (e.g. SSR, or anchor not in the
 * current bundle) — the comment still renders in its list; it just has no pin.
 */
export function buildPinModel(matches: Element[]): {
  count: number
  extraLabel: string | null
} {
  const count = matches.length
  return { count, extraLabel: count > 1 ? `+${count - 1} more` : null }
}

// ---- orchestration helpers (pure, dependency-injected, SSR-free) ------------

/** Load every comment for the token's prototype (all statuses). */
export async function runLoadComments({
  token,
  api,
}: {
  token: string
  api: Pick<typeof designAgentApi, "listCommentsByToken">
}): Promise<CommentRecord[]> {
  return api.listCommentsByToken(token)
}

/**
 * Create a comment on the public route, then prepend the returned record to the
 * current list (newest-first). `comments` is optional (defaults to empty) so the
 * helper composes from a bare `{ token, anchorId, body, api }` call too.
 */
export async function runCreateComment({
  token,
  anchorId,
  body,
  api,
  comments = [],
}: {
  token: string
  anchorId: string
  body: string
  api: Pick<typeof designAgentApi, "createCommentByToken">
  comments?: CommentRecord[]
}): Promise<CommentRecord[]> {
  const created = await api.createCommentByToken(token, {
    anchor_id: anchorId,
    body,
  })
  return [created, ...comments]
}

/** Resolve a comment (internal/authed only — addressed by prototype id). */
export async function runResolveComment({
  prototypeId,
  commentId,
  api,
}: {
  prototypeId: number
  commentId: number
  api: Pick<typeof designAgentApi, "resolveComment">
}): Promise<CommentRecord> {
  return api.resolveComment(prototypeId, commentId)
}

// ---- pure view --------------------------------------------------------------

const ORPHAN_AFFORDANCE = "Anchor removed in a later version"

export type CommentsPanelViewProps = {
  comments: CommentRecord[]
  /** When set, the anchored composer is open for this anchor id. */
  composer?: { anchorId: string; body: string } | null
  busy?: boolean
  error?: string | null
  /** The resolve affordance renders only on the internal mount (prototypeId
   *  supplied). The public viewer creates + reads only. */
  canResolve?: boolean
  /** Optional per-anchor "+N more" badge text (AD4 collision), keyed by
   *  anchor_id. Absent on SSR / when the bundle DOM is not yet queryable. */
  pinExtra?: Record<string, string | null>
  onBodyChange?: (value: string) => void
  onSubmit?: () => void
  onCancelComposer?: () => void
  onResolve?: (commentId: number) => void
  /** Apply hands a comment to the IterateComposer. Supplied only on the signed-in
   *  mount; absent on the public viewer → no Apply button. Apply also resolves the
   *  comment (the container handler calls the parent seam AND calls resolve). */
  onApply?: (comment: CommentRecord) => void
  /** Ignore — resolve the comment WITHOUT pre-filling the composer. Supplied only
   *  on the signed-in mount (alongside `onApply`). Absent → no Ignore button. */
  onIgnore?: (comment: CommentRecord) => void
}

function CommentThread({
  comment,
  withPin,
  canResolve,
  pinExtra,
  busy = false,
  onResolve,
  onApply,
  onIgnore,
}: {
  comment: CommentRecord
  withPin: boolean
  canResolve?: boolean
  pinExtra?: string | null
  /** Disables Apply/Ignore while an iterate is in flight to prevent overlapping runs. */
  busy?: boolean
  onResolve?: (commentId: number) => void
  /** When supplied (signed-in mount only), an Apply action hands the comment to
   *  the IterateComposer to pre-fill an iterate prompt. Absent on the public mount
   *  → no Apply button renders. Apply also resolves the comment. */
  onApply?: (comment: CommentRecord) => void
  /** Ignore — resolve without pre-fill. */
  onIgnore?: (comment: CommentRecord) => void
}) {
  const resolved = comment.status === "resolved"
  return (
    <li
      className={`comment-thread${resolved ? " comment--resolved resolved" : ""}`}
      data-testid={`comment-thread-${comment.id}`}
      data-status={comment.status}
    >
      {withPin && (
        <span
          className={`comment-pin${resolved ? " comment-pin--resolved" : ""}`}
          data-testid={`comment-pin-${comment.id}`}
          aria-hidden="true"
        >
          {resolved ? "✓" : "●"}
          {pinExtra && <span className="comment-pin-extra">{pinExtra}</span>}
        </span>
      )}
      {/* Author + avatar + relative timestamp header. The avatar uses author initials, brand-tinted. */}
      <div className="comment-meta comment-meta-head">
        <CommentAvatar author={comment.author} />
        <span className="comment-author proto-comment-au">{comment.author}</span>
        <time
          className="comment-timestamp proto-comment-time"
          dateTime={comment.created_at}
          title={comment.created_at}
        >
          {shortRelativeTime(comment.created_at)}
        </time>
      </div>
      <div className="comment-body">{comment.body}</div>
      {/* Apply / Ignore actions. Apply calls the parent handler (pre-fill or
          immediate-iterate) then resolves; Ignore resolves only. Both rendered
          only on the signed-in mount (onApply/onIgnore supplied) + open comments. */}
      {(onApply || onIgnore || (canResolve && !resolved)) && !resolved && (
        <div className="comment-actions" data-testid={`comment-actions-${comment.id}`}>
          {onApply && (
            <button
              type="button"
              className="btn btn-accent comment-apply-btn"
              data-testid={`comment-apply-${comment.id}`}
              disabled={busy}
              onClick={() => onApply(comment)}
            >
              Apply
            </button>
          )}
          {onIgnore && (
            <button
              type="button"
              className="btn comment-ignore-btn"
              data-testid={`comment-ignore-${comment.id}`}
              disabled={busy}
              onClick={() => onIgnore(comment)}
            >
              Ignore
            </button>
          )}
          {/* Keep the original explicit Resolve for non-Apply/Ignore mounts. */}
          {!onApply && !onIgnore && canResolve && (
            <button
              type="button"
              className="btn comment-resolve-btn"
              data-testid={`comment-resolve-${comment.id}`}
              onClick={() => onResolve?.(comment.id)}
            >
              Resolve
            </button>
          )}
        </div>
      )}
    </li>
  )
}

/** Pure presentational panel — no hooks, no I/O → SSR-renderable in node-env
 *  vitest. The container threads live state + handlers into it. */
export function CommentsPanelView({
  comments,
  composer = null,
  busy = false,
  error = null,
  canResolve = false,
  pinExtra,
  onBodyChange,
  onSubmit,
  onCancelComposer,
  onResolve,
  onApply,
  onIgnore,
}: CommentsPanelViewProps) {
  const open = comments.filter((c) => c.status === "open")
  const resolved = comments.filter((c) => c.status === "resolved")
  const orphaned = comments.filter((c) => c.status === "orphaned")

  return (
    <aside className="comments-panel" data-testid="comments-panel">
      <header className="comments-panel-header">
        <h2 className="comments-panel-title">Comments</h2>
      </header>

      {composer && (
        <form
          className="comment-composer"
          data-testid="comment-composer"
          onSubmit={(e) => {
            e.preventDefault()
            onSubmit?.()
          }}
        >
          <p className="comment-composer-anchor" data-testid="comment-composer-anchor">
            Anchored to <code>{composer.anchorId}</code>
          </p>
          <textarea
            className="comment-composer-input"
            data-testid="comment-composer-input"
            value={composer.body}
            placeholder="Add a comment…"
            onChange={(e) => onBodyChange?.(e.target.value)}
          />
          <div className="comment-composer-actions">
            <button
              type="submit"
              className="btn btn-accent"
              data-testid="comment-composer-submit"
              disabled={busy || !composer.body.trim()}
            >
              Comment
            </button>
            <button
              type="button"
              className="btn"
              data-testid="comment-composer-cancel"
              onClick={() => onCancelComposer?.()}
            >
              Cancel
            </button>
          </div>
        </form>
      )}

      {comments.length === 0 ? (
        <p className="comments-empty" data-testid="comments-empty">
          Right-click any element in the prototype to leave a comment.
        </p>
      ) : (
        <>
          <ul className="comment-list" data-testid="comments-open">
            {open.map((c) => (
              <CommentThread
                key={c.id}
                comment={c}
                withPin
                canResolve={canResolve}
                pinExtra={pinExtra?.[c.anchor_id] ?? null}
                busy={busy}
                onResolve={onResolve}
                onApply={onApply}
                onIgnore={onIgnore}
              />
            ))}
          </ul>

          {resolved.length > 0 && (
            <section
              className="comments-resolved comment--resolved"
              data-testid="comments-resolved"
            >
              <h3 className="comments-section-title">Resolved</h3>
              <ul className="comment-list comment-list--collapsed">
                {resolved.map((c) => (
                  <CommentThread
                    key={c.id}
                    comment={c}
                    withPin
                    pinExtra={pinExtra?.[c.anchor_id] ?? null}
                  />
                ))}
              </ul>
            </section>
          )}

          {orphaned.length > 0 && (
            <section
              className="comments-orphaned comment--orphaned"
              data-testid="comments-orphaned"
            >
              <h3 className="comments-section-title">Orphaned</h3>
              <p className="comment-orphaned-note" data-testid="comment-orphaned-note">
                {ORPHAN_AFFORDANCE}
              </p>
              <ul className="comment-list comment-list--muted">
                {orphaned.map((c) => (
                  // No <pin> for orphaned comments — there is no element to
                  // anchor to (withPin omitted → defaults to no pin).
                  <CommentThread key={c.id} comment={c} withPin={false} />
                ))}
              </ul>
            </section>
          )}
        </>
      )}

      {error && (
        <p className="comments-error error" data-testid="comments-error">
          {error}
        </p>
      )}
    </aside>
  )
}

// ---- container --------------------------------------------------------------

export type CommentsPanelProps = {
  token: string
  /** Supplied only on the internal/authed mount — enables the resolve
   *  affordance. The public viewer omits it (create + read only). */
  prototypeId?: number
  /** P3-14 (F10): supplied only on the signed-in mount (DesignAgentLauncher) —
   *  an Apply action on an open comment hands it to the IterateComposer to
   *  pre-fill an iterate prompt. Absent on the public viewer → no Apply button
   *  (AC9 — the public mount behaves exactly as before P3-14). */
  onApply?: (comment: CommentRecord) => void
  /** When supplied, Apply runs the comment through the canvas's shared iterate
   *  runner immediately (instead of pre-filling the composer). The host passes the
   *  runner's `runIterate` here; the comment body becomes the iterate instruction
   *  and the comment is resolved. Takes precedence over `onApply` when present.
   *  The agent decides applicability — the client never fabricates a change. */
  onIterateComment?: (comment: CommentRecord) => void
  /** Disables Apply while the shared runner is mid-iterate to prevent overlapping runs. */
  iterateBusy?: boolean
  /** When true, the composer is suppressed (no contextmenu listener, no write
   *  affordance). Used on the public /p/<token> surface where comment create is
   *  disabled (B9b/B9c). Read-only viewers can still read all comments. */
  readOnly?: boolean
}

function toMessage(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback
}

/** Public component. Loads comments on mount, listens for right-clicks to open
 *  an anchored composer, and wires submit/resolve to the orchestration helpers
 *  and the canonical `designAgentApi`. Delegates rendering to the pure view. */
export function CommentsPanel({
  token,
  prototypeId,
  onApply,
  onIterateComment,
  iterateBusy = false,
  readOnly = false,
}: CommentsPanelProps) {
  // (see handleApply / handleIgnore below for the CHANGE C resolve wiring)
  const [comments, setComments] = useState<CommentRecord[]>([])
  const [composer, setComposer] = useState<{ anchorId: string; body: string } | null>(
    null,
  )
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const panelRef = useRef<HTMLDivElement>(null)

  // Load existing comments once on mount.
  useEffect(() => {
    let active = true
    runLoadComments({ token, api: designAgentApi })
      .then((list) => {
        if (active) setComments(list)
      })
      .catch((e) => {
        if (active) setError(toMessage(e, "Failed to load comments"))
      })
    return () => {
      active = false
    }
  }, [token])

  // Right-click anywhere with a reachable anchor id opens the composer for that
  // anchor. Suppressed in readOnly mode (public viewer — comment create disabled).
  // For P3 MVP this captures same-origin DOM under the panel/parent document; the
  // cross-iframe bridge is a follow-up (see scope note above).
  useEffect(() => {
    if (readOnly) return
    function onContextMenu(e: MouseEvent) {
      const anchorId = captureAnchorId(e.target as Element | null)
      if (!anchorId) return
      e.preventDefault()
      setComposer({ anchorId, body: "" })
    }
    document.addEventListener("contextmenu", onContextMenu)
    return () => document.removeEventListener("contextmenu", onContextMenu)
  }, [readOnly])

  async function handleSubmit() {
    if (!composer || !composer.body.trim()) return
    setBusy(true)
    setError(null)
    try {
      const next = await runCreateComment({
        token,
        anchorId: composer.anchorId,
        body: composer.body,
        api: designAgentApi,
        comments,
      })
      setComments(next)
      setComposer(null)
    } catch (e) {
      setError(toMessage(e, "Failed to add comment"))
    } finally {
      setBusy(false)
    }
  }

  async function handleResolve(commentId: number) {
    if (prototypeId == null) return
    setBusy(true)
    setError(null)
    try {
      const updated = await runResolveComment({
        prototypeId,
        commentId,
        api: designAgentApi,
      })
      setComments((prev) =>
        prev.map((c) => (c.id === updated.id ? updated : c)),
      )
    } catch (e) {
      setError(toMessage(e, "Failed to resolve comment"))
    } finally {
      setBusy(false)
    }
  }

  // Apply = hand the comment to the parent (pre-fills the IterateComposer or runs
  // the shared iterate runner) AND resolve it. Ignore = resolve ONLY (no pre-fill).
  // Apply renders only when the parent supplied `onApply` or `onIterateComment`
  // (signed-in mount) AND we can resolve (prototypeId).
  function handleApply(comment: CommentRecord) {
    // When the host supplies `onIterateComment`, Apply runs the immediate iterate
    // path — the comment body is sent into the shared runner (the agent decides
    // applicability; the client fabricates nothing) and the comment is resolved.
    // Falls back to the pre-fill seam (`onApply`) only when no runner is supplied.
    if (onIterateComment) {
      onIterateComment(comment)
    } else {
      onApply?.(comment)
    }
    void handleResolve(comment.id)
  }

  function handleIgnore(comment: CommentRecord) {
    void handleResolve(comment.id)
  }

  // Apply/Ignore are only meaningful when the parent wants the comment (either
  // the pre-fill seam OR the immediate-iterate seam) AND we can resolve (authed
  // mount). Public viewer → neither.
  const canApply = (onApply != null || onIterateComment != null) && prototypeId != null

  return (
    <div ref={panelRef} className="comments-panel-mount">
      <CommentsPanelView
        comments={comments}
        composer={readOnly ? null : composer}
        busy={busy || iterateBusy}
        error={error}
        canResolve={prototypeId != null}
        onBodyChange={(body) =>
          setComposer((c) => (c ? { ...c, body } : c))
        }
        onSubmit={handleSubmit}
        onCancelComposer={() => setComposer(null)}
        onResolve={handleResolve}
        onApply={canApply ? handleApply : undefined}
        onIgnore={canApply ? handleIgnore : undefined}
      />
    </div>
  )
}
