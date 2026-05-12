"use client"

import { useCallback, useMemo, useState } from "react"
import { useNavigation } from "../../../context/NavigationContext"
import { useContent } from "../../../context/ContentContext"
import { isBriefEmpty, type BriefFindingRow } from "../../../types/content"
import { runPrdGeneration } from "../../../lib/runPrdGeneration"
import { AppLayout } from "./AppLayout"
import { EmptyPane } from "../../shared/EmptyPane"

export function BriefScreen() {
  const { goTo, setAIBarValue, expandAiPanel, showToast } = useNavigation()
  const { content, setContent } = useContent()
  const { brief, briefDetails } = content

  const [prdBusyKey, setPrdBusyKey] = useState<string | null>(null)

  const openEvidenceFor = (detailKey: string | undefined) => {
    if (detailKey && briefDetails?.[detailKey]) {
      setContent({ detail: briefDetails[detailKey] })
    }
    goTo("detail")
  }

  const handleAskAI = (question: string) => {
    expandAiPanel()
    setAIBarValue(question)
  }

  const handleSecondaryCta = useCallback(
    async (f: BriefFindingRow) => {
      const key = f.detailKey
      if (f.secondaryCtaBehavior === "generate_prd") {
        if (!key || !briefDetails?.[key]) {
          showToast("Can't generate PRD", "Open evidence from a finding with a linked brief first.")
          return
        }
        const meta = briefDetails[key].meta
        setPrdBusyKey(key)
        try {
          const result = await runPrdGeneration(meta)
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
          setPrdBusyKey(null)
        }
        return
      }
      const prompts: Record<string, string> = {
        strategy:
          "Draft a short strategy memo: decision needed, options, recommendation, and risks for leadership review.",
        open_analysis:
          "Outline the next analysis steps to confirm root cause for this signal — data cuts, cohorts, and what would falsify our hypothesis.",
        set_alert:
          "Suggest monitoring triggers and review cadence for this signal — thresholds, owners, and when to escalate to Investigate or Fix.",
      }
      expandAiPanel()
      setAIBarValue(
        prompts[f.secondaryCtaBehavior] ??
          `Help me think through next steps for: ${f.title.slice(0, 120)}`,
      )
    },
    [briefDetails, expandAiPanel, goTo, setContent, showToast],
  )

  const empty = isBriefEmpty(brief)

  const flatFindings = useMemo(
    () => brief.sections.flatMap((s) => s.findings),
    [brief.sections],
  )

  return (
    <AppLayout mainClassName="main--reading">
      <div className="main-header">
        <div>
          <h1
            className="main-title"
            style={{
              fontSize: "clamp(15px, 1.65vw, 22px)",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
              lineHeight: 1.2,
            }}
          >
            Sprntly found three most important things to drive your goal
          </h1>
        </div>
      </div>

      {empty ? (
        <EmptyPane
          title="No findings in this brief"
          hint="When `/v1/brief/current` returns insights, the adapter maps them into finding cards (Build / Fix / Optimize) with evidence and PRD links."
          placeholders={4}
        />
      ) : (
        <div className="wb-doc">
          {brief.docHeader ? (
            <>
              <div className="wb-doc-header-grid">
                <div className="wb-doc-header-cell">
                  <div className="wb-doc-header-label">Company</div>
                  <div className="wb-doc-header-value">{brief.docHeader.company}</div>
                </div>
                <div className="wb-doc-header-cell">
                  <div className="wb-doc-header-label">Week of</div>
                  <div className="wb-doc-header-value">{brief.docHeader.weekOf}</div>
                </div>
                <div className="wb-doc-header-cell">
                  <div className="wb-doc-header-label">Product area</div>
                  <div className="wb-doc-header-value">{brief.docHeader.productArea}</div>
                </div>
              </div>
            </>
          ) : null}

          <div className="wb-card-stack">
            {flatFindings.map((f, i) => (
              <TemplateFindingCard
                key={f.detailKey ?? `${i}`}
                finding={f}
                prdBusy={prdBusyKey === f.detailKey}
                onViewEvidence={() => openEvidenceFor(f.detailKey)}
                onSecondary={() => void handleSecondaryCta(f)}
                onAskAI={() => handleAskAI(f.askQuestion)}
              />
            ))}
          </div>

          {brief.docFooter ? (
            <div className="wb-footer">
              <div className="wb-footer-grid">
                <div className="wb-footer-col">
                  <div className="wb-footer-head">Total at risk / upside</div>
                  <div className="wb-footer-body">{brief.docFooter.totalAtRiskOrUpside}</div>
                </div>
                <div className="wb-footer-col">
                  <div className="wb-footer-head">Recoverable (near-term)</div>
                  <div className="wb-footer-body">{brief.docFooter.recoverableRange}</div>
                </div>
                <div className="wb-footer-col">
                  <div className="wb-footer-head">Sources this week</div>
                  <div className="wb-footer-body">{brief.docFooter.sourcesThisWeek}</div>
                </div>
              </div>
            </div>
          ) : null}
        </div>
      )}
    </AppLayout>
  )
}

function TemplateFindingCard({
  finding: f,
  prdBusy,
  onViewEvidence,
  onSecondary,
  onAskAI,
}: {
  finding: BriefFindingRow
  prdBusy: boolean
  onViewEvidence: () => void
  onSecondary: () => void
  onAskAI: () => void
}) {
  const accent = f.actionAccent
  return (
    <article className={`wb-card wb-card--${accent}`}>
      <div className="wb-card-inner">
        <div className="wb-card-top">
          <span className="wb-card-action">{f.actionLabel}</span>
          <span className="wb-card-metric">{f.metricHighlight}</span>
        </div>
        <h3 className="wb-card-headline">{f.title}</h3>
        <p className="wb-card-body">{f.desc}</p>
        <p className="wb-card-signals">{f.signalLine}</p>
        <div className="wb-card-confidence">Confidence {(f.confidence ?? 0).toFixed(2)}</div>
        <div className="wb-card-actions">
          <button type="button" className="wb-card-link" onClick={onViewEvidence}>
            View evidence →
          </button>
          <div className="wb-card-actions-right">
            <button type="button" className="wb-card-ask" onClick={onAskAI}>
              Ask Sprntly
            </button>
            <button
              type="button"
              className={`wb-card-secondary wb-card-secondary--${accent}`}
              onClick={onSecondary}
              disabled={prdBusy && f.secondaryCtaBehavior === "generate_prd"}
            >
              {prdBusy && f.secondaryCtaBehavior === "generate_prd"
                ? "Generating…"
                : f.secondaryCtaLabel}
            </button>
          </div>
        </div>
      </div>
    </article>
  )
}
