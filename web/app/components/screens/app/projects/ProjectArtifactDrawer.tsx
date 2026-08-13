"use client"

// ── ProjectArtifactDrawer — in-place artifact viewer for the Projects screen ──
//
// AD-HOC (live-rig): opens an artifact BESIDE the group chat, no route change.
// The app's real ContentPanel/PrdPanelContent are bound to the workspace-root
// global ContentContext/NavigationContext (they read `useContent()`, not
// props) and their only entry point (`openPrdTab`) navigates to `/` — mounting
// a second instance here would fight the shell's own. So this is a thin,
// self-contained viewer that fetches the SAME authenticated GET routes every
// other artifact-open path calls (`GET /v1/prd/{id}`, `/v1/evidence/{id}`,
// `/v1/reports/{id}`) and renders their real bodies.
//
// AD-P13b (Gap 3 — main-chat parity, REUSE not fork): for a PRD artifact this
// adds the SAME Document / Evidence / Tickets segmentation the main-chat
// ContentPanel ships, over PROPS + the project's own scoped routes rather than
// the global ContentContext. The prototype launcher is the app's real
// props-based `DesignAgentLauncher` (takes `prdId`), and the Tickets view reads
// the SAME `storiesApi.getForPrd`/`generate` the main-chat Tickets tab uses.
// Nothing is synthesized client-side.
//
// Tokens only, from globals.css :root (no new palette).
import { useCallback, useEffect, useRef, useState } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import Link from "next/link"
import {
  ApiError,
  evidenceApi,
  prdApi,
  projectsApi,
  reportsApi,
  storiesApi,
  type ArtifactItem,
  type GeneratedStory,
  type ProjectArtifactType,
} from "../../../../lib/api"
import { sleepUntilNextPoll } from "../../../../lib/poll"
import { DesignAgentLauncher } from "../../../design-agent/DesignAgentLauncher"
import { PrdHtmlView, type PrdSaveStatus } from "../../../shared/PrdHtmlView"
import { PrdMarkdownEditor } from "../../../shared/PrdMarkdownEditor"
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

/** The three in-panel views a PRD artifact exposes — the same
 *  Evidence → PRD → Tickets pipeline the main-chat ContentPanel segments,
 *  reordered here to lead with the open document. */
type PrdView = "document" | "evidence" | "tickets"

/** Renders a resolved Body into the drawer's scroll region. Shared by the
 *  document view and the evidence view so both honor the markdown-or-HTML
 *  duality with one renderer. */
function BodyRender({ body }: { body: Body }) {
  if (body.kind === "loading") {
    return (
      <div className={styles.state} aria-busy="true">
        <div className={styles.skeletonLine} style={{ width: "60%" }} />
        <div className={styles.skeletonLine} style={{ width: "92%" }} />
        <div className={styles.skeletonLine} style={{ width: "80%" }} />
        <div className={styles.skeletonLine} style={{ width: "88%" }} />
      </div>
    )
  }
  if (body.kind === "markdown") {
    return (
      <article className={styles.doc}>
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{body.md}</ReactMarkdown>
      </article>
    )
  }
  if (body.kind === "html") {
    return (
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
    )
  }
  if (body.kind === "external") {
    return (
      <div className={styles.state}>
        <p className={styles.stateNote}>{body.note}</p>
        <Link className={styles.externalBtn} href={body.href} data-testid="project-artifact-drawer-open-canvas">
          Open the prototype canvas
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M7 17 17 7M8 7h9v9" />
          </svg>
        </Link>
      </div>
    )
  }
  return (
    <div className={styles.state}>
      <p className={styles.stateNote}>{body.note}</p>
    </div>
  )
}

/** In-panel Tickets view for a PRD (main-chat parity): reads the SAME
 *  `storiesApi.getForPrd` cache the ContentPanel Tickets tab reads first, and
 *  regenerates on demand via `storiesApi.generate(prdId)` → poll `getJob`. Per-
 *  PRD tickets ride with the (already-attached) PRD artifact — there is no
 *  separate ticket_set to attach here (that's the chat-insight path). */
