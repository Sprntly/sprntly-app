"use client"

// ── ProjectArtifactDrawer — in-place artifact viewer for the Projects screen ──
//
// AD-HOC (live-rig): opens an artifact BESIDE the group chat, no route change.
// The app's real ContentPanel/PrdPanelContent are bound to the workspace-root
// global ContentContext/NavigationContext (they read `useContent()`, not
// props) and their only entry point (`openPrdTab`) navigates to `/` — mounting
// a second instance here would fight the shell's own. So this is a thin,
// self-contained READ-ONLY viewer that fetches the SAME authenticated GET
// routes every other artifact-open path calls (`GET /v1/prd/{id}`,
// `/v1/evidence/{id}`, `/v1/reports/{id}`) and renders their real bodies. No
// content is ever synthesized client-side: a type with no in-place body
// (prototype — a live canvas) shows a link out instead of a fabricated page.
//
// Tokens only, from globals.css :root (no new palette).
import { useEffect, useState } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import Link from "next/link"
import {
  ApiError,
  evidenceApi,
  prdApi,
  reportsApi,
  type ArtifactItem,
  type ProjectArtifactType,
} from "../../../../lib/api"
import { IconClose } from "../../../shared/app-icons"
import { useEscapeToClose } from "./useEscapeToClose"
import styles from "./ProjectArtifactDrawer.module.css"

/** Copied from ProjectDetailScreen's TYPE_BADGE (the app's real per-type
 *  semantic palette — not a new one). */
const TYPE_BADGE: Record<ProjectArtifactType, { label: string; bg: string; color: string }> = {
  prd: { label: "PRD", bg: "var(--accent-soft)", color: "var(--accent-ink)" },
  evidence: { label: "Evidence", bg: "#FEF0E6", color: "#B45309" },
  prototype: { label: "Prototype", bg: "#DBEAFE", color: "#1E40AF" },
  report: { label: "Report", bg: "#EDE9FE", color: "#6D28D9" },
  ticket_set: { label: "Tickets", bg: "var(--info-soft)", color: "var(--info)" },
}

type Body =
  | { kind: "loading" }
  | { kind: "markdown"; title: string; md: string }
  | { kind: "html"; title: string; html: string }
  | { kind: "external"; title: string; href: string; note: string }
  | { kind: "empty"; note: string }
  | { kind: "error"; note: string }

function artifactTitle(a: ArtifactItem): string {
  return a.title && a.title.trim().length > 0 ? a.title : TYPE_BADGE[a.type].label
}

/** The `/prototype` canvas link — the one artifact that has no read-and-render
 *  body (it's a live app, not a document). Mirrors the deep-link the app
 *  already ships. Null when the prototype has no PRD behind it. */
function prototypeHref(a: Extract<ArtifactItem, { type: "prototype" }>): string | null {
  return a.open.prd_id != null ? `/prototype?prd=${a.open.prd_id}` : null
}

/** Strip a leading/trailing ```html … ``` fence, mirroring the app's own
 *  `stripHtmlCodeFence` — a PRD/evidence body is sometimes wrapped in one. */
function stripFence(raw: string): string {
  return raw
    .replace(/^\s*```(?:html)?\s*/i, "")
    .replace(/\s*```\s*$/i, "")
    .trim()
}

/** Sprntly PRDs/evidence store their body in `payload_md` as EITHER markdown
 *  OR a self-contained HTML document (the app renders the latter via
 *  PrdHtmlView). Detect the HTML case so the drawer renders it in a sandboxed
 *  iframe rather than dumping raw tags as markdown text. */
function looksLikeHtml(body: string): boolean {
  const head = body.slice(0, 400).toLowerCase()
  return (
    head.includes("<!doctype html") ||
    head.includes("<html") ||
    (head.includes("<style") && head.includes("<")) ||
    /^<(div|section|main|article|body|head)[\s>]/.test(head)
  )
}

/** Shape a fetched `payload_md` into the right renderable Body, honoring the
 *  markdown-or-HTML duality. Empty bodies fall through to the caller. */
function bodyFromMd(title: string, rawMd: string, emptyNote: string): Body {
  const body = stripFence(rawMd)
  if (body.length === 0) return { kind: "empty", note: emptyNote }
  return looksLikeHtml(body)
    ? { kind: "html", title, html: body }
    : { kind: "markdown", title, md: body }
}

