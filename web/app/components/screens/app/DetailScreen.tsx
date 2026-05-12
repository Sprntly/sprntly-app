"use client"

import { useEffect, useState } from "react"
import { useNavigation } from "../../../context/NavigationContext"
import { useContent } from "../../../context/ContentContext"
import type { DetailEvidenceSection } from "../../../types/content"
import { InlineChart } from "../../shared/InlineChart"
import { runPrdGeneration } from "../../../lib/runPrdGeneration"
import { pickDefaultDetailKey } from "../../../lib/brief-adapter"
import { AppLayout } from "./AppLayout"
import { EmptyPane } from "../../shared/EmptyPane"

export function DetailScreen() {
  const { goTo, setAIBarValue, expandAiPanel, showToast } = useNavigation()
  const { content, setContent } = useContent()
  const d = content.detail
  const [generating, setGenerating] = useState(false)

  useEffect(() => {
    if (content.detail) return
    const key = pickDefaultDetailKey(content.briefDetails ?? {})
    if (!key) return
    const next = content.briefDetails?.[key]
    if (next) setContent({ detail: next })
  }, [content.detail, content.briefDetails, setContent])

  const handleGeneratePrd = async () => {
    if (!d?.meta) {
      showToast("Can't generate PRD", "Open this evidence from the brief first.")
      return
    }
    setGenerating(true)
    try {
      const result = await runPrdGeneration(d.meta)
      if (!result.ok) {
        showToast("PRD generation failed", result.message.slice(0, 200))
        return
      }
      setContent({ prd: result.prd })
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
          hint="The Evidence view is built from the current weekly brief (`/v1/brief/current`). If the API returns no insights yet, or the app cannot reach your EC2 host (wrong NEXT_PUBLIC_API_URL, CORS, or auth), this stays empty. Open Weekly brief first, or confirm the brief payload includes an `insights` array."
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
          onClick={() => {
            expandAiPanel()
            setAIBarValue(`About this finding — summarize risks and next steps.`)
          }}
        >
          <AskIcon />
        </button>
      </div>

      <p className="detail-summary">{d.summary}</p>

      {d.metrics.length > 0 ? (
        <div className="impact-estimate">
          <div className="impact-estimate-eyebrow">Estimated impact</div>
          <div className="impact-estimate-row">
            {d.metrics.slice(0, 3).map((m, i) => (
              <div key={i} className="impact-estimate-item">
                <div className={`impact-estimate-val ${m.valueClass ?? ""}`}>{m.value}</div>
                <div className="impact-estimate-lbl">{m.label}</div>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {d.evidenceSections.map((section, i) => (
        <EvidenceSectionBlock key={i} section={section} />
      ))}

      {d.cta ? (
        <div className="detail-cta-actions detail-cta-actions-end">
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
      ) : null}
    </AppLayout>
  )
}

function EvidenceSectionBlock({ section }: { section: DetailEvidenceSection }) {
  return (
    <div className="evidence-section">
      <h2 className="evidence-title">{section.sectionTitle}</h2>
      {section.charts?.length
        ? section.charts.map((c, i) => (
            <div key={i} className="evidence-card">
              <InlineChart
                kind={c.kind}
                title={c.title}
                subtitle={c.subtitle}
                data={c.data}
              />
            </div>
          ))
        : null}
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