function PrdTicketsView({ prdId }: { prdId: number }) {
  type State =
    | { kind: "loading" }
    | { kind: "ready"; stories: GeneratedStory[]; stale: boolean }
    | { kind: "generating" }
    | { kind: "empty" }
    | { kind: "error"; note: string }
  const [state, setState] = useState<State>({ kind: "loading" })
  const busyRef = useRef(false)

  const read = useCallback(async () => {
    try {
      const cache = await storiesApi.getForPrd(prdId)
      if (cache.status === "ready" && cache.stories.length > 0) {
        setState({ kind: "ready", stories: cache.stories, stale: !cache.fresh })
      } else if (cache.status === "generating") {
        setState({ kind: "generating" })
      } else {
        setState({ kind: "empty" })
      }
    } catch {
      setState({ kind: "error", note: "Couldn't load tickets for this PRD." })
    }
  }, [prdId])

  useEffect(() => {
    setState({ kind: "loading" })
    void read()
  }, [read])

  const generate = useCallback(async () => {
    if (busyRef.current) return
    busyRef.current = true
    setState({ kind: "generating" })
    try {
      const start = await storiesApi.generate(prdId)
      const startedAt = Date.now()
      let status = "generating"
      while (Date.now() - startedAt < 3 * 60 * 1000) {
        const job = await storiesApi.getJob(start.job_id)
        status = job.status
        if (status !== "generating") break
        await sleepUntilNextPoll(3000)
      }
      if (status !== "ready") {
        setState({ kind: "error", note: "That ticket run didn't finish. Try again." })
        return
      }
      await read()
    } catch {
      setState({ kind: "error", note: "That ticket run didn't come through. Try again." })
    } finally {
      busyRef.current = false
    }
  }, [prdId, read])

  return (
    <div className={styles.tickets} data-testid="project-artifact-drawer-tickets">
      {state.kind === "loading" ? (
        <div className={styles.state} aria-busy="true">
          <div className={styles.skeletonLine} style={{ width: "70%" }} />
          <div className={styles.skeletonLine} style={{ width: "90%" }} />
          <div className={styles.skeletonLine} style={{ width: "82%" }} />
        </div>
      ) : state.kind === "generating" ? (
        <div className={styles.state} aria-busy="true">
          <p className={styles.stateNote}>Breaking this PRD into tickets… this can take a minute.</p>
        </div>
      ) : state.kind === "error" ? (
        <div className={styles.state}>
          <p className={styles.stateNote}>{state.note}</p>
          <button type="button" className={styles.genBtn} onClick={generate}>
            Try again
          </button>
        </div>
      ) : state.kind === "empty" ? (
        <div className={styles.state}>
          <p className={styles.stateNote}>No tickets yet for this PRD.</p>
          <button type="button" className={styles.genBtn} onClick={generate} data-testid="project-drawer-generate-tickets">
            Generate tickets
          </button>
        </div>
      ) : (
        <>
          {state.stale ? (
            <div className={styles.ticketNote}>
              The PRD changed since these were written.
              <button type="button" className={styles.ticketNoteBtn} onClick={generate}>
                Regenerate
              </button>
            </div>
          ) : null}
          <ol className={styles.ticketList}>
            {state.stories.map((s, i) => (
              <li key={s.id ?? `${i}-${s.title}`} className={styles.ticketRow}>
                <span className={styles.ticketKey}>T-{i + 1}</span>
                <div className={styles.ticketMain}>
                  <div className={styles.ticketTitle}>{s.title}</div>
                  {s.user_story || s.body ? (
                    <div className={styles.ticketStory}>{s.user_story || s.body}</div>
                  ) : null}
                </div>
              </li>
            ))}
          </ol>
        </>
      )}
    </div>
  )
}

