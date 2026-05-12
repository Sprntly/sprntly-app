"use client"

import { useCallback, useMemo, useState } from "react"
import { useNavigation } from "../../../context/NavigationContext"
import { useContent } from "../../../context/ContentContext"
import { isBriefEmpty, type BriefFindingRow, type PastWeekRow } from "../../../types/content"
import { runPrdGeneration } from "../../../lib/runPrdGeneration"
import { AppLayout } from "./AppLayout"
import { EmptyPane } from "../../shared/EmptyPane"

export function BriefScreen() {
  const {
    goTo,
    setAIBarValue,
    expandAiPanel,
    reviewPastOpen,
    setReviewPastOpen,
    showToast,
  } = useNavigation()
  const { content, setContent } = useContent()
  const { brief, pastWeeks, briefDetails } = content

  const [prdBusyKey, setPrdBusyKey] = useState<string | null>(null)
  const [pastFilter, setPastFilter] = useState<"week" | "month" | "all">("week")

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
  const totalPastFindings = useMemo(
    () => pastWeeks.reduce((n, w) => n + w.findings.length, 0),
    [pastWeeks],
  )

  const flatFindings = useMemo(
    () => brief.sections.flatMap((s) => s.findings),
    [brief.sections],
  )

  return (
    <AppLayout>
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
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <div className="review-past-wrap">
            <button
              type="button"
              className="btn"
              disabled={pastWeeks.length === 0}
              onClick={(e) => {
                e.stopPropagation()
                if (pastWeeks.length === 0) return
                setReviewPastOpen(!reviewPastOpen)
              }}
            >
              <ClockIcon />
              Review past
              <ChevronDownIcon />
            </button>
            {reviewPastOpen && pastWeeks.length > 0 && (
              <ReviewPastMenu
                weeks={pastWeeks}
                filter={pastFilter}
                onFilterChange={setPastFilter}
                onItemClick={() => {
                  setReviewPastOpen(false)
                  goTo("detail")
                }}
                onViewAll={() => {
                  setReviewPastOpen(false)
                  goTo("past")
                }}
                totalShown={totalPastFindings}
              />
            )}
          </div>
          <button type="button" className="btn">
            <ShareIcon />
            Share in Slack
          </button>
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
          <hr className="wb-rule" />

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
              <hr className="wb-rule" />
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

function ReviewPastMenu({
  weeks,
  filter,
  onFilterChange,
  onItemClick,
  onViewAll,
  totalShown,
}: {
  weeks: PastWeekRow[]
  filter: "week" | "month" | "all"
  onFilterChange: (f: "week" | "month" | "all") => void
  onItemClick: () => void
  onViewAll: () => void
  totalShown: number
}) {
  return (
    <div className="review-past-menu open">
      <div className="review-past-head">
        <div className="review-past-title">Past briefs</div>
        <div className="review-past-sub">Status of every finding we&apos;ve surfaced</div>
      </div>
      <div className="review-past-filters">
        {(["week", "month", "all"] as const).map((f) => (
          <button
            key={f}
            type="button"
            className={`review-past-filter ${filter === f ? "active" : ""}`}
            onClick={() => onFilterChange(f)}
          >
            {f === "week" ? "Past weeks" : f === "month" ? "Past months" : "All time"}
          </button>
        ))}
      </div>
      <div className="review-past-body">
        {weeks.map((week) => (
          <ReviewPastGroup
            key={week.date + week.label}
            date={`${week.date} · ${week.label}`}
            count={week.findings.length}
            items={week.findings}
            onClick={onItemClick}
          />
        ))}
      </div>
      <div className="review-past-foot">
        <span>
          {totalShown > 0 ? `Showing ${totalShown} finding${totalShown === 1 ? "" : "s"}` : "No findings"}
        </span>
        <button type="button" className="btn btn-ghost btn-sm" onClick={onViewAll}>
          View all →
        </button>
      </div>
    </div>
  )
}

function ReviewPastGroup({
  date,
  count,
  items,
  onClick,
}: {
  date: string
  count: number
  items: PastWeekRow["findings"]
  onClick: () => void
}) {
  return (
    <div className="rp-group">
      <div className="rp-group-head">
        <div className="rp-group-date">{date}</div>
        <div className="rp-group-count">{count} findings</div>
      </div>
      {items.map((item, i) => (
        <div key={i} className="rp-item" onClick={onClick}>
          <div className="rp-item-title">{item.title}</div>
          <div className="rp-item-meta">
            <span className={`rp-status ${item.status}`}>
              {item.status === "in-progress"
                ? "In progress"
                : item.status === "logged"
                  ? "Logged"
                  : item.status === "in-motion"
                    ? "PRD drafted"
                    : item.status === "not-started"
                      ? "Not started"
                      : item.status === "shipped"
                        ? "Shipped"
                        : "Declined"}
            </span>
            <span className={`rp-item-sub ${item.positive ? "pos" : ""}`}>{item.sub}</span>
          </div>
        </div>
      ))}
    </div>
  )
}

function ClockIcon() {
  return (
    <svg
      width="13"
      height="13"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M12 8v4l3 3" />
      <circle cx="12" cy="12" r="10" />
    </svg>
  )
}

function ChevronDownIcon() {
  return (
    <svg width="10" height="10" viewBox="0 0 10 10" fill="currentColor">
      <path d="M5 7L1 3h8z" />
    </svg>
  )
}

function ShareIcon() {
  return (
    <svg
      width="13"
      height="13"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8" />
      <polyline points="16 6 12 2 8 6" />
      <line x1="12" y1="2" x2="12" y2="15" />
    </svg>
  )
}
