"use client"

import { useMemo, useState, type ReactNode } from "react"
import { useNavigation } from "../../../context/NavigationContext"
import { useContent } from "../../../context/ContentContext"
import { isBriefEmpty, type BriefState, type PastWeekRow } from "../../../types/content"
import { AppLayout } from "./AppLayout"
import { EmptyPane } from "../../shared/EmptyPane"

export function BriefScreen() {
  const { goTo, setAIBarValue, reviewPastOpen, setReviewPastOpen } = useNavigation()
  const { content, setContent } = useContent()
  const { brief, pastWeeks, briefDetails } = content

  const openEvidenceFor = (detailKey: string | undefined) => {
    if (detailKey && briefDetails?.[detailKey]) {
      setContent({ detail: briefDetails[detailKey] })
    }
    goTo("detail")
  }
  const [pastFilter, setPastFilter] = useState<"week" | "month" | "all">("week")

  const empty = isBriefEmpty(brief)
  const totalPastFindings = useMemo(
    () => pastWeeks.reduce((n, w) => n + w.findings.length, 0),
    [pastWeeks],
  )

  const handleAskAI = (question: string) => {
    setAIBarValue(question)
  }

  return (
    <AppLayout>
      <div className="main-header">
        <div>
          <h1 className="main-title">
            Weekly brief{" "}
            {brief.weekRange ? <span>— {brief.weekRange}</span> : null}
          </h1>
          <p className="main-sub">
            {brief.subline ??
              (empty
                ? "No brief loaded yet — connect data and run the weekly pipeline."
                : "")}
          </p>
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
          hint="When your worker returns ranked findings, map them into `content.brief.sections` (and optional impact + meta lines). The layout is already wired."
          placeholders={4}
        />
      ) : (
        <>
          <ImpactBanner brief={brief} />
          <BriefMeta brief={brief} />
          {brief.sections.map((section, si) => (
            <div key={si}>
              <BriefSectionHead
                title={
                  <>
                    {section.titlePrefix}
                    <span>{section.titleEmphasis}</span>
                  </>
                }
                subtotal={section.subtotal}
                subtotalClass={section.subtotalClass}
              />
              {section.findings.map((f, fi) => (
                <Finding
                  key={`${si}-${fi}`}
                  rank={f.rank}
                  tagType={f.tagType}
                  tagLabel={f.tagLabel}
                  impactLabel={f.impactLabel}
                  confidence={f.confidence}
                  title={f.title}
                  desc={f.desc}
                  impacts={f.impacts}
                  askQuestion={f.askQuestion}
                  onAskAI={handleAskAI}
                  onViewEvidence={() => openEvidenceFor(f.detailKey)}
                />
              ))}
            </div>
          ))}
        </>
      )}
    </AppLayout>
  )
}

function ImpactBanner({ brief }: { brief: BriefState }) {
  if (!brief.impactEyebrow && brief.impactStats.length === 0) return null
  return (
    <div className="brief-impact-banner">
      <div className="brief-impact-inner">
        {brief.impactEyebrow ? (
          <div className="brief-impact-eyebrow">{brief.impactEyebrow}</div>
        ) : null}
        <div className="brief-impact-headline">
          {brief.impactHeadlineLead}
          {brief.impactHeadlineEmphasis1 ? (
            <span>{brief.impactHeadlineEmphasis1}</span>
          ) : null}
          {brief.impactHeadlineMid}
          {brief.impactHeadlineEmphasis2 ? (
            <span>{brief.impactHeadlineEmphasis2}</span>
          ) : null}
          {brief.impactHeadlineTrail}
        </div>
        {brief.impactStats.length > 0 ? (
          <div className="brief-impact-stats">
            {brief.impactStats.map((s, i) => (
              <div key={i} className="brief-impact-stat">
                <strong className={s.valueClass ?? ""}>{s.value}</strong>
                {s.label}
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  )
}

function BriefMeta({ brief }: { brief: BriefState }) {
  if (!brief.metaLines.length) return null
  return (
    <div className="brief-meta">
      {brief.metaLines.map((line, i) => (
        <div key={i} className="brief-meta-item">
          {i === 0 ? <span className="brief-meta-dot"></span> : null}
          {line}
        </div>
      ))}
    </div>
  )
}

function BriefSectionHead({
  title,
  subtotal,
  subtotalClass,
}: {
  title: ReactNode
  subtotal: string
  subtotalClass: string
}) {
  return (
    <div className="brief-section-head">
      <h2 className="brief-section-title">{title}</h2>
      <div className="brief-section-rule"></div>
      <span className={`brief-section-subtotal ${subtotalClass}`}>{subtotal}</span>
    </div>
  )
}

function Finding({
  rank,
  tagType,
  tagLabel,
  impactLabel,
  confidence,
  title,
  desc,
  impacts,
  askQuestion,
  onAskAI,
  onViewEvidence,
}: {
  rank: number
  tagType: "double" | "new" | "fix"
  tagLabel: string
  impactLabel: string
  confidence: number
  title: string
  desc: string
  impacts: { label: string; value: string; positive?: boolean; negative?: boolean }[]
  askQuestion: string
  onAskAI: (q: string) => void
  onViewEvidence: () => void
}) {
  return (
    <div className="finding">
      <div className="finding-head">
        <div
          className="finding-num"
          title={`Sprntly AI Agent · Rank ${rank.toString().padStart(2, "0")}`}
        >
          <span className="finding-num-label">AI</span>
          <span className="finding-num-rank">{rank.toString().padStart(2, "0")}</span>
        </div>
        <div className="finding-body">
          <div className="finding-tag-row">
            <span className={`tag tag-${tagType}`}>{tagLabel}</span>
            <span className="tag tag-impact">{impactLabel}</span>
            <span className="tag tag-confidence">Confidence {confidence}</span>
          </div>
          <h3 className="finding-title">{title}</h3>
          <p className="finding-desc">{desc}</p>
          <div className="finding-bottom-row">
            <div className="finding-impact">
              {impacts.map((impact, i) => (
                <div key={i} className="impact-item">
                  <span className="impact-label">{impact.label}</span>
                  <span
                    className={`impact-val ${impact.positive ? "pos" : ""} ${impact.negative ? "neg" : ""}`}
                  >
                    {impact.value}
                  </span>
                </div>
              ))}
            </div>
            <div className="finding-actions-right">
              <button
                type="button"
                className="ask-ai-btn"
                title="Ask Sprntly about this finding"
                onClick={() => onAskAI(askQuestion)}
              >
                <AskIcon />
              </button>
              <button
                type="button"
                className="btn btn-primary btn-sm"
                onClick={onViewEvidence}
              >
                View evidence →
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
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
