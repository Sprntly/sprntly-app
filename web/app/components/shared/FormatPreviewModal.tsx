/**
 * Preview a format: what Sprntly will produce, and how we mapped their shape
 * onto it.
 *
 * Two panes, because the left one alone is not enough to act on. A format that
 * didn't compile clean has an empty or partial skeleton — the diagnosis lives
 * entirely in the mapping panel on the right, which is why the preview is
 * available at EVERY compile status and why all three mapping blocks render
 * their empty state rather than being omitted. A silently missing block reads
 * as "nothing to report" when it means "we have no data".
 *
 * The rendered skeleton is model output, so it goes in an iframe with
 * `sandbox=""` — no scripts, and NOT `allow-same-origin`. PrdHtmlView
 * (`:312`) needs same-origin because it autosaves and measures a
 * contenteditable document; a read-only preview needs neither, so it takes the
 * stricter posture. The iframe is not keyboard-reachable content, and the
 * mapping panel is its text equivalent — which is the other reason that panel
 * always renders.
 *
 * This dialog carries a REAL focus trap plus Escape and focus-return. No other
 * modal in the repo traps today; that is a house-wide decision about small
 * confirms, and this is a large two-pane surface where tabbing out into the
 * page behind it strands the user in content they can't see.
 */
"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import {
  ApiError,
  artifactTemplatesApi,
  type ArtifactTemplatePreview,
  type SectionForm,
  type SectionMap,
} from "../../lib/api"
import { translateCompileNotes } from "../../lib/compileNotes"

/** How a section is written, in plain words. Closed set, validated server-side
 *  at compile time so one concept never gets two labels. */
const FORM_LABEL: Record<SectionForm, string> = {
  prose: "Prose",
  bullets: "Bullets",
  table: "Table",
  stories: "User stories",
}

const EMPTY_MAP: SectionMap = {
  sections: [],
  unmapped_house: [],
  extra_sections: [],
}

/** `{{placeholder}}` → visible ghost text, so the skeleton reads as a form
 *  waiting to be filled rather than as a broken document.
 *
 *  Splits on TAGS first and only rewrites the text between them: rewriting
 *  blind would turn `<img alt="{{title}}">` into mangled markup. The iframe is
 *  `sandbox=""` so nothing injected here can execute; this only has to be
 *  well-formed, not trusted. Pure → unit-testable. */
export function ghostHtmlPlaceholders(body: string): string {
  return body
    .split(/(<[^>]*>)/)
    .map((chunk) =>
      chunk.startsWith("<")
        ? chunk
        : chunk.replace(
            /\{\{[^{}]*\}\}/g,
            (m) => `<span class="afmt-ph">${m}</span>`,
          ),
    )
    .join("")
}

/** The stylesheet the preview iframe carries. Deliberately appended AFTER the
 *  document rather than spliced into its `<style>` element: that element is an
 *  empty marker the backend fills with prd.css at generation time
 *  (app/html_style.py:39), and a preview must not teach anyone it is already
 *  populated. */
const PREVIEW_IFRAME_CSS = `
<style>
  body { font-family: ui-sans-serif, system-ui, sans-serif; color: #1B2A22;
         line-height: 1.5; margin: 0; padding: 20px; }
  .afmt-ph { color: #AAB3AE; font-style: italic; }
  table { border-collapse: collapse; }
  td, th { border: 1px solid #DFE4E1; padding: 4px 8px; text-align: left; }
</style>`

/** Wrap the compiled skeleton for display. */
export function previewSrcDoc(body: string): string {
  return `${ghostHtmlPlaceholders(body)}${PREVIEW_IFRAME_CSS}`
}

/** Markdown placeholders get the same ghost treatment. react-markdown runs
 *  without rehype-raw here (repo-wide default), so a `<span>` in the source
 *  would print verbatim — the wrapping is done in React, per text node. */
function ghostText(children: React.ReactNode): React.ReactNode {
  if (typeof children === "string") {
    const parts = children.split(/(\{\{[^{}]*\}\})/g)
    if (parts.length === 1) return children
    return parts.map((part, i) =>
      /^\{\{[^{}]*\}\}$/.test(part) ? (
        <span className="afmt-ph" key={i}>
          {part}
        </span>
      ) : (
        part
      ),
    )
  }
  if (Array.isArray(children)) return children.map((c) => ghostText(c))
  return children
}

export type FormatPreviewModalViewProps = {
  open: boolean
  /** The format's name — known from the row, so the dialog can title itself the
   *  instant it opens rather than after the fetch. */
  name: string
  /** The fetched preview. Null while the fetch is in flight. */
  detail: ArtifactTemplatePreview | null
  preview: { format: "html" | "markdown"; body: string } | null
  loading: boolean
  /** Plain-language; never a raw server detail. */
  error: string | null
  /** The row is gone (404). Retrying can't help, so only Close is offered and
   *  the copy is not-found — NEVER "you don't have access". */
  notFound: boolean
  /** ready && admin && type live && not already active. */
  canActivate: boolean
  /** Rendered IN PLACE OF the activate button, never as a disabled one. */
  activateBlockedReason: string | null
  activating: boolean
  onActivate: () => void
  onRetry: () => void
  onClose: () => void
}

