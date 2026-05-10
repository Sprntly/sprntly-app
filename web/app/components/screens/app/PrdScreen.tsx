"use client"

import type { CSSProperties, ReactNode } from "react"
import { useNavigation } from "../../../context/NavigationContext"
import { useContent } from "../../../context/ContentContext"
import type { PrdChartDatum, PrdChartKind, PrdState } from "../../../types/content"
import { AppLayout } from "./AppLayout"
import { EmptyPane } from "../../shared/EmptyPane"
import {
  IconCheck,
  IconCopy,
  IconGrid,
  IconLinkInsert,
  IconListBullet,
  IconMail,
  IconRedo,
  IconUndo,
} from "../../shared/app-icons"

export function PrdScreen() {
  const { goTo, openModal, shareMenuOpen, setShareMenuOpen, showToast } =
    useNavigation()
  const { content } = useContent()
  const prd = content.prd

  const handleShare = (type: "email" | "slack" | "link") => {
    setShareMenuOpen(false)
    const messages = {
      email: {
        title: "Opening email draft",
        sub: "Your email client will open with the PRD attached.",
      },
      slack: {
        title: "Posted to Slack",
        sub: "PRD shared in #product. Your team can react & comment inline.",
      },
      link: {
        title: "Link copied",
        sub: "Anyone at sprntly.ai with the link can view this PRD.",
      },
    }
    const msg = messages[type]
    showToast(msg.title, msg.sub)
  }

  return (
    <AppLayout mainStyle={{ maxWidth: 900 }}>
      <a className="detail-back" onClick={() => goTo("detail")}>
        ← Back to evidence
      </a>

      <div className="prd-frame">
        <PrdToolbar hasDoc={!!prd} />
        {prd ? (
          <div
            className="prd-body"
            contentEditable
            spellCheck={false}
            suppressContentEditableWarning
          >
            <div className="prd-meta">{prd.metaLine}</div>
            <h1 className="prd-title">{prd.title}</h1>
            <PrdSections sections={prd.sections} />
          </div>
        ) : (
          <div className="prd-body" style={{ minHeight: 280 }}>
            <EmptyPane
              title="No PRD draft loaded"
              hint="When your LLM generates a mini-PRD, assign `content.prd` with `metaLine`, `title`, and `sections` (h2 / p / ul blocks). Toolbar actions stay available for future wiring."
              placeholders={0}
            />
          </div>
        )}

        <div className="prd-foot">
          <div className="prd-foot-left">
            <button type="button" className="btn btn-ghost btn-sm" disabled={!prd}>
              Save as draft
            </button>
          </div>
          <div className="prd-foot-right">
            <div style={{ position: "relative" }}>
              <button
                type="button"
                className="btn"
                disabled={!prd}
                onClick={(e) => {
                  e.stopPropagation()
                  if (!prd) return
                  setShareMenuOpen(!shareMenuOpen)
                }}
              >
                Share
                <svg width="10" height="10" viewBox="0 0 10 10" fill="currentColor">
                  <path d="M5 7L1 3h8z" />
                </svg>
              </button>
              {shareMenuOpen && prd && (
                <div className="share-menu open">
                  <ShareMenuItem
                    icon={<IconMail size={14} />}
                    title="Email"
                    desc="Send to teammates or stakeholders"
                    onClick={() => handleShare("email")}
                  />
                  <ShareMenuItem
                    icon={<span style={{ fontWeight: 700, fontSize: 10 }}>Sl</span>}
                    iconStyle={{ background: "#4A154B", color: "#fff" }}
                    title="Slack"
                    desc="Post to a channel"
                    onClick={() => handleShare("slack")}
                  />
                  <div className="share-menu-divider" />
                  <ShareMenuItem
                    icon={<IconCopy size={14} />}
                    title="Copy link"
                    desc="Viewable by your team"
                    onClick={() => handleShare("link")}
                  />
                </div>
              )}
            </div>
            <button
              type="button"
              className="btn btn-accent"
              disabled={!prd}
              onClick={() => prd && openModal("approve")}
            >
              <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                <IconCheck size={16} />
                Approve & next step
              </span>
            </button>
          </div>
        </div>
      </div>
    </AppLayout>
  )
}

