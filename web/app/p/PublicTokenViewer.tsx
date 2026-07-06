"use client"
// Client viewer for the public /p/<token> route (P2-05). Co-located with the
// page exactly like web/app/(app)/onboarding/[step]/OnboardingStep.tsx — the
// server shell (page.tsx) handles static export; this owns the runtime
// behaviour. Reads the real token from the LIVE URL (window.location.pathname,
// client-side) — NOT useParams(), which under output:"export" returns the
// prerendered "_" sentinel (see shareTokenFromPathname). Resolves it against the
// public backend resolver, and branches: public → iframe; passcode → gate;
// missing/private/not-ready/404 → notFound().
//
// The resolver + branch logic are split into pure functions (resolveToken,
// nextViewerState) so they are unit-testable in the node-env vitest run, which
// has no DOM/router — the same split convention as DesignAgentDrawer's
// runGenerateFlow. Relative imports (not `@/…`) match the codebase + vitest.
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react"
import { notFound } from "next/navigation"
import { PrototypeViewer } from "../components/design-agent/PrototypeViewer"
import { DeviceBadge } from "../components/design-agent/DeviceBadge"
import { ManualEditOverlay } from "../components/design-agent/ManualEditOverlay"
// CommentAvatar + shortRelativeTime are reused (not redefined) for the new
// General section's cards, matching the identity chrome pinned cards already
// use elsewhere on this surface (one source of truth for author rendering).
import { CommentsPanel, CommentAvatar, shortRelativeTime } from "../components/design-agent/CommentsPanel"
// C2b: the public surface drives the SAME pin engine as the signed-in editor via
// the shared usePinMarking hook + the extracted MarkOverlay / PinLayer /
// PrototypeMarkLayer leaves. The only per-surface difference is the create-fn:
// the public viewer routes via createCommentByToken (no prototypeId / auth).
import { usePinMarking } from "../components/design-agent/usePinMarking"
import { MarkOverlay, PinLayer, PrototypeMarkLayer } from "../components/design-agent/PrototypeMarkLayer"
import { designAgentApi, type CommentRecord } from "../lib/api"
import { PasscodeGate } from "./PasscodeGate"
import { resolveToken, type ResolvedView } from "./resolveToken"
import { shareTokenFromLocation } from "./shareTokenFromPathname"
import { IconClose, IconMessage, IconPin, IconCheck } from "../components/shared/app-icons"

// ── General-section line icons (inline SVG, stroke-only — no emoji) ─────────
// Not added to the shared app-icons.tsx registry: these are specific to the
// new General/Pinned sidebar split and used only here.
function IconSpeechBubble({ size = 12 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M1 1h10a.5.5 0 0 1 .5.5v6a.5.5 0 0 1-.5.5H4l-3 2V1.5A.5.5 0 0 1 1 1z" />
    </svg>
  )
}
function IconSpeechBubblePlus({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M1.5 1.5h11a.5.5 0 0 1 .5.5v7a.5.5 0 0 1-.5.5H4l-2.5 2V2a.5.5 0 0 1 .5-.5z" />
      <line x1="7" y1="4.5" x2="7" y2="8" />
      <line x1="4.5" y1="6.25" x2="9.5" y2="6.25" />
    </svg>
  )
}
function IconPinMarker({ size = 11 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 11 11" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M5.5 1a3 3 0 1 1 0 6 3 3 0 0 1 0-6z" />
      <line x1="5.5" y1="7" x2="5.5" y2="10.5" />
    </svg>
  )
}

export type { ResolvedView }

export type ViewerState =
  | { kind: "loading" }
  | { kind: "notfound" }
  | { kind: "error" }
  | { kind: "passcode" }
  | { kind: "ready"; bundleUrl: string; isComplete: boolean; targetPlatform: string }

// Pure reducer over a resolver outcome → the terminal viewer state. Passcode
// mode arrives with bundle_url === null (the bundle is withheld until POST
// /passcode succeeds), so it maps to the gate; any other missing bundle_url is
// treated as not-found rather than rendering an empty iframe.
export function nextViewerState(
  view: ResolvedView | null,
): Extract<ViewerState, { kind: "notfound" | "passcode" | "ready" }> {
  if (!view) return { kind: "notfound" }
  if (view.share_mode === "passcode" && !view.bundle_url) return { kind: "passcode" }
  if (!view.bundle_url) return { kind: "notfound" }
  return {
    kind: "ready",
    bundleUrl: view.bundle_url,
    isComplete: view.is_complete,
    targetPlatform: view.target_platform,
  }
}