export function FormatPreviewModalView({
  open,
  name,
  detail,
  preview,
  loading,
  error,
  notFound,
  canActivate,
  activateBlockedReason,
  activating,
  onActivate,
  onRetry,
  onClose,
}: FormatPreviewModalViewProps) {
  const dialogRef = useRef<HTMLDivElement>(null)
  const openerRef = useRef<Element | null>(null)

  // Focus lands inside on open and goes back to the invoking control on close.
  useEffect(() => {
    if (!open) return
    openerRef.current = document.activeElement
    const first = dialogRef.current?.querySelector<HTMLElement>(
      "button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])",
    )
    first?.focus()
    const opener = openerRef.current
    return () => {
      if (opener instanceof HTMLElement) opener.focus()
    }
  }, [open])

  // Escape closes; Tab cycles within the dialog. Both are captured on the
  // dialog itself so a keystroke inside the sandboxed iframe (which cannot be
  // focused anyway) is never relied on.
  const onKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation()
        onClose()
        return
      }
      if (e.key !== "Tab") return
      const focusables = Array.from(
        dialogRef.current?.querySelectorAll<HTMLElement>(
          "button:not([disabled]), [href], input:not([disabled]), select, textarea, [tabindex]:not([tabindex='-1'])",
        ) ?? [],
      )
      if (focusables.length === 0) return
      const first = focusables[0]
      const last = focusables[focusables.length - 1]
      const active = document.activeElement
      if (e.shiftKey && active === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && active === last) {
        e.preventDefault()
        first.focus()
      }
    },
    [onClose],
  )

  if (!open) return null

  const map = detail?.section_map ?? EMPTY_MAP
  const sections = [...(map.sections ?? [])].sort(
    (a, b) => (a.order ?? 0) - (b.order ?? 0),
  )
  // `map.unmapped_house` / `map.extra_sections` are deliberately not read: the
  // two blocks that rendered them are gone (see below the section table). The
  // API still returns them and the type still declares them — this component
  // just has nothing to say about them any more.
  const notes = translateCompileNotes(detail?.compile_notes)

  return (
    <div
      className="modal-overlay open"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
      aria-hidden={false}
    >
      <div
        ref={dialogRef}
        className="modal modal-lg afmt-preview"
        role="dialog"
        aria-modal="true"
        aria-labelledby="afmt-preview-title"
        onKeyDown={onKeyDown}
      >
        <div className="modal-head">
          <div className="modal-head-text">
            <h2 className="modal-title" id="afmt-preview-title">
              Preview — {name}
            </h2>
            <p className="modal-sub">
              This is the shape Sprntly will write into. The text is
              placeholder; the structure is real.
            </p>
          </div>
          <button
            type="button"
            className="modal-close"
            onClick={onClose}
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        <div className="modal-body">
          {error ? (
            <p className="settings-msg settings-msg-error" role="alert">
              {error}
            </p>
          ) : null}

          <div className="afmt-preview-panes">
            {/* ── Left: what a document will look like ────────────────── */}
            <section className="afmt-preview-pane" aria-label="What Sprntly will produce">
              <h3 className="afmt-preview-h">What Sprntly will produce</h3>
              {loading ? (
                <div className="afmt-skel afmt-skel--doc" aria-hidden />
              ) : preview && preview.body.trim() ? (
                preview.format === "html" ? (
                  <iframe
                    className="afmt-preview-frame"
                    title={`Preview of ${name}`}
                    // sandbox="" — no scripts, no same-origin. A read-only
                    // preview needs neither, so it takes the stricter posture.
                    sandbox=""
                    srcDoc={previewSrcDoc(preview.body)}
                  />
                ) : (
                  <div className="afmt-preview-md">
                    <ReactMarkdown
                      remarkPlugins={[remarkGfm]}
                      components={{
                        p: ({ children }) => <p>{ghostText(children)}</p>,
                        li: ({ children }) => <li>{ghostText(children)}</li>,
                        td: ({ children }) => <td>{ghostText(children)}</td>,
                        th: ({ children }) => <th>{ghostText(children)}</th>,
                        h1: ({ children }) => <h1>{ghostText(children)}</h1>,
                        h2: ({ children }) => <h2>{ghostText(children)}</h2>,
                        h3: ({ children }) => <h3>{ghostText(children)}</h3>,
                      }}
                    >
                      {preview.body}
                    </ReactMarkdown>
                  </div>
                )
              ) : (
                <p className="afmt-preview-empty">
                  We couldn&apos;t build a preview from this format yet.
                </p>
              )}
            </section>

            {/* ── Right: how their shape maps onto ours ───────────────── */}
            <section className="afmt-preview-pane" aria-label="How we mapped your format">
              <h3 className="afmt-preview-h">How we mapped your format</h3>

              {loading ? (
                <div className="afmt-skel afmt-skel--map" aria-hidden />
              ) : (
                <>
                  {/* Every note, never truncated — the row shows the first and
                      sends the rest here. */}
                  {notes.length > 0 ? (
                    <div className="afmt-preview-block">
                      <h4 className="afmt-preview-bh">What we couldn&apos;t place</h4>
                      <ul className="afmt-preview-notes">
                        {notes.map((n) => (
                          <li key={n}>{n}</li>
                        ))}
                      </ul>
                    </div>
                  ) : null}

                  <div className="afmt-preview-block">
                    <h4 className="afmt-preview-bh">Section by section</h4>
                    {sections.length > 0 ? (
                      <div
                        className="afmt-map-scroll"
                        role="region"
                        aria-label="Section mapping"
                        tabIndex={0}
                      >
                        <table className="afmt-map">
                          <thead>
                            <tr>
                              <th>Your section</th>
                              <th>What goes there</th>
                              <th>Written as</th>
                            </tr>
                          </thead>
                          <tbody>
                            {sections.map((s) => (
                              <tr key={s.id || `${s.customer}-${s.order}`}>
                                <td>{s.customer}</td>
                                <td>{s.house}</td>
                                <td>{FORM_LABEL[s.form] ?? "Prose"}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ) : (
                      <p className="afmt-preview-empty">
                        We don&apos;t have a section-by-section map for this
                        format.
                      </p>
                    )}
                  </div>

                  {/* The "Added by Sprntly" and "Yours, kept" blocks used to
                      sit here and are deliberately gone.
                      "Added by Sprntly" described behaviour that no longer
                      exists: the compiler stopped grafting house sections into
                      a format with no slot for them
                      (prd-template-compile-v2), so the list it rendered was
                      either empty or a description of something the product
                      does not do.
                      "Yours, kept" said that sections with no Sprntly
                      equivalent are kept — which is now simply what the whole
                      preview means. Its own block re-stated the section table
                      above it in prose.
                      `section_map` still CARRIES both arrays; nothing renders
                      them, so a future surface that wants them has the data. */}
                </>
              )}
            </section>
          </div>
        </div>

        <div className="modal-foot">
          {error && !notFound ? (
            <button type="button" className="btn btn-sm" onClick={onRetry}>
              Try again
            </button>
          ) : null}
          {canActivate ? (
            <button
              type="button"
              className="btn btn-sm btn-primary"
              onClick={onActivate}
              disabled={activating}
            >
              {activating ? "Switching…" : "Use this format"}
            </button>
          ) : activateBlockedReason ? (
            // Replaced by the reason, never left as a disabled button nobody
            // can explain.
            <p className="afmt-blocked" role="note">
              {activateBlockedReason}
            </p>
          ) : null}
          <button type="button" className="btn btn-sm" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  )
}

// ───────────────────── Hooks-wired wrapper ─────────────────────

type Props = {
  /** Null closes the dialog. Carries the row so the title and the activation
   *  gate are correct before the preview fetch lands. */
  templateId: string | null
  name: string
  canActivate: boolean
  activateBlockedReason: string | null
  activating: boolean
  onActivate: () => void
  /** The row is gone server-side — the section drops it from the list. */
  onNotFound: (id: string) => void
  onClose: () => void
}

export function FormatPreviewModal({
  templateId,
  name,
  canActivate,
  activateBlockedReason,
  activating,
  onActivate,
  onNotFound,
  onClose,
}: Props) {
  const [detail, setDetail] = useState<ArtifactTemplatePreview | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notFound, setNotFound] = useState(false)
  const [attempt, setAttempt] = useState(0)

  // `onNotFound` is read through a ref rather than listed as a dependency: the
  // section passes an inline closure, so depending on it would re-fetch the
  // preview on every parent render.
  const onNotFoundRef = useRef(onNotFound)
  onNotFoundRef.current = onNotFound

  useEffect(() => {
    if (!templateId) return
    let cancelled = false
    setDetail(null)
    setError(null)
    setNotFound(false)
    setLoading(true)
    artifactTemplatesApi
      .preview(templateId)
      .then((payload) => {
        if (!cancelled) setDetail(payload)
      })
      .catch((e: unknown) => {
        if (cancelled) return
        // 404 renders as NOT FOUND, full stop — never "you don't have
        // access". The row is already listed to this caller, so there is no
        // existence left to protect, and the wrong word here would tell a
        // teammate their own company's format was hidden from them.
        if (e instanceof ApiError && e.status === 404) {
          setNotFound(true)
          setError("That format isn't here anymore.")
          onNotFoundRef.current(templateId)
          return
        }
        setError("We couldn't load this preview.")
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [templateId, attempt])

  return (
    <FormatPreviewModalView
      open={templateId != null}
      name={name}
      detail={detail}
      preview={
        detail
          ? { format: detail.format ?? "markdown", body: detail.body ?? "" }
          : null
      }
      loading={loading}
      error={error}
      notFound={notFound}
      canActivate={canActivate && !loading && error == null}
      activateBlockedReason={activateBlockedReason}
      activating={activating}
      onActivate={onActivate}
      onRetry={() => setAttempt((n) => n + 1)}
      onClose={onClose}
    />
  )
}
