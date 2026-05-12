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
        {kind === "gauge" ? <GaugeChart data={data} /> : null}
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

function wrapAxisLabel(label: string): string[] {
  const text = (label ?? "").toString()
  if (text.length <= 12) return [text]
  // Prefer splitting before a parenthetical (e.g. "Searched 3P repair (Day 7)"
  // → ["Searched 3P repair", "(Day 7)"]) so the qualifier sits on line two.
  const parenIdx = text.lastIndexOf(" (")
  if (parenIdx > 0 && parenIdx < text.length - 1) {
    return [text.slice(0, parenIdx), text.slice(parenIdx + 1)]
  }
  const mid = Math.floor(text.length / 2)
  const after = text.indexOf(" ", mid)
  const before = text.lastIndexOf(" ", mid)
  const candidates = [after, before].filter((i) => i > 0 && i < text.length - 1)
  if (candidates.length === 0) return [text]
  const splitAt = candidates.reduce((best, i) =>
    Math.abs(i - mid) < Math.abs(best - mid) ? i : best,
  )
  return [text.slice(0, splitAt), text.slice(splitAt + 1)]
}

function LineChart({ data }: { data: PrdChartDatum[] }) {
  const wrapped = data.map((d) => wrapAxisLabel(String(d.label ?? "")))
  const anyWrapped = wrapped.some((lines) => lines.length > 1)
  const w = 560
  const padL = 40
  const padR = 14
  const padT = 14
  const padB = anyWrapped ? 44 : 30
  const h = anyWrapped ? 196 : 180
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
  const labelBaselineY = anyWrapped ? h - 22 : h - 10
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
      {data.map((d, i) => {
        const lines = wrapped[i]
        return (
          <g key={i}>
            <circle cx={x(i)} cy={y(toNum(d.value))} r={3.5} fill={CHART_COLORS[0]} />
            <text
              x={x(i)}
              y={labelBaselineY}
              textAnchor="middle"
              className="prd-line-axis"
            >
              {lines.map((line, j) => (
                <tspan key={j} x={x(i)} dy={j === 0 ? 0 : 12}>
                  {line}
                </tspan>
              ))}
            </text>
          </g>
        )
      })}
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

function GaugeChart({ data }: { data: PrdChartDatum[] }) {
  // First datum = current value, optional second = target marker.
  const current = data[0]
  const target = data[1]
  if (!current) return null
  const currentNum = toNum(current.value)
  const targetNum = target ? toNum(target.value) : null
  // Pick a "nice" max — round up to next 25/50/100 based on data magnitude.
  const rawMax = Math.max(currentNum, targetNum ?? 0, 1)
  const niceMax = (() => {
    if (rawMax <= 25) return 25
    if (rawMax <= 50) return 50
    if (rawMax <= 100) return 100
    // Round up to next multiple of 50 above the raw max.
    return Math.ceil(rawMax / 50) * 50
  })()
  const currentPct = Math.max(0, Math.min(1, currentNum / niceMax))
  const targetPct =
    targetNum != null ? Math.max(0, Math.min(1, targetNum / niceMax)) : null

  // Geometry: 180° semicircle arc. SVG viewBox 240x140.
  const w = 240
  const h = 140
  const cx = w / 2
  const cy = 118 // baseline of arc near bottom of viewBox
  const r = 92
  const stroke = 16
  // Convert pct (0..1) along the 180° arc (from left, sweeping right).
  // Angle in degrees: 180 (left) → 0 (right). Use radians for math.
  const angleAt = (pct: number) => Math.PI * (1 - pct) // π → 0
  const ptAt = (pct: number, radius = r) => {
    const a = angleAt(pct)
    return {
      x: cx + radius * Math.cos(a),
      y: cy - radius * Math.sin(a),
    }
  }
  const arcPath = (fromPct: number, toPct: number) => {
    const p0 = ptAt(fromPct)
    const p1 = ptAt(toPct)
    // Gauge is a 180° semicircle — every sub-arc is < 180°, so the
    // large-arc-flag is always 0. (The previous threshold of `>0.5`
    // incorrectly picked the major arc for current values past 50%,
    // routing the arc through the bottom and clipping off-canvas.)
    return `M ${p0.x.toFixed(2)} ${p0.y.toFixed(2)} A ${r} ${r} 0 0 1 ${p1.x.toFixed(2)} ${p1.y.toFixed(2)}`
  }

  // Target tick mark — short radial line crossing the arc.
  const tickInner = targetPct != null ? ptAt(targetPct, r - stroke / 2 - 4) : null
  const tickOuter = targetPct != null ? ptAt(targetPct, r + stroke / 2 + 4) : null

  const gradId = `prd-gauge-grad-${Math.random().toString(36).slice(2, 8)}`
  const fmtCurrent = fmtVal(current.value)
  const fmtTarget = target ? fmtVal(target.value) : null

  return (
    <div className="prd-gauge">
      <svg
        viewBox={`0 0 ${w} ${h}`}
        width={w}
        height={h}
        className="prd-gauge-svg"
      >
        <defs>
          <linearGradient id={gradId} x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.75" />
            <stop offset="100%" stopColor="var(--accent)" stopOpacity="1" />
          </linearGradient>
        </defs>
        {/* Background arc */}
        <path
          d={arcPath(0, 1)}
          fill="none"
          stroke="var(--surface-3)"
          strokeWidth={stroke}
          strokeLinecap="round"
        />
        {/* Filled arc (current) */}
        {currentPct > 0 ? (
          <path
            d={arcPath(0, currentPct)}
            fill="none"
            stroke={`url(#${gradId})`}
            strokeWidth={stroke}
            strokeLinecap="round"
          />
        ) : null}
        {/* Target tick */}
        {tickInner && tickOuter ? (
          <line
            x1={tickInner.x}
            y1={tickInner.y}
            x2={tickOuter.x}
            y2={tickOuter.y}
            stroke="var(--ink)"
            strokeWidth={2}
            strokeLinecap="round"
          />
        ) : null}
        {/* Range labels (min / max) */}
        <text
          x={cx - r}
          y={cy + 14}
          textAnchor="middle"
          className="prd-gauge-tick"
        >
          0
        </text>
        <text
          x={cx + r}
          y={cy + 14}
          textAnchor="middle"
          className="prd-gauge-tick"
        >
          {niceMax}
        </text>
        {/* Center value */}
        <text
          x={cx}
          y={cy - 22}
          textAnchor="middle"
          className="prd-gauge-value"
        >
          {fmtCurrent}
        </text>
        {fmtTarget != null ? (
          <text
            x={cx}
            y={cy - 6}
            textAnchor="middle"
            className="prd-gauge-sub"
          >
            vs target {fmtTarget}
          </text>
        ) : null}
      </svg>
      <ul className="prd-gauge-legend">
        <li>
          <span className="prd-gauge-dot" />
          <span className="prd-gauge-lbl">{current.label || "Current"}</span>
          <span className="prd-gauge-val">{fmtCurrent}</span>
        </li>
        {target ? (
          <li>
            <span className="prd-gauge-tick-mark" />
            <span className="prd-gauge-lbl">{target.label || "Target"}</span>
            <span className="prd-gauge-val">{fmtTarget}</span>
          </li>
        ) : null}
      </ul>
    </div>
  )
}

const CHART_KINDS: PrdChartKind[] = ["bar", "line", "pie", "donut", "stat", "gauge"]

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