// localStorage key for the anon viewer's display name. Persisted once on first
// comment so a returning viewer is not re-prompted. Reading is wrapped in a
// try/catch — localStorage can throw (private mode / disabled storage) and the
// viewer must still function (it just re-prompts).
const VIEWER_NAME_KEY = "da-viewer-name"

function readStoredViewerName(): string {
  try {
    return (window.localStorage.getItem(VIEWER_NAME_KEY) ?? "").trim()
  } catch {
    return ""
  }
}

function persistViewerName(name: string): void {
  try {
    window.localStorage.setItem(VIEWER_NAME_KEY, name)
  } catch {
    /* storage unavailable — proceed without persistence */
  }
}

// Up-to-two-letter initials for the identity-strip avatar. A single-word name
// yields one letter; empty segments are dropped so a trailing space never
// produces an empty/"undefined" chip (the single-field name has no first/last
// concatenation artifact to begin with).
function viewerInitials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean)
  return words.slice(0, 2).map((w) => w[0]!.toUpperCase()).join("")
}

export function PublicTokenViewer() {
  // The real share token comes from the live URL, not useParams() — under
  // output:"export" the route is prerendered under the "_" sentinel, so
  // useParams() returns "_". `undefined` = not yet read on the client (stay in
  // loading); `null` = read but no real token (sentinel/malformed → notFound()).
  const [token, setToken] = useState<string | null | undefined>(undefined)
  useEffect(() => {
    setToken(shareTokenFromLocation())
  }, [])
  const [state, setState] = useState<ViewerState>({ kind: "loading" })
  // C2a public-viewer chrome state. `commentsOpen` toggles the writable-anon
  // CommentsPanel.
  const [commentsOpen, setCommentsOpen] = useState(false)
  // Phase 3 (anon public writes): the viewer's display name, hydrated from
  // localStorage. Empty until the viewer supplies it via the name-capture form,
  // which is shown the first time they open the writable comments surface with no
  // stored name. Threaded onto BOTH create paths (the pin onCreate + the
  // CommentsPanel mount) so anon comments are attributed to a name.
  const [viewerName, setViewerName] = useState("")
  const [fullName, setFullName] = useState("")
  // dedup: canonical server comment ids from the mounted CommentsPanel,
  // forwarded to PrototypeMarkLayer so a saved pin whose comment is in the server
  // list has its local card suppressed (the canvas dot stays). Public pins stay
  // non-resolvable (no onResolve passed) — this is dedup only.
  const [serverCommentIds, setServerCommentIds] = useState<number[]>([])
  useEffect(() => {
    setViewerName(readStoredViewerName())
  }, [])

  // General (unpinned) comments: a separate read of the SAME by-token list
  // CommentsPanel already fetches internally, kept independent here because
  // CommentsPanel only surfaces ids via onCommentsLoaded (not full records) and
  // is out of scope to modify on this ticket.
  const [allComments, setAllComments] = useState<CommentRecord[]>([])
  const refreshComments = useCallback(() => {
    if (typeof token !== "string") return
    designAgentApi
      .listCommentsByToken(token)
      .then((list) => setAllComments(list))
      .catch(() => {
        // Degrade silently, same posture as getByPrd/getActiveByPrd: the
        // General section simply shows empty until the next successful load.
      })
  }, [token])
  useEffect(() => {
    refreshComments()
  }, [refreshComments])

  // A general comment has BOTH null pin coords AND no element anchor (per the
  // data model: "a prototype_comments row with null pin coordinates AND null
  // anchor"). Checking pin_x_pct alone would also sweep in the OLDER
  // right-click-anywhere anchored comments (CommentsPanel's own composer path)
  // into General — those carry a real anchor_id but, by design, no x/y
  // position. `c.anchor_id == null` still narrows correctly at runtime even
  // though CommentRecord's declared type stays `string` (unwidened, to avoid
  // breaking CommentsPanel.tsx's `pinExtra?.[c.anchor_id]` Record index, which
  // is out of scope to touch) — the loose-equality null check does not rely on
  // that type being accurate.
  const byNewestFirst = (a: CommentRecord, b: CommentRecord) =>
    new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
  const generalComments = useMemo(
    () => allComments.filter((c) => c.pin_x_pct == null && c.anchor_id == null).sort(byNewestFirst),
    [allComments],
  )
  const generalOpenCount = useMemo(
    () => generalComments.filter((c) => c.status !== "resolved").length,
    [generalComments],
  )
  const pinnedOpenCount = useMemo(
    () => allComments.filter((c) => !(c.pin_x_pct == null && c.anchor_id == null) && c.status !== "resolved").length,
    [allComments],
  )

  const [generalComposerOpen, setGeneralComposerOpen] = useState(false)
  const [generalBody, setGeneralBody] = useState("")
  const [generalPosting, setGeneralPosting] = useState(false)
  const [generalError, setGeneralError] = useState<string | null>(null)
  const generalTextareaRef = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    if (generalComposerOpen) generalTextareaRef.current?.focus()
  }, [generalComposerOpen])

  function openGeneralComposer() {
    setGeneralError(null)
    setGeneralComposerOpen(true)
  }
  function cancelGeneralComposer() {
    setGeneralComposerOpen(false)
    setGeneralBody("")
    setGeneralError(null)
  }
  async function submitGeneralComment() {
    const trimmed = generalBody.trim()
    if (!trimmed || generalPosting || typeof token !== "string") return
    setGeneralPosting(true)
    setGeneralError(null)
    try {
      const created = await designAgentApi.createCommentByToken(token, {
        body: trimmed,
        anchor_id: null,
        pin_x_pct: null,
        pin_y_pct: null,
        viewer_name: viewerName,
      })
      // Prepend locally (newest-first) — avoids a second full-list round trip.
      setAllComments((prev) => [created, ...prev])
      setGeneralBody("")
      setGeneralComposerOpen(false)
    } catch {
      setGeneralError("Failed to post comment. Please try again.")
    } finally {
      setGeneralPosting(false)
    }
  }

  // Capture form is shown when the writable comments surface is open but no name
  // is known yet. On submit we persist then proceed; the panel renders next.
  const viewerNeedsName = !viewerName
  const needsName = commentsOpen && viewerNeedsName
  function handleNameSubmit(e: FormEvent) {
    e.preventDefault()
    const name = fullName.trim()
    if (!name) return
    persistViewerName(name)
    setViewerName(name)
    // Auto-enable the element selector so the viewer can immediately click an
    // element to comment — no separate Mark-button click. Idempotent
    // (setMarkMode, not toggleMark); the sidebar is already open so
    // onEnterMarkMode is a no-op here.
    pin.setMarkMode(true)
  }
  // C2b: real marking, driven by the shared usePinMarking hook. The create-fn is
  // the public createCommentByToken (no prototypeId / auth) — distinct from the
  // signed-in editor's withAuthRetry(createComment(prototype.id)). No
  // onPinIterate / onPinApply on this surface, so PrototypeMarkLayer's Apply /
  // Ignore stay hidden (editorMode=false). Entering mark mode + dropping a pin
  // both reveal the comments sidebar so the new pin row is visible.
  const pin = usePinMarking({
    onCreate: (payload) => designAgentApi.createCommentByToken(token as string, { ...payload, viewer_name: viewerName }),
    onEnterMarkMode: () => setCommentsOpen(true),
    onPinDropped: () => setCommentsOpen(true),
    // A pin comment must carry a real viewer name — never post "Anonymous". Until
    // the viewer supplies one, the submit aborts and the name-capture form is
    // surfaced (the comments sidebar holds the single Full name form). Once the
    // name is set, requireName flips false and the pin posts attributed.
    requireName: viewerNeedsName,
    onRequireName: () => setCommentsOpen(true),
    // Public viewer stays in mark mode across repeated comments so the next click
    // starts a new pin without re-enabling the element selector each time.
    stayInMarkMode: true,
  })

  useEffect(() => {
    if (token === undefined) return // not yet read from the URL → stay loading
    if (!token) {
      setState({ kind: "notfound" })
      return
    }
    let active = true
    resolveToken(token)
      .then((view) => {
        if (active) setState(nextViewerState(view))
      })
      .catch(() => {
        if (active) setState({ kind: "error" })
      })
    return () => {
      active = false
    }
  }, [token])

  // notFound() during render is the supported client-component pattern (it
  // throws into the nearest not-found boundary).
  if (state.kind === "notfound") notFound()
  if (state.kind === "loading") {
    return (
      <div className="design-agent-surface da-public-loading">Loading prototype…</div>
    )
  }
  if (state.kind === "error") {
    return (
      <div className="design-agent-surface da-public-error">
        Could not load this prototype. Please try again.
      </div>
    )
  }
  if (state.kind === "passcode") return <PasscodeGate token={token as string} />
  // Single-device gate — mirrors the signed-in single-device viewer's toggle gate.
  // A prototype targeting one device has nothing to toggle to, so we suppress the
  // Desktop/Mobile toggle (via showDesktop/showMobile → PrototypeViewer's showToggle)
  // and show a static DeviceBadge in its slot instead. "both"/legacy/null → both
  // true → the toggle renders as before (no regression).
  const targetPlatform = state.targetPlatform
  const showDesktop = targetPlatform !== "mobile"
  const showMobile = targetPlatform !== "desktop"
  const singleDevice = !showDesktop || !showMobile
  return (
    <div className="design-agent-surface">
      <div className="da-ready" data-testid="da-ready">
        <div
          className={`da-stage${pin.markMode ? " marking" : ""}`}
          data-testid="da-canvas-center"
        >
          <PrototypeViewer
            bundleUrl={state.bundleUrl}
            isComplete={state.isComplete}
            // Single-device gate: suppress the in-frame Desktop/Mobile toggle when
            // only one device applies (PrototypeViewer's showToggle = showDesktop &&
            // showMobile). "both" leaves both true → toggle renders unchanged.
            showDesktop={showDesktop}
            showMobile={showMobile}
            // Start the stage in the prototype's own form factor so a mobile-only
            // proto renders in the mobile bezel (not a desktop frame under a "Mobile"
            // badge). Mirrors the signed-in single-device viewer's stage default.
            initialPlatform={targetPlatform === "mobile" ? "mobile" : "desktop"}
            // Edge-to-edge: suppress the cosmetic browser-frame decoration (traffic
            // lights + URL bar) so the shared prototype renders flush. The Mark +
            // Comment headControls below are NOT gated by hideChrome and remain.
            hideChrome
            // C2a: Mark + Comment controls in the browser-frame head. Styled like the
            // platform toggle (.platform-toggle group look). aria-pressed reflects the
            // toggle state. Comment opens the collapsible da-right sidebar.
            // C2b: Mark drives the real pin/mark overlay via the shared hook
            // (pin.toggleMark / pin.markMode), mounted in the stageOverlay below.
            headControls={
              <>
                {/* Single-device: the static device pill fills the toggle's vacated
                    slot, left of the Mark/Comment group (matches the toggle's former
                    position). Renders nothing for "both"/legacy. */}
                {singleDevice && <DeviceBadge platform={targetPlatform} />}
                <div
                  className="platform-toggle proto-head-controls-group"
                  role="group"
                  aria-label="Prototype tools"
                >
                <button
                  type="button"
                  className={pin.markMode ? "mark-active" : ""}
                  aria-pressed={pin.markMode}
                  data-testid="public-mark-toggle"
                  onClick={() => pin.toggleMark()}
                  title="Mark"
                >
                  <IconPin size={14} />
                </button>
                <button
                  type="button"
                  className={commentsOpen ? "active" : ""}
                  aria-pressed={commentsOpen}
                  data-testid="public-comments-toggle"
                  onClick={() => setCommentsOpen((v) => !v)}
                  title="Comments"
                >
                  <IconMessage size={14} />
                </button>
                </div>
              </>
            }
            // C2b: the marking overlay renders INSIDE `.proto-stage`, layered over the
            // iframe. MarkOverlay is click-inert except in mark mode (where it
            // hit-tests the iframe + drops a pin); PinLayer renders the numbered pins.
            stageOverlay={
              <>
                <MarkOverlay markMode={pin.markMode} onStageClick={pin.handleStageClick} />
                <PinLayer pins={pin.pins} computedPinPositions={pin.computedPinPositions} occludedPins={pin.occludedPins} />
              </>
            }
            chrome={
              /* The manual-edit overlay renders its toggle only when a prototypeId
                 is supplied. The public token view is minimum-disclosure and exposes
                 no prototypeId on this surface, so the overlay mounts with prototypeId
                 undefined and renders nothing here (non-breaking). */
              <ManualEditOverlay isComplete={state.isComplete} />
            }
          />
        </div>
        {/* C2a + C2b: collapsible right-side comments panel — same da-right layout
            as the signed-in editor. Width transitions 0→340px when open. The
            CommentsPanel mounts with no prototypeId (minimum-disclosure) so create
            routes via createCommentByToken(token) and list via listCommentsByToken(token);
            canResolve is false for anonymous viewers. The name-capture form lives
            inside the panel body so it appears in the sidebar on first comment. */}
        <aside
          className={`da-right${commentsOpen ? " open" : ""}`}
          data-testid="da-canvas-comments"
          aria-hidden={commentsOpen ? "false" : "true"}
        >
          <div className="da-right-top">
            <IconMessage size={15} />
            <span className="da-right-title">Comments</span>
            <button
              type="button"
              className="da-right-close"
              title="Hide comments"
              aria-label="Hide comments"
              data-testid="public-comments-close"
              onClick={() => setCommentsOpen((v) => !v)}
            >
              <IconClose size={14} />
            </button>
          </div>
          <div className="da-right-body">
            {/* Post-submit identity strip — who the viewer is commenting as. Shown
                once a name is set (initials avatar + full name). Cosmetic
                orientation; the full name has no first/last concat artifact. */}
            {viewerName && (
              <div
                className="viewer-identity-strip"
                data-testid="viewer-identity-strip"
              >
                <div className="pc-av" aria-hidden>
                  {viewerInitials(viewerName)}
                </div>
                <div>
                  <div className="viewer-identity-name">{viewerName}</div>
                  <div className="viewer-identity-sub">Commenting as</div>
                </div>
              </div>
            )}
            {/* Mark-mode notice — orients the viewer while the element selector is
                on (auto-enabled on name submit). Belt-and-suspenders alongside the
                canvas crosshair + inset ring. */}
            {pin.markMode && (
              <div
                className="mark-mode-sidebar-notice"
                role="status"
                data-testid="mark-mode-notice"
              >
                <span className="mark-mode-notice-icon" aria-hidden>
                  <svg
                    width="14"
                    height="14"
                    viewBox="0 0 14 14"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.6"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <circle cx="7" cy="7" r="4" />
                    <line x1="7" y1="1" x2="7" y2="3" />
                    <line x1="7" y1="11" x2="7" y2="13" />
                    <line x1="1" y1="7" x2="3" y2="7" />
                    <line x1="11" y1="7" x2="13" y2="7" />
                  </svg>
                </span>
                <div className="mark-mode-notice-body">
                  <p className="mark-mode-notice-title">Click any element to comment</p>
                  <p className="mark-mode-notice-desc">
                    The element selector is on. Click something in the prototype to
                    attach a comment to it.
                  </p>
                </div>
              </div>
            )}
            {/* C2b: dropped-pin comment rows (draft composer + saved rows).
                editorMode=false + canResolve=false → Apply / Ignore / resolve
                stay hidden on the public surface. */}
            <PrototypeMarkLayer
              pins={pin.pins}
              editorMode={false}
              canResolve={false}
              onPinDraftChange={pin.handlePinDraftChange}
              onSubmitComment={pin.handlePinSubmit}
              onPinRemove={pin.handlePinRemove}
              serverCommentIds={serverCommentIds}
            />
            {commentsOpen && needsName && (
              /* Phase 3: first-comment name capture. Shown when the writable comments
                 surface is open but no name is stored yet. On submit we persist the
                 name to localStorage and proceed; a returning viewer is not re-prompted.
                 A short PII notice sets expectations about where the name + comment go.
                 Both the General and Pinned sections below are gated behind this same
                 name-capture step (a general comment must carry a real viewer name,
                 never "Anonymous", exactly like the pinned path). */
              <form
                className="design-agent-surface da-viewer-name-form"
                data-testid="viewer-name-form"
                onSubmit={handleNameSubmit}
              >
                <label className="da-viewer-name-label" htmlFor="da-viewer-full-name">
                  Add your name to comment
                </label>
                {/* Wrapper gives the input a row-flex context so its
                    `flex: 1 1 120px` grows horizontally (full-width single line)
                    rather than stretching vertically as a direct child of the
                    column form — otherwise it renders as a tall multi-line box. */}
                <div className="da-viewer-name-fields">
                  <input
                    id="da-viewer-full-name"
                    className="da-viewer-name-input"
                    data-testid="viewer-full-name-input"
                    type="text"
                    placeholder="Full name"
                    value={fullName}
                    onChange={(e) => setFullName(e.target.value)}
                    maxLength={80}
                    autoComplete="name"
                  />
                </div>
                <button
                  type="submit"
                  className="btn btn-accent da-viewer-name-submit"
                  data-testid="viewer-name-submit"
                  disabled={!fullName.trim()}
                >
                  Continue
                </button>
                <p className="da-viewer-name-notice" data-testid="viewer-name-notice">
                  Your name and comment are shared with the prototype&rsquo;s owner.
                </p>
              </form>
            )}
            {!needsName && (
              <>
                {/* General section — unpinned, prototype-level feedback. First in
                    the sidebar: the lower-friction entry point (no element click,
                    no mark mode required). */}
                <section
                  className="comments-section"
                  aria-label="General comments"
                  data-testid="general-comments-section"
                >
                  <h3 className="comments-section-title">
                    <IconSpeechBubble />
                    General
                    {generalOpenCount > 0 && (
                      <span
                        className="comments-section-count"
                        aria-label={`${generalOpenCount} open general comments`}
                      >
                        {generalOpenCount}
                      </span>
                    )}
                  </h3>
                  {generalComments.length > 0 ? (
                    <ul className="comment-list" data-testid="general-comments-list">
                      {generalComments.map((c) => (
                        <li
                          key={c.id}
                          className="comment-thread comment-thread--general"
                          data-testid={`general-comment-thread-${c.id}`}
                        >
                          <div className="comment-meta">
                            <CommentAvatar author={c.author} />
                            <span className="comment-author">{c.author}</span>
                            <time
                              className="comment-timestamp"
                              dateTime={c.created_at}
                              title={c.created_at}
                            >
                              {shortRelativeTime(c.created_at)}
                            </time>
                            <span
                              className={`comment-resolve-btn comment-resolve-btn--static${c.status === "resolved" ? " resolved" : ""}`}
                              title={c.status === "resolved" ? "Resolved" : undefined}
                              aria-hidden="true"
                            >
                              <IconCheck size={13} />
                            </span>
                          </div>
                          <p className="comment-body">{c.body}</p>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="comments-section-empty" data-testid="general-comments-empty">
                      No general comments yet.
                    </p>
                  )}
                  {!generalComposerOpen ? (
                    <button
                      type="button"
                      className="general-comment-trigger"
                      data-testid="general-comment-trigger"
                      aria-label="Add a general comment"
                      onClick={openGeneralComposer}
                    >
                      <IconSpeechBubblePlus />
                      Add a general comment
                    </button>
                  ) : (
                    <div className="general-comment-composer" role="form" aria-label="Add a general comment">
                      <p className="general-comment-composer-label">General comment</p>
                      <textarea
                        ref={generalTextareaRef}
                        className="general-comment-composer-input"
                        data-testid="general-comment-input"
                        placeholder="Share overall feedback about this prototype…"
                        maxLength={2000}
                        aria-label="General comment text"
                        value={generalBody}
                        onChange={(e) => setGeneralBody(e.target.value)}
                      />
                      {generalError && (
                        <p className="comments-error error" data-testid="general-comment-error">
                          {generalError}
                        </p>
                      )}
                      <div className="general-comment-composer-actions">
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm"
                          data-testid="general-comment-cancel"
                          onClick={cancelGeneralComposer}
                        >
                          Cancel
                        </button>
                        <button
                          type="button"
                          className="btn btn-accent btn-sm"
                          data-testid="general-comment-send"
                          disabled={!generalBody.trim() || generalPosting}
                          onClick={() => void submitGeneralComment()}
                        >
                          Send
                        </button>
                      </div>
                    </div>
                  )}
                </section>

                {/* Pinned section — element-anchored comments. Unchanged rendering
                    (CommentsPanel + its composer/resolve-dedup wiring, byte-for-byte
                    the same mount as before this ticket); only the header + wrapping
                    section + hideGeneralComments (to avoid double-rendering a general
                    comment inside this section) are new. */}
                <section
                  className="comments-section"
                  aria-label="Element-anchored comments"
                  data-testid="pinned-comments-section"
                >
                  <h3 className="comments-section-title">
                    <IconPinMarker />
                    Pinned
                    {pinnedOpenCount > 0 && (
                      <span
                        className="comments-section-count"
                        aria-label={`${pinnedOpenCount} open pinned comments`}
                      >
                        {pinnedOpenCount}
                      </span>
                    )}
                  </h3>
                  {/* C2a writable-anon comments. No prototypeId on this surface (minimum-
                      disclosure), so create routes via createCommentByToken(token);
                      canComment enables create while resolve/apply/ignore/delete stay
                      hidden (all gated on prototypeId). */}
                  <CommentsPanel
                    token={token as string}
                    canComment
                    viewerName={viewerName}
                    onCommentsLoaded={setServerCommentIds}
                    hideGeneralComments
                  />
                </section>
              </>
            )}
          </div>
        </aside>
      </div>
    </div>
  )
}
