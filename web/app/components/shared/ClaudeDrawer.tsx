"use client"

import { useMemo } from "react"
import { useNavigation } from "../../context/NavigationContext"
import { useContent } from "../../context/ContentContext"
import { htmlPrdToPlainText } from "../../lib/htmlBrief"
import type { DetailState, PrdState } from "../../types/content"
import { IconClose, IconSparkle } from "./app-icons"

type CtxItem = {
  title: string
  preview: string
  defaultChecked: boolean
  tokens: number
}

const PROBLEM_KEYS = ["problem", "context", "background", "summary", "overview"]
const SOLUTION_KEYS = ["solution", "approach", "design", "implementation", "plan"]
const ACCEPTANCE_KEYS = ["acceptance", "success", "criteria", "test"]
const METRIC_KEYS = ["metric", "impact", "kpi", "measure"]

function classify(heading: string): "problem" | "solution" | "acceptance" | "metrics" | "other" {
  const h = heading.toLowerCase()
  if (PROBLEM_KEYS.some((k) => h.includes(k))) return "problem"
  if (SOLUTION_KEYS.some((k) => h.includes(k))) return "solution"
  if (ACCEPTANCE_KEYS.some((k) => h.includes(k))) return "acceptance"
  if (METRIC_KEYS.some((k) => h.includes(k))) return "metrics"
  return "other"
}

function estimateTokens(text: string): number {
  if (!text) return 0
  // Rough heuristic: ~1 token per 4 characters of English prose.
  return Math.max(1, Math.round(text.length / 4))
}

