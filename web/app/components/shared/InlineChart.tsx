"use client"

import { useEffect, useRef, useState } from "react"

import type { PrdChartDatum, PrdChartKind } from "../../types/content"

/**
 * The categorical series colours, in FIXED ORDER — slot 1 is always blue, slot
 * 2 always orange. Never cycled or reassigned by rank: colour follows the
 * entity, so a filter that drops a series must not repaint the survivors.
 *
 * THESE WERE CHOSEN BY VALIDATOR, NOT BY EYE. The previous eight
 * (#5B7FFF, #6FCF97, #F2994A…) failed two of the six checks a categorical
 * palette has to clear: three sat outside the lightness band (#6FCF97 0.78,
 * #56CCF2 0.79, #F2C94C 0.85 against a 0.43–0.77 band, so they washed out on
 * white), and five fell under 3:1 contrast with the surface. This set clears
 * the band, the chroma floor, colour-vision separation (worst adjacent pair
 * ΔE 9.1 protan, target ≥ 8) and the normal-vision floor (worst 19.6, floor
 * 15).
 *
 * Three of them — aqua, yellow, magenta — still sit under 3:1 on a light
 * surface. That is allowed only with RELIEF: every chart here carries a
 * visible value label beside its mark, so identity never rests on the colour
 * alone. If a future chart form drops those labels, it needs a table view
 * instead, not a darker palette.
 */
export const CHART_COLORS = [
  "#2a78d6", // 1 blue
  "#eb6834", // 2 orange
  "#1baf7a", // 3 aqua
  "#eda100", // 4 yellow
  "#e87ba4", // 5 magenta
  "#008300", // 6 green
  "#4a3aa7", // 7 violet
  "#e34948", // 8 red
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


/**
 * Has this chart been scrolled into view yet?
 *
 * WHY SCROLL AND NOT MOUNT. These render inside a streaming chat reply: the
 * card exists before its numbers finish arriving, so animating on mount would
 * play the reveal against half a dataset, in a card the reader is not looking
 * at yet. Intersection is the moment it is actually seen.
 *
 * FIRES ONCE. A bar that re-grows every time it scrolls past is a distraction
 * the second time and a fault the tenth; the observer disconnects on the first
 * hit and the chart stays put.
 *
 * REDUCED MOTION SKIPS STRAIGHT TO THE END STATE — `true` on the first render,
 * no observer, no transition. Someone who has asked their OS for less motion
 * is not asking for a slower reveal; they are asking for none, and an animated
 * chart is a common accessibility complaint.
 */
function useRevealed<T extends Element>() {
  const ref = useRef<T | null>(null)
  const [revealed, setRevealed] = useState(() => prefersReducedMotion())

  useEffect(() => {
    if (revealed) return
    const el = ref.current
    // No element, or a browser without the observer (jsdom included): show the
    // finished chart rather than an empty one — the reveal is decoration, and
    // decoration must never be load-bearing for reading the data.
    if (!el || typeof IntersectionObserver === "undefined") {
      setRevealed(true)
      return
    }
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setRevealed(true)
          io.disconnect()
        }
      },
      // A little of the card is enough — waiting for half of a tall chart
      // means the top has already been read by the time it moves.
      { threshold: 0.15 },
    )
    io.observe(el)
    return () => io.disconnect()
  }, [revealed])

  return { ref, revealed }
}