export function ProjectArtifactDrawer({
  artifact,
  onClose,
}: {
  artifact: ArtifactItem | null
  onClose: () => void
}) {
  const [body, setBody] = useState<Body>({ kind: "loading" })

  useEscapeToClose(artifact != null, onClose)

  useEffect(() => {
    if (!artifact) return
    let cancelled = false
    setBody({ kind: "loading" })

    const fail = (err: unknown) => {
      if (cancelled) return
      if (err instanceof ApiError && (err.status === 403 || err.status === 404)) {
        setBody({ kind: "error", note: "This artifact isn't available to you right now." })
      } else {
        setBody({ kind: "error", note: "Couldn't load this artifact. Try again." })
      }
    }

    if (artifact.type === "prd") {
      prdApi
        .get(artifact.open.prd_id)
        .then((prd) => {
          if (cancelled) return
          setBody(bodyFromMd(prd.title || artifactTitle(artifact), prd.payload_md ?? "", "This PRD has no written body yet."))
        })
        .catch(fail)
    } else if (artifact.type === "evidence") {
      evidenceApi
        .get(artifact.open.evidence_id)
        .then((ev) => {
          if (cancelled) return
          setBody(bodyFromMd(ev.title || artifactTitle(artifact), ev.payload_md ?? "", "This evidence page has no written body yet."))
        })
        .catch(fail)
    } else if (artifact.type === "report") {
      reportsApi
        .get(artifact.open.report_id)
        .then((rep) => {
          if (cancelled) return
          const html = (rep.html ?? "").trim()
          setBody(
            html.length > 0
              ? { kind: "html", title: rep.title || artifactTitle(artifact), html }
              : { kind: "empty", note: "This report has no rendered body yet." },
          )
        })
        .catch(fail)
    } else if (artifact.type === "prototype") {
      const href = prototypeHref(artifact)
      setBody(
        href
          ? {
              kind: "external",
              title: artifactTitle(artifact),
              href,
              note: "Prototypes open in the live canvas.",
            }
          : { kind: "empty", note: "This prototype has no canvas to open." },
      )
    } else {
      // ticket_set — a standalone set has no single-document body to render here.
      setBody({ kind: "empty", note: "Ticket sets open from the Tickets workspace." })
    }

    return () => {
      cancelled = true
    }
  }, [artifact])

  if (!artifact) return null
  const cfg = TYPE_BADGE[artifact.type]

  // AD-HOC (live-rig): renders as a LAYOUT COLUMN beside the chat, not a
  // modal overlay — the group chat stays a fully interactive column to the
  // left while this panel occupies the right region (replacing the rail).
  // So: no backdrop, no `aria-modal`, no focus trap; `role="region"` (a
  // labelled complementary reading pane), and Escape still closes it.
  return (
      <aside
        className={styles.drawer}
        role="region"
        aria-label={`${cfg.label} — ${artifactTitle(artifact)}`}
        data-testid="project-artifact-drawer"
      >
        <header className={styles.head}>
          <span className={styles.badge} style={{ background: cfg.bg, color: cfg.color }}>
            {cfg.label}
          </span>
          <span className={styles.title} data-testid="project-artifact-drawer-title">
            {artifactTitle(artifact)}
          </span>
          <button
            type="button"
            className={styles.close}
            onClick={onClose}
            aria-label="Close artifact"
            data-testid="project-artifact-drawer-close"
          >
            <IconClose size={16} title="Close" />
          </button>
        </header>

        <div className={styles.body} data-testid="project-artifact-drawer-body">
          {body.kind === "loading" ? (
            <div className={styles.state} aria-busy="true">
              <div className={styles.skeletonLine} style={{ width: "60%" }} />
              <div className={styles.skeletonLine} style={{ width: "92%" }} />
              <div className={styles.skeletonLine} style={{ width: "80%" }} />
              <div className={styles.skeletonLine} style={{ width: "88%" }} />
            </div>
          ) : body.kind === "markdown" ? (
            <article className={styles.doc}>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{body.md}</ReactMarkdown>
            </article>
          ) : body.kind === "html" ? (
            <iframe
              className={styles.reportFrame}
              srcDoc={body.html}
              title={body.title}
              // Matches the app's own PrdHtmlView/HtmlReportView: same-origin
              // rendering (so the doc's inline styles + @font-face paint) WITHOUT
              // allow-scripts — the body is rendered, never executed.
              sandbox="allow-same-origin"
              data-testid="project-artifact-drawer-report"
            />
          ) : body.kind === "external" ? (
            <div className={styles.state}>
              <p className={styles.stateNote}>{body.note}</p>
              <Link className={styles.externalBtn} href={body.href} data-testid="project-artifact-drawer-open-canvas">
                Open the prototype canvas
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M7 17 17 7M8 7h9v9" />
                </svg>
              </Link>
            </div>
          ) : (
            <div className={styles.state}>
              <p className={styles.stateNote}>{body.note}</p>
            </div>
          )}
        </div>
      </aside>
  )
}
