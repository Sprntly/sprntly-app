"use client"

import { useState } from "react"
import { useNavigation } from "../../../context/NavigationContext"
import { useContent } from "../../../context/ContentContext"
import type { DetailEvidenceSection } from "../../../types/content"
import { prdApi } from "../../../lib/api"
import { markdownToPrdState } from "../../../lib/prd-adapter"
import { AppLayout } from "./AppLayout"
import { EmptyPane } from "../../shared/EmptyPane"

export function DetailScreen() {
  const { goTo, setAIBarValue, showToast } = useNavigation()
  const { content, setContent } = useContent()
  const d = content.detail
  const [generating, setGenerating] = useState(false)

  const handleGeneratePrd = async () => {
    if (!d?.meta) {
      showToast("Can't generate PRD", "Open this evidence from the brief first.")
      return
    }
    setGenerating(true)
    try {
      const start = await prdApi.generate(d.meta.briefId, d.meta.insightIndex)

      // Poll until ready (or failed). PRDs typically take ~3 min on first run;
      // already-generated PRDs return status='ready' immediately so we skip the loop.
      let prd = await prdApi.get(start.prd_id)
      const startedAt = Date.now()
      const MAX_MS = 6 * 60 * 1000 // 6 min ceiling
      while (prd.status === "generating" && Date.now() - startedAt < MAX_MS) {
        await new Promise((r) => setTimeout(r, 4000))
        prd = await prdApi.get(start.prd_id)
      }
      if (prd.status === "failed") {
        throw new Error(prd.error || "PRD generation failed on the backend")
      }
      if (prd.status !== "ready") {
        throw new Error("Timed out waiting for PRD")
      }
      setContent({ prd: markdownToPrdState(prd.payload_md) })
      goTo("prd")
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      showToast("PRD generation failed", msg.slice(0, 200))
    } finally {
      setGenerating(false)
    }
  }

  if (!d) {
    return (
      <AppLayout>
        <a className="detail-back" onClick={() => goTo("brief")}>
          ← Weekly brief
        </a>
        <EmptyPane
          title="No evidence loaded"
          hint="Set `content.detail` when the user opens a finding (title, metrics, evidence sections, optional HTML charts). Until then this screen stays empty."
          placeholders={3}
        />
      </AppLayout>
    )
  }

  return (
    <AppLayout>
      <a className="detail-back" onClick={() => goTo("brief")}>
        {d.backLabel}
      </a>

      <div className="detail-title-row">
        <div style={{ flex: 1 }}>
          <div className="finding-tag-row" style={{ marginBottom: 10 }}>
            {d.tags.map((t, i) => (
              <span key={i} className={t.className}>
                {t.label}
              </span>
            ))}
          </div>
          <h1 className="detail-title">{d.title}</h1>
        </div>
        <button
          type="button"
          className="ask-ai-btn"
          style={{ marginTop: 8 }}
          onClick={() =>
            setAIBarValue(`About this finding — summarize risks and next steps.`)
          }
        >
          <AskIcon />
        </button>
      </div>

      <p className="detail-summary">{d.summary}</p>

      {d.metrics.length > 0 ? (
        <div className="detail-grid">
          {d.metrics.map((m, i) => (
            <div key={i} className="detail-metric">
              <div className="detail-metric-label">{m.label}</div>
              <div className={`detail-metric-val ${m.valueClass ?? ""}`}>{m.value}</div>
              {m.note ? <div className="detail-metric-note">{m.note}</div> : null}
            </div>
          ))}
        </div>
      ) : null}

      {d.evidenceSections.map((section, i) => (
        <EvidenceSectionBlock key={i} section={section} />
      ))}

      {d.cta ? (
        <div className="detail-cta-card">
          <div className="detail-cta-strip"></div>
          <div className="detail-cta-inner">
            <div className="detail-cta-text">
              <h3 className="detail-cta-headline">{d.cta.headline}</h3>
              <p className="detail-cta-sub">{d.cta.sub}</p>
            </div>
            <div className="detail-cta-actions">
              <button type="button" className="btn" onClick={() => goTo("brief")}>
                {d.cta.dismissLabel}
              </button>
              <button
                type="button"
                className="btn btn-accent"
                onClick={handleGeneratePrd}
                disabled={generating}
              >
                {generating ? "Generating PRD…" : d.cta.primaryLabel}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </AppLayout>
  )
}

function EvidenceSectionBlock({ section }: { section: DetailEvidenceSection }) {
  return (
    <div className="evidence-section">
      <h2 className="evidence-title">{section.sectionTitle}</h2>
      {section.html ? (
        <div className="evidence-card">
          <div
            className="chart-box"
            dangerouslySetInnerHTML={{ __html: section.html }}
          />
        </div>
      ) : null}
      {section.quoteRows?.map((row, i) => (
        <div key={i} className="evidence-card">
          <EvidenceRow
            source={row.source}
            quote={row.quote}
            meta={row.meta}
            badge={row.badge}
          />
        </div>
      ))}
    </div>
  )
}

function EvidenceRow({
  source,
  quote,
  meta,
  badge,
}: {
  source: string
  quote: string
  meta: string[]
  badge?: string
}) {
  return (
    <div className="evidence-row">
      <div className="evidence-source">{source}</div>
      <div className="evidence-body">
        <div className="evidence-quote">{quote}</div>
        <div className="evidence-meta">
          {meta.map((m, i) => (
            <span key={i}>{m}</span>
          ))}
          {badge ? (
            <span
              style={{
                color: "var(--accent-ink)",
                background: "var(--accent-soft)",
                padding: "1px 6px",
                borderRadius: 3,
              }}
            >
              {badge}
            </span>
          ) : null}
        </div>
      </div>
    </div>
  )
}

function AskIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
      <path d="M12 8v4M12 15h0" strokeWidth="2.4" />
    </svg>
  )
}
