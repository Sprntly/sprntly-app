"use client"

import type { PrdChartDatum, PrdChartKind } from "../../types/content"

export const CHART_COLORS = [
  "#5B7FFF",
  "#6FCF97",
  "#F2994A",
  "#BB6BD9",
  "#56CCF2",
  "#EB5757",
  "#F2C94C",
  "#27AE60",
]

export function toNum(v: number | string): number {
  if (typeof v === "number") return v
  const m = String(v).replace(/[^\d.\-]/g, "")
  const n = parseFloat(m)
  return Number.isFinite(n) ? n : 0
}

export function fmtVal(v: number | string): string {
  return typeof v === "string" ? v : String(v)
}

export function InlineChart({
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
        {kind === "donut" ? <PieChart data={data} donut /> : null}
        {kind === "stat" ? <StatChart data={data} /> : null}
        {kind === "funnel" ? <FunnelChart data={data} /> : null}
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
                style={{
                  width: `${pct.toFixed(1)}%`,
                  background: CHART_COLORS[i % CHART_COLORS.length],
                }}
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
  const points = data
    .map((d, i) => `${x(i).toFixed(1)},${y(toNum(d.value)).toFixed(1)}`)
    .join(" ")
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
      <polyline
        points={points}
        fill="none"
        stroke={CHART_COLORS[0]}
        strokeWidth={2.5}
        strokeLinejoin="round"
      />
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

function PieChart({ data, donut = false }: { data: PrdChartDatum[]; donut?: boolean }) {
  const total = data.reduce((sum, d) => sum + toNum(d.value), 0) || 1
  const cx = 90
  const cy = 90
  const r = 80
  // Donut variant cuts a circular hole in the middle. The hole renders as
  // an extra path with the page background color, layered above the slices.
  const innerR = donut ? r * 0.55 : 0
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
    return {
      path,
      color: CHART_COLORS[i % CHART_COLORS.length],
      pct,
      label: d.label,
      value: d.value,
    }
  })
  return (
    <div className="prd-pie">
      <svg viewBox="0 0 180 180" width={180} height={180}>
        {slices.map((s, i) => (
          <path key={i} d={s.path} fill={s.color} />
        ))}
        {donut ? (
          <circle cx={cx} cy={cy} r={innerR} fill="var(--surface)" />
        ) : null}
      </svg>
      <ul className="prd-pie-legend">
        {slices.map((s, i) => (
          <li key={i}>
            <span className="prd-pie-swatch" style={{ background: s.color }} />
            <span className="prd-pie-label">{s.label}</span>
            <span className="prd-pie-val">
              {fmtVal(s.value)}{" "}
              <span style={{ color: "var(--muted)" }}>({s.pct}%)</span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

function FunnelChart({ data }: { data: PrdChartDatum[] }) {
  // Each step is a trapezoid stacked vertically. Width scales linearly with
  // value (max value → full width, smallest → narrower). Labels render to
  // the LEFT of each trapezoid, value to the right — no overlap regardless
  // of label length, unlike a line chart's horizontal x-axis.
  const max = Math.max(...data.map((d) => toNum(d.value)), 1)
  return (
    <ul className="prd-funnel">
      {data.map((d, i) => {
        const v = toNum(d.value)
        const widthPct = Math.max(8, (v / max) * 100)
        const color = CHART_COLORS[i % CHART_COLORS.length]
        return (
          <li key={i} className="prd-funnel-row">
            <span className="prd-funnel-label">{d.label}</span>
            <span className="prd-funnel-track">
              <span
                className="prd-funnel-fill"
                style={{ width: `${widthPct.toFixed(1)}%`, background: color }}
              />
            </span>
            <span className="prd-funnel-val">{fmtVal(d.value)}</span>
          </li>
        )
      })}
    </ul>
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

const CHART_KINDS: PrdChartKind[] = ["bar", "line", "pie", "stat"]

/** Parse a `chart` fenced-block body into props for InlineChart, or null. */
export function parseChartBody(body: string): {
  kind: PrdChartKind
  title?: string
  subtitle?: string
  data: PrdChartDatum[]
} | null {
  const tryParse = (s: string): unknown => {
    try {
      return JSON.parse(s)
    } catch {
      return null
    }
  }
  const trimmed = body.trim()
  let parsed = tryParse(trimmed)
  if (parsed == null) {
    const start = trimmed.indexOf("{")
    const end = trimmed.lastIndexOf("}")
    if (start >= 0 && end > start) parsed = tryParse(trimmed.slice(start, end + 1))
  }
  if (!parsed || typeof parsed !== "object") return null
  const obj = parsed as Record<string, unknown>
  const kind = String(obj.kind || "").toLowerCase() as PrdChartKind
  if (!CHART_KINDS.includes(kind)) return null
  const dataRaw = (obj.data as unknown[]) || []
  if (!Array.isArray(dataRaw)) return null
  const data: PrdChartDatum[] = dataRaw
    .map((d) => {
      if (!d || typeof d !== "object") return null
      const item = d as Record<string, unknown>
      const label = item.label == null ? "" : String(item.label)
      const valueRaw = item.value
      if (valueRaw == null) return null
      const value: number | string =
        typeof valueRaw === "number" ? valueRaw : String(valueRaw)
      return { label, value }
    })
    .filter((d: PrdChartDatum | null): d is PrdChartDatum => d !== null)
  if (data.length === 0) return null
  return {
    kind,
    title: typeof obj.title === "string" ? obj.title : undefined,
    subtitle: typeof obj.subtitle === "string" ? obj.subtitle : undefined,
    data,
  }
}
