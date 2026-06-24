"use client"

import { useEffect, useState } from "react"
import { roadmapDocApi, type RoadmapDoc } from "../../../lib/api"
import { renderInline } from "../../../lib/inline-md"
import { AppLayout } from "./AppLayout"
import { EmptyPane } from "../../shared/EmptyPane"

/**
 * Roadmap Doc artifact view (design scene onbstrat / `data-art-view="roadmapdoc"`).
 *
 * A clean, READ-ONLY "word-doc" render of the company's uploaded roadmap, read
 * from `GET /v1/company/roadmap-doc`. It mirrors the design's `rmdoc-*` layout:
 * a caption ("Your roadmap · uploaded …"), the doc title, then the extracted
 * text body. Per the design, this view has NO artifact tabs / share / footer
 * CTAs — it is a living document, not part of the PRD/Evidence/Tickets flow.
 *
 * The backend stores the original upload + the markdown text extracted by the
 * shared ingest converter; this view renders that extracted text. The structured
 * "bets / initiatives" decomposition in the mockup is illustrative — a real
 * upload renders its own extracted content faithfully in the same styling.
 */

type LoadState =
  | { kind: "loading" }
  | { kind: "empty" }
  | { kind: "error"; message: string }
  | { kind: "ready"; doc: RoadmapDoc }

// Split a heading line ("# Title" / "## Section") from the leading hashes.
function headingLevel(line: string): { level: number; text: string } | null {
  const m = /^(#{1,6})\s+(.*)$/.exec(line.trim())
  if (!m) return null
  return { level: m[1].length, text: m[2].trim() }
}

function relativeUpload(iso: string | null): string {
  if (!iso) return "uploaded just now"
  const then = new Date(iso).getTime()
  if (!Number.isFinite(then)) return "uploaded just now"
  const mins = Math.round((Date.now() - then) / 60000)
  if (mins < 1) return "uploaded just now"
  if (mins < 60) return `uploaded ${mins}m ago`
  const hrs = Math.round(mins / 60)
  if (hrs < 24) return `uploaded ${hrs}h ago`
  const days = Math.round(hrs / 24)
  return `uploaded ${days}d ago`
}

// Strip a file extension for a clean title.
function titleFromFilename(name: string): string {
  return name.replace(/\.[^.]+$/, "").trim() || "Your roadmap"
}

/**
 * Render the extracted markdown text in the rmdoc word-doc layout. Top-level
 * (`#`) and second-level (`##`) headings become section headers; bullet /
 * numbered list items become `.rmdoc-row`s; everything else is a paragraph.
 */
function RoadmapBody({ text }: { text: string }) {
  const lines = text.split(/\r?\n/)
  const blocks: React.ReactNode[] = []
  let rows: string[] = []

  const flushRows = (key: string) => {
    if (rows.length === 0) return
    const captured = rows
    rows = []
    blocks.push(
      <div className="rmdoc-init" key={key}>
        {captured.map((r, i) => (
          <div className="rmdoc-row" key={i}>
            <span className="rmdoc-rt">{renderInline(r)}</span>
          </div>
        ))}
      </div>,
    )
  }

  lines.forEach((raw, idx) => {
    const line = raw.trim()
    if (!line) {
      flushRows(`rows-${idx}`)
      return
    }
    const h = headingLevel(line)
    if (h) {
      flushRows(`rows-${idx}`)
      if (h.level <= 1) {
        blocks.push(
          <div className="rmdoc-strategy" key={`h-${idx}`}>
            {renderInline(h.text)}
          </div>,
        )
      } else {
        blocks.push(
          <div className="rmdoc-init-h" key={`h-${idx}`}>
            {renderInline(h.text)}
          </div>,
        )
      }
      return
    }
    const item = /^([-*+]|\d+[.)])\s+(.*)$/.exec(line)
    if (item) {
      rows.push(item[2])
      return
    }
    flushRows(`rows-${idx}`)
    blocks.push(
      <div className="rmdoc-summary" key={`p-${idx}`}>
        {renderInline(line)}
      </div>,
    )
  })
  flushRows("rows-end")

  return <>{blocks}</>
}

export function RoadmapDocScreen() {
  const [state, setState] = useState<LoadState>({ kind: "loading" })

  useEffect(() => {
    let cancelled = false
    roadmapDocApi
      .get()
      .then((doc) => {
        if (cancelled) return
        setState(doc ? { kind: "ready", doc } : { kind: "empty" })
      })
      .catch((e: unknown) => {
        if (cancelled) return
        setState({
          kind: "error",
          message: e instanceof Error ? e.message : String(e),
        })
      })
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <AppLayout>
      <div className="art-col" data-art-view="roadmapdoc">
        <div className="art-view av-roadmapdoc">
          {state.kind === "loading" && (
            <EmptyPane title="Loading your roadmap…" hint="" placeholders={3} />
          )}

          {state.kind === "empty" && (
            <EmptyPane
              title="No roadmap uploaded yet"
              hint="Upload your roadmap in onboarding (Strategy step) or Settings — Sprntly loads it in and pressure-tests it against your data."
              placeholders={0}
            />
          )}

          {state.kind === "error" && (
            <EmptyPane
              title="Couldn't load your roadmap"
              hint={state.message}
              placeholders={0}
            />
          )}

          {state.kind === "ready" && (
            <div className="rmdoc">
              <div className="rmdoc-cap">
                Your roadmap · {relativeUpload(state.doc.uploaded_at)}
              </div>
              <div className="rmdoc-h">{titleFromFilename(state.doc.filename)}</div>
              {state.doc.extracted_text.trim() ? (
                <RoadmapBody text={state.doc.extracted_text} />
              ) : (
                <div className="rmdoc-strategy">
                  We stored your roadmap but couldn&apos;t extract readable text
                  from it. It will still inform your briefs.
                </div>
              )}
              <div className="rmdoc-foot">
                <span className="lbl">Living document · read-only render</span>
              </div>
            </div>
          )}
        </div>
      </div>
    </AppLayout>
  )
}