export function ProjectArtifactDrawer({
  artifact,
  projectId,
  onClose,
}: {
  artifact: ArtifactItem | null
  /** The project this drawer is scoped to — threaded so PRD sub-views
   *  (tickets/evidence/prototype) act only within this project's own,
   *  membership-gated routes. */
  projectId: number | string
  onClose: () => void
}) {
  const [body, setBody] = useState<Body>({ kind: "loading" })
  // Which in-panel view is active for a PRD artifact. Reset to "document" on
  // every open so a fresh artifact always lands on its own body.
  const [view, setView] = useState<PrdView>("document")
  // The PRD's evidence body (fetched lazily the first time the Evidence tab is
  // opened for this artifact) — the SAME markdown-or-HTML renderer as the doc.
  const [evidenceBody, setEvidenceBody] = useState<Body | null>(null)
  // Which artifact id the evidence has already been fetched for — a ref (not a
  // dep) so the fetch effect can't re-run itself: keying the fetch on
  // `evidenceBody` would cancel its own in-flight request the instant it set
  // the loading state.
  const evidenceFetchedFor = useRef<number | null>(null)
  // Inline-edit save status for the PRD Document view (AD-P13b) — surfaced in
  // the footer while a PRD is edited in place through the shared PrdHtmlView.
  const [saveStatus, setSaveStatus] = useState<PrdSaveStatus>("saved")

  useEscapeToClose(artifact != null, onClose)

  const isPrd = artifact?.type === "prd"
  const prdId = artifact?.type === "prd" ? artifact.open.prd_id : null

  useEffect(() => {
    setView("document")
    setEvidenceBody(null)
    evidenceFetchedFor.current = null
  }, [artifact])

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

  // Lazily fetch the PRD's evidence the first time the Evidence tab is opened.
  // Keyed off the PRD's own brief reference (the same (brief_id, insight_index)
  // pointer the main-chat Evidence tab loads from). A PRD with no brief context
  // shows a "no evidence" note rather than an empty pane.
  useEffect(() => {
    if (view !== "evidence" || artifact?.type !== "prd") return
    if (evidenceFetchedFor.current === artifact.id) return
    evidenceFetchedFor.current = artifact.id
    const briefId = artifact.open.brief_id
    const insightIndex = artifact.open.insight_index
    if (briefId == null || insightIndex == null) {
      setEvidenceBody({ kind: "empty", note: "This PRD has no research evidence behind it." })
      return
    }
    let cancelled = false
    setEvidenceBody({ kind: "loading" })
    evidenceApi
      .byInsight(briefId, insightIndex)
      .then((ev) => {
        if (cancelled) return
        if (!ev || !ev.payload_md) {
          setEvidenceBody({ kind: "empty", note: "This PRD has no research evidence behind it." })
          return
        }
        setEvidenceBody(bodyFromMd(ev.title || "Evidence", ev.payload_md, "This evidence page has no written body yet."))
      })
      .catch(() => {
        if (!cancelled) setEvidenceBody({ kind: "empty", note: "This PRD has no research evidence behind it." })
      })
    return () => {
      cancelled = true
    }
  }, [view, artifact])

  if (!artifact) return null
  const cfg = TYPE_BADGE[artifact.type]

  // AD-HOC (live-rig): renders as a LAYOUT COLUMN beside the chat, not a
  // modal overlay. So: no backdrop, no `aria-modal`, no focus trap;
  // `role="region"` (a labelled complementary reading pane), Escape closes it.
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

        {/* Main-chat parity: the Document / Evidence / Tickets segmentation,
            shown only for a PRD artifact (the pipeline anchor). Other artifact
            types keep their single-body view unchanged. */}
        {isPrd ? (
          <div className={styles.seg} role="tablist" aria-label="PRD views" data-testid="project-artifact-drawer-seg">
            {([
              { id: "document", label: "PRD" },
              { id: "evidence", label: "Evidence" },
              { id: "tickets", label: "Tickets" },
            ] as { id: PrdView; label: string }[]).map((t) => (
              <button
                key={t.id}
                type="button"
                role="tab"
                aria-selected={view === t.id}
                className={`${styles.segBtn} ${view === t.id ? styles.segBtnActive : ""}`}
                onClick={() => setView(t.id)}
                data-testid={`project-artifact-drawer-seg-${t.id}`}
              >
                {t.label}
              </button>
            ))}
          </div>
        ) : null}

        <div className={styles.body} data-testid="project-artifact-drawer-body">
          {!isPrd || view === "document" ? (
            // AD-P13b — one editor, two consumers: BOTH PRD body shapes are now
            // edited in place through the SAME shared primitives the main-chat
            // PRD tab uses — a v3 HTML PRD via `PrdHtmlView`, a markdown PRD via
            // `PrdMarkdownEditor` — each with a project-scoped, ★ IDOR-gated save
            // INJECTED (`projectsApi.savePrdContent`), never the global cross-
            // tenant-only `prdApi.update` path. The `draftScope` keeps the
            // drawer's local draft from colliding with the main-chat draft for
            // the same prd_id.
            isPrd && prdId != null && body.kind === "html" ? (
              <div className={styles.editorWrap} data-testid="project-artifact-drawer-editor">
                <PrdHtmlView
                  key={`${prdId}:${body.html.length}`}
                  html={body.html}
                  prdId={prdId}
                  title={body.title}
                  onStatus={setSaveStatus}
                  onSave={async (fullHtml, t) => {
                    await projectsApi.savePrdContent(projectId, prdId, t, fullHtml)
                  }}
                />
              </div>
            ) : isPrd && prdId != null && body.kind === "markdown" ? (
              <div className={styles.editorWrap} data-testid="project-artifact-drawer-md-editor">
                <PrdMarkdownEditor
                  key={`${prdId}:${body.md.length}`}
                  prdId={prdId}
                  title={body.title}
                  onStatus={setSaveStatus}
                  draftScope={`project-${projectId}`}
                  onSave={async (text, t) => {
                    await projectsApi.savePrdContent(projectId, prdId, t, text)
                  }}
                >
                  {/* Raw markdown source rendered as the editable body, so a
                      plain text edit round-trips through `innerText` without
                      flattening the document's markdown. */}
                  <div className={styles.doc} style={{ whiteSpace: "pre-wrap" }}>{body.md}</div>
                </PrdMarkdownEditor>
              </div>
            ) : (
              <BodyRender body={body} />
            )
          ) : view === "evidence" ? (
            <BodyRender body={evidenceBody ?? { kind: "loading" }} />
          ) : prdId != null ? (
            <PrdTicketsView prdId={prdId} />
          ) : null}
        </div>

        {/* Generate-prototype: the app's real props-based launcher (takes the
            PRD id). Shows the existing prototype's preview card / opens the
            live canvas; the generate trigger itself lives in the canvas flow.
            Rendered on the Document view of a PRD only. */}
        {isPrd && prdId != null && view === "document" ? (
          <div className={styles.footer} data-testid="project-artifact-drawer-proto">
            {body.kind === "html" || body.kind === "markdown" ? (
              <div className={styles.saveStatus} data-testid="project-artifact-drawer-save-status">
                <span
                  className={styles.saveDot}
                  style={{ background: saveStatus === "saving" ? "var(--accent)" : saveStatus === "unsaved" ? "var(--ink-4)" : "var(--green-d, var(--accent))" }}
                />
                {saveStatus === "saving" ? "Saving…" : saveStatus === "unsaved" ? "Unsaved edits — autosaving…" : "Saved · edits autosave"}
              </div>
            ) : null}
            <DesignAgentLauncher prdId={prdId} prdTitle={artifactTitle(artifact)} />
            <Link className={styles.externalBtn} href={`/prototype?prd=${prdId}`} data-testid="project-drawer-open-canvas">
              Open the prototype canvas
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M7 17 17 7M8 7h9v9" />
              </svg>
            </Link>
          </div>
        ) : null}
      </aside>
  )
}