function PrdSections({
  sections,
}: {
  sections: PrdState["sections"]
}) {
  return (
    <>
      {sections.map((block, i) => {
        if (block.type === "h2") {
          return (
            <h2 key={i} className="prd-h2">
              {block.text}
            </h2>
          )
        }
        if (block.type === "p") {
          return (
            <p key={i}>{block.text}</p>
          )
        }
        if (block.type === "ul" && block.items) {
          return (
            <ul key={i}>
              {block.items.map((li, j) => (
                <li key={j}>{li}</li>
              ))}
            </ul>
          )
        }
        if (block.type === "table") {
          return (
            <table key={i} className="prd-table">
              <thead>
                <tr>
                  {block.headers.map((h, j) => (
                    <th key={j}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {block.rows.map((row, j) => (
                  <tr key={j}>
                    {row.map((cell, k) => (
                      <td key={k}>{cell}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          )
        }
        if (block.type === "chart") {
          return (
            <PrdChart
              key={i}
              kind={block.kind}
              title={block.title}
              subtitle={block.subtitle}
              data={block.data}
            />
          )
        }
        return null
      })}
    </>
  )
}

function toNum(v: number | string): number {
  if (typeof v === "number") return v
  const m = String(v).replace(/[^\d.\-]/g, "")
  const n = parseFloat(m)
  return Number.isFinite(n) ? n : 0
}

function fmtVal(v: number | string): string {
  return typeof v === "string" ? v : String(v)
}

const CHART_COLORS = [
  "#5B7FFF",
  "#6FCF97",
  "#F2994A",
  "#BB6BD9",
  "#56CCF2",
  "#EB5757",
  "#F2C94C",
  "#27AE60",
]

function PrdChart({
  kind,
  title,
  subtitle,
  data,
}: {
  kind: PrdChartKind
  title?: string
  subtitle?: string
  data: PrdChartDatum[]
}) {
  return (
    <figure className={`prd-chart prd-chart-${kind}`}>
      {title ? <figcaption className="prd-chart-title">{title}</figcaption> : null}
      {subtitle ? <div className="prd-chart-sub">{subtitle}</div> : null}
      <div className="prd-chart-body">
        {kind === "bar" ? <BarChart data={data} /> : null}
        {kind === "line" ? <LineChart data={data} /> : null}
        {kind === "pie" ? <PieChart data={data} /> : null}
        {kind === "stat" ? <StatChart data={data} /> : null}
      </div>
    </figure>
  )
}

function BarChart({ data }: { data: PrdChartDatum[] }) {
  const max = Math.max(...data.map((d) => toNum(d.value)), 1)
  return (
    <div className="prd-bars">
      {data.map((d, i) => {
        const pct = (toNum(d.value) / max) * 100
        return (
          <div key={i} className="prd-bar-row">
            <div className="prd-bar-label">{d.label}</div>
            <div className="prd-bar-track">
              <div
                className="prd-bar-fill"
                style={{ width: `${pct.toFixed(1)}%`, background: CHART_COLORS[i % CHART_COLORS.length] }}
              />
            </div>
            <div className="prd-bar-val">{fmtVal(d.value)}</div>
          </div>
        )
      })}
    </div>
  )
}

function LineChart({ data }: { data: PrdChartDatum[] }) {
  const w = 560
  const h = 180
  const padL = 40
  const padR = 14
  const padT = 14
  const padB = 30
  const innerW = w - padL - padR
  const innerH = h - padT - padB
  const values = data.map((d) => toNum(d.value))
  const max = Math.max(...values, 1)
  const min = Math.min(...values, 0)
  const range = max - min || 1
  const n = data.length
  const x = (i: number) => padL + (i * innerW) / Math.max(n - 1, 1)
  const y = (v: number) => padT + innerH - ((v - min) / range) * innerH
  const points = data.map((d, i) => `${x(i).toFixed(1)},${y(toNum(d.value)).toFixed(1)}`).join(" ")
  const ticks = 4
  const yTicks = Array.from({ length: ticks + 1 }, (_, k) => min + (range * k) / ticks)
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="prd-line" preserveAspectRatio="xMidYMid meet">
      {yTicks.map((t, i) => {
        const yy = y(t)
        return (
          <g key={i}>
            <line x1={padL} x2={w - padR} y1={yy} y2={yy} className="prd-line-grid" />
            <text x={padL - 6} y={yy + 3} textAnchor="end" className="prd-line-axis">
              {Math.round(t)}
            </text>
          </g>
        )
      })}
      <polyline points={points} fill="none" stroke={CHART_COLORS[0]} strokeWidth={2.5} strokeLinejoin="round" />
      {data.map((d, i) => (
        <g key={i}>
          <circle cx={x(i)} cy={y(toNum(d.value))} r={3.5} fill={CHART_COLORS[0]} />
          <text x={x(i)} y={h - 10} textAnchor="middle" className="prd-line-axis">
            {d.label}
          </text>
        </g>
      ))}
    </svg>
  )
}

function PieChart({ data }: { data: PrdChartDatum[] }) {
  const total = data.reduce((sum, d) => sum + toNum(d.value), 0) || 1
  const cx = 90
  const cy = 90
  const r = 80
  let acc = 0
  const slices = data.map((d, i) => {
    const v = toNum(d.value)
    const start = (acc / total) * Math.PI * 2 - Math.PI / 2
    acc += v
    const end = (acc / total) * Math.PI * 2 - Math.PI / 2
    const large = end - start > Math.PI ? 1 : 0
    const x1 = cx + r * Math.cos(start)
    const y1 = cy + r * Math.sin(start)
    const x2 = cx + r * Math.cos(end)
    const y2 = cy + r * Math.sin(end)
    const path = `M ${cx} ${cy} L ${x1.toFixed(2)} ${y1.toFixed(2)} A ${r} ${r} 0 ${large} 1 ${x2.toFixed(2)} ${y2.toFixed(2)} Z`
    const pct = ((v / total) * 100).toFixed(0)
    return { path, color: CHART_COLORS[i % CHART_COLORS.length], pct, label: d.label, value: d.value }
  })
  return (
    <div className="prd-pie">
      <svg viewBox="0 0 180 180" width={180} height={180}>
        {slices.map((s, i) => (
          <path key={i} d={s.path} fill={s.color} />
        ))}
      </svg>
      <ul className="prd-pie-legend">
        {slices.map((s, i) => (
          <li key={i}>
            <span className="prd-pie-swatch" style={{ background: s.color }} />
            <span className="prd-pie-label">{s.label}</span>
            <span className="prd-pie-val">
              {fmtVal(s.value)} <span style={{ color: "var(--muted)" }}>({s.pct}%)</span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

function StatChart({ data }: { data: PrdChartDatum[] }) {
  return (
    <div className="prd-stats">
      {data.map((d, i) => (
        <div key={i} className="prd-stat">
          <div className="prd-stat-val">{fmtVal(d.value)}</div>
          <div className="prd-stat-lbl">{d.label}</div>
        </div>
      ))}
    </div>
  )
}

function PrdToolbar({ hasDoc }: { hasDoc: boolean }) {
  return (
    <div className="prd-toolbar">
      <div className="prd-tools-l">
        <button type="button" className="prd-tool" disabled={!hasDoc} title="Undo" aria-label="Undo">
          <IconUndo size={16} />
        </button>
        <button type="button" className="prd-tool" disabled={!hasDoc} title="Redo" aria-label="Redo">
          <IconRedo size={16} />
        </button>
        <div className="prd-tool-divider" />
        <button type="button" className="prd-tool" disabled={!hasDoc}>
          <strong>B</strong>
        </button>
        <button type="button" className="prd-tool" disabled={!hasDoc}>
          <em>I</em>
        </button>
        <button type="button" className="prd-tool" disabled={!hasDoc}>
          <u>U</u>
        </button>
        <div className="prd-tool-divider" />
        <button type="button" className="prd-tool" disabled={!hasDoc}>
          H1
        </button>
        <button type="button" className="prd-tool" disabled={!hasDoc}>
          H2
        </button>
        <button type="button" className="prd-tool" disabled={!hasDoc} title="Bullet list" aria-label="Bullet list">
          <IconListBullet size={16} />
        </button>
        <div className="prd-tool-divider" />
        <button
          type="button"
          className="prd-tool"
          disabled={!hasDoc}
          title="Insert link"
          style={{ display: "inline-flex", alignItems: "center" }}
        >
          <IconLinkInsert size={15} />
          <span style={{ marginLeft: 5 }}>Link</span>
        </button>
        <button
          type="button"
          className="prd-tool"
          disabled={!hasDoc}
          title="Insert table"
          style={{ display: "inline-flex", alignItems: "center" }}
        >
          <IconGrid size={15} />
          <span style={{ marginLeft: 5 }}>Table</span>
        </button>
      </div>
      <div className="prd-status">
        <span
          style={{
            width: 6,
            height: 6,
            borderRadius: "50%",
            background: hasDoc ? "var(--accent)" : "var(--muted)",
          }}
        />
        {hasDoc ? "Saved · Draft" : "No draft"}
      </div>
    </div>
  )
}

function ShareMenuItem({
  icon,
  iconStyle,
  title,
  desc,
  onClick,
}: {
  icon: ReactNode
  iconStyle?: CSSProperties
  title: string
  desc: string
  onClick: () => void
}) {
  return (
    <div className="share-menu-item" onClick={onClick}>
      <div className="share-menu-item-icon" style={iconStyle}>
        {icon}
      </div>
      <div>
        <div style={{ fontWeight: 600 }}>{title}</div>
        <div style={{ fontSize: 11, color: "var(--muted)", fontWeight: 400 }}>
          {desc}
        </div>
      </div>
    </div>
  )
}