function formatTokens(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}K tok`
  return `${n} tok`
}

function truncate(s: string, n = 360): string {
  const t = s.trim()
  return t.length > n ? `${t.slice(0, n).trimEnd()}…` : t
}

function sectionGroups(prd: PrdState): Map<string, string[]> {
  const groups = new Map<string, string[]>()
  // v3 HTML PRD: no parsed sections — feed the page's plain text as one bundle.
  if (prd.html) {
    const text = htmlPrdToPlainText(prd.html)
    if (text) groups.set("PRD", text.split("\n"))
    return groups
  }
  let current = "Overview"
  for (const sec of prd.sections) {
    if (sec.type === "h2") {
      current = sec.text || "Section"
      if (!groups.has(current)) groups.set(current, [])
      continue
    }
    const lines = groups.get(current) ?? []
    if (sec.type === "p" && sec.text) lines.push(sec.text)
    if (sec.type === "ul" && sec.items?.length)
      lines.push(...sec.items.map((it) => `• ${it}`))
    groups.set(current, lines)
  }
  return groups
}

function buildContextItems(
  prd: PrdState | null,
  detail: DetailState | null,
): { items: CtxItem[]; instructionPlaceholder: string; total: number } {
  const items: CtxItem[] = []

  // Problem & context — prefer PRD problem-style sections, fall back to detail summary.
  const problemBuckets: string[] = []
  const solutionBuckets: string[] = []
  const acceptanceBuckets: string[] = []
  const metricsBuckets: string[] = []
  const otherBuckets: string[] = []

  if (prd) {
    for (const [heading, lines] of sectionGroups(prd)) {
      if (lines.length === 0) continue
      const block = `${heading}\n${lines.join("\n")}`
      switch (classify(heading)) {
        case "problem":
          problemBuckets.push(block)
          break
        case "solution":
          solutionBuckets.push(block)
          break
        case "acceptance":
          acceptanceBuckets.push(block)
          break
        case "metrics":
          metricsBuckets.push(block)
          break
        default:
          otherBuckets.push(block)
      }
    }
  }

  if (detail) {
    const detailParts: string[] = []
    if (detail.title) detailParts.push(detail.title)
    if (detail.summary) detailParts.push(detail.summary)
    if (detail.metrics.length > 0) {
      detailParts.push(
        detail.metrics
          .slice(0, 3)
          .map((m) => `${m.label}: ${m.value}`)
          .join(" · "),
      )
    }
    if (detailParts.length > 0) {
      problemBuckets.unshift(detailParts.join("\n"))
    }

    // Quote-style evidence rows feed an Evidence bundle context.
    const evidenceLines: string[] = []
    for (const sec of detail.evidenceSections) {
      if (sec.quoteRows?.length) {
        for (const q of sec.quoteRows) {
          evidenceLines.push(`${q.source}: "${q.quote}"`)
        }
      }
    }
    if (evidenceLines.length > 0) {
      otherBuckets.push(`Evidence quotes\n${evidenceLines.join("\n")}`)
    }
  }

  const pushItem = (
    title: string,
    buckets: string[],
    defaultChecked: boolean,
  ) => {
    if (buckets.length === 0) return
    const text = buckets.join("\n\n")
    items.push({
      title,
      preview: truncate(text),
      defaultChecked,
      tokens: estimateTokens(text),
    })
  }

  pushItem("Problem & context", problemBuckets, true)
  pushItem("Proposed solution", solutionBuckets, true)
  pushItem("Acceptance criteria + test plan", acceptanceBuckets, true)
  pushItem("Impact & metrics", metricsBuckets, true)
  pushItem("Additional PRD context", otherBuckets, false)

  const total = items.reduce((sum, it) => sum + it.tokens, 0)
  const subject = prd?.title || detail?.title || "this work"
  const instructionPlaceholder = `e.g. Scope ${subject}; reuse existing utilities and feature flags rather than introducing new ones.`
  return { items, instructionPlaceholder, total }
}

export function ClaudeDrawer() {
  const { activeDrawer, closeDrawers, showToast } = useNavigation()
  const { content } = useContent()

  const { items, instructionPlaceholder, total } = useMemo(
    () => buildContextItems(content.prd, content.detail),
    [content.prd, content.detail],
  )

  if (activeDrawer !== "claude") return null

  const subject = content.prd?.title || content.detail?.title || "the work"

  const handleSend = () => {
    closeDrawers()
    showToast(
      "Prototype generation started",
      "The coding agent is scoping the work — we'll ping Slack when the PR opens.",
      "Track progress →",
    )
  }

  return (
    <>
      <div className="drawer-overlay open" onClick={closeDrawers} />
      <aside className="drawer open">
        <div className="drawer-head">
          <h3 className="drawer-title">
            <span className="drawer-icon">
              <IconSparkle size={15} />
            </span>
            Generate Prototype
          </h3>
          <button type="button" className="drawer-close" onClick={closeDrawers} aria-label="Close">
            <IconClose size={18} />
          </button>
        </div>
        <div className="drawer-body">
          <p className="drawer-sub">
            Turn this PRD into a working prototype of{" "}
            <strong>{subject}</strong>. A coding agent scopes the work,
            implements it across the right files, and opens a review-ready PR
            on <strong>main</strong>.
          </p>

          {items.length === 0 ? (
            <div
              style={{
                padding: "16px 18px",
                border: "1px dashed var(--line)",
                borderRadius: 12,
                color: "var(--muted)",
                fontSize: 13,
              }}
            >
              No PRD loaded yet. Generate or open a PRD first, and the context
              package will populate from it.
            </div>
          ) : (
            items.map((it) => (
              <ContextSection
                key={it.title}
                title={it.title}
                size={formatTokens(it.tokens)}
                defaultChecked={it.defaultChecked}
                preview={it.preview}
              />
            ))
          )}

          <div style={{ marginTop: 16 }}>
            <label className="field-label">Instruction for Claude (optional)</label>
            <textarea className="textarea" placeholder={instructionPlaceholder} />
          </div>

          <div
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              color: "var(--muted)",
              marginTop: 14,
              padding: "10px 12px",
              background: "var(--surface-2)",
              borderRadius: 8,
              display: "flex",
              justifyContent: "space-between",
            }}
          >
            <span>Total context size</span>
            <span>
              <strong style={{ color: "var(--ink)" }}>~{formatTokens(total)}</strong>
              {total > 0 ? " · under limit" : ""}
            </span>
          </div>
        </div>
        <div className="drawer-foot">
          <span style={{ fontSize: 11.5, color: "var(--muted)" }}>
            Runs on connected GitHub repo
          </span>
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn" onClick={closeDrawers}>
              Cancel
            </button>
            <button
              type="button"
              className="btn btn-accent"
              onClick={handleSend}
              disabled={items.length === 0}
            >
              <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                <IconSparkle size={16} />
                Generate Prototype
              </span>
            </button>
          </div>
        </div>
      </aside>
    </>
  )
}

function ContextSection({
  title,
  size,
  preview,
  defaultChecked,
}: {
  title: string
  size: string
  preview: string
  defaultChecked: boolean
}) {
  return (
    <div className="ctx-section">
      <div className="ctx-section-head">
        <div className="ctx-section-title">
          <input type="checkbox" defaultChecked={defaultChecked} />
          {title}
        </div>
        <div className="ctx-section-size">{size}</div>
      </div>
      <div className="ctx-section-body">
        <div className="ctx-preview">{preview}</div>
      </div>
    </div>
  )
}