function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || !window.matchMedia) return false
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches
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
  // One observer per chart, on the figure — every mark inside reveals off the
  // same `data-revealed`, so a bar, its value and the line beside it move
  // together rather than racing.
  const { ref, revealed } = useRevealed<HTMLElement>()
  return (
    <figure
      ref={ref}
      className={`prd-chart prd-chart-${kind}`}
      data-revealed={revealed ? "true" : "false"}
    >
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
                  // The width IS the reveal: 0 until the card is seen, then
                  // the CSS transition carries it out. Staggered a row at a
                  // time so the eye reads the ranking in order rather than
                  // watching five bars arrive at once.
                  width: `${pct.toFixed(1)}%`,
                  background: CHART_COLORS[i % CHART_COLORS.length],
                  transitionDelay: `${Math.min(i, 8) * 60}ms`,
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
        // `pathLength=1` normalises the dash to a fraction of the line's own
        // length, so the draw-on takes the same time whatever the data's shape
        // — without it a jagged series animates slower than a flat one.
        pathLength={1}
        className="prd-line-path"
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
            <circle
              cx={x(i)}
              cy={y(toNum(d.value))}
              // >= 8px across, with a 2px ring in the surface colour so a dot
              // sitting on the line, a gridline or another dot still reads as
              // its own mark.
              r={4}
              fill={CHART_COLORS[0]}
              className="prd-line-dot"
              style={{ transitionDelay: `${420 + Math.min(i, 10) * 40}ms` }}
            />
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
  // WHICH SLICE IS BEING READ. Hover and keyboard focus both set it, from the
  // ring or the legend, so the two halves of the chart are one control: point
  // at "In Progress" in either place and the other lights up too.
  const [active, setActive] = useState<number | null>(null)

  const total = data.reduce((sum, d) => sum + toNum(d.value), 0) || 1
  const cx = 90
  const cy = 90
  const r = 80
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

  const shown = active == null ? null : slices[active]

  return (
    <div className="prd-pie">
      <div className="prd-pie-ring">
        <svg viewBox="0 0 180 180" width={180} height={180} role="presentation">
          {slices.map((s, i) => (
            <path
              key={i}
              className="prd-pie-slice"
              d={s.path}
              fill={s.color}
              // A 2px separator in the SURFACE colour between touching slices —
              // white doing the dividing, so two adjacent hues never read as
              // one shape. Without it a donut is a solid ring with colour
              // changes in it.
              stroke="var(--surface-2)"
              strokeWidth={2}
              // Dimming uses fill-opacity, NOT opacity: the reveal animation
              // already owns opacity, and two rules on one property fight
              // whenever a reader hovers mid-reveal.
              fillOpacity={active == null || active === i ? 1 : 0.28}
              data-active={active === i ? "true" : undefined}
              onMouseEnter={() => setActive(i)}
              onMouseLeave={() => setActive(null)}
              style={{ transitionDelay: `${Math.min(i, 8) * 70}ms` }}
            />
          ))}
          {donut ? <circle cx={cx} cy={cy} r={innerR} fill="var(--surface-2)" /> : null}
        </svg>

        {/* THE HOLE IS THE READOUT. A donut's middle is the one place a value
            can go that needs no tooltip positioning, never leaves the card, and
            reads the same for a mouse and for a keyboard. Resting, it holds the
            total — the number a share chart otherwise makes you add up. */}
        {donut ? (
          <div className="prd-pie-center" aria-hidden>
            <span className="prd-pie-center-val">
              {shown ? fmtVal(shown.value) : total}
            </span>
            <span className="prd-pie-center-lbl">
              {shown ? `${shown.label} · ${shown.pct}%` : "total"}
            </span>
          </div>
        ) : null}
      </div>

      {/* The legend is a list of BUTTONS, not text. It is the keyboard's way
          into the ring — tab through it and each slice lights up with its
          share — and the reason this chart needs no separate tooltip. */}
      <ul className="prd-pie-legend">
        {slices.map((s, i) => (
          <li key={i}>
            <button
              type="button"
              className="prd-pie-legend-row"
              data-active={active === i ? "true" : undefined}
              // Reading a chart is not an action; this is a hover/focus target
              // that happens to be keyboard-reachable, so it announces the
              // figures rather than a command.
              aria-label={`${s.label}: ${fmtVal(s.value)}, ${s.pct}%`}
              onMouseEnter={() => setActive(i)}
              onMouseLeave={() => setActive(null)}
              onFocus={() => setActive(i)}
              onBlur={() => setActive(null)}
            >
              <span className="prd-pie-swatch" style={{ background: s.color }} />
              <span className="prd-pie-label">{s.label}</span>
              <span className="prd-pie-val">
                {fmtVal(s.value)} <span className="prd-pie-pct">({s.pct}%)</span>
              </span>
            </button>
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
        <div
          key={i}
          className="prd-stat"
          // A rise-and-fade, not a count-up. A number that spins to its value
          // is unreadable while it does it, and these are the figures the
          // sentence above the card just quoted — the reader is checking one,
          // not watching it.
          style={{ transitionDelay: `${Math.min(i, 6) * 70}ms` }}
        >
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
            // `pathLength=1` again: the sweep is a dash offset over the arc's
            // own length, so a 10% gauge and a 90% one fill at the same rate
            // rather than the short one snapping.
            pathLength={1}
            className="prd-gauge-value"
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
