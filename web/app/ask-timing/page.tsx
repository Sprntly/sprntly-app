import type { Metadata } from "next"

// ─────────────────────────────────────────────────────────────────────────────
// Ask-flow timing report — a hand-updated static page, same pattern as
// /privacy and /support. No parameters, no database: the DATA constant below
// IS the report. Update ritual: pull the day's `[timing]` lines from the
// staging journal, compute per-block min/median/max, paste the two most
// recent days here, ship. The page always shows the latest day against the
// previous one, which is the comparison the latency work steers by.
// ─────────────────────────────────────────────────────────────────────────────

type StepStat = { label: string; note?: string; typical: string; min: string; max: string }
type DaySnapshot = {
  date: string
  asks: number
  workerMedianS: number
  workerMaxS: number
  plannerMedianS: number | null // null = not yet instrumented that day
  steps: StepStat[]
  highlights: string[]
}

const DATA: { current: DaySnapshot; previous: DaySnapshot } = {
  previous: {
    date: "2026-08-11",
    asks: 20,
    workerMedianS: 54,
    workerMaxS: 422,
    plannerMedianS: 5.5,
    steps: [], // per-step instrumentation shipped 2026-08-12; only totals exist
    highlights: [
      "Answers still called every connected tool live per question (up to 8s of third-party reads).",
      "Worst ask of the day ran 7 minutes.",
    ],
  },
  current: {
    date: "2026-08-12",
    asks: 26,
    workerMedianS: 33,
    workerMaxS: 149,
    plannerMedianS: 5.6,
    steps: [
      { label: "Planning", note: "decides how to answer · blocks the send", typical: "5.6s", min: "0.1s", max: "7.4s" },
      { label: "Understanding the question", note: "semantic encoding", typical: "0.5s", min: "0.2s", max: "0.9s" },
      { label: "Finding relevant documents", typical: "2.8s", min: "1.0s", max: "4.0s" },
      { label: "Searching product memory", note: "knowledge graph", typical: "6.0s", min: "4.9s", max: "7.0s" },
      { label: "Writing a standard answer", typical: "23.6s", min: "8.2s", max: "68s" },
      { label: "Voice-of-customer report", note: "3 runs", typical: "132s", min: "123s", max: "135s" },
      { label: "Competitive research report", note: "1 run · designed to be thorough", typical: "—", min: "—", max: "313s" },
      { label: "Follow-up suggestions", note: "after the answer is visible — doesn't block", typical: "1.3s", min: "0.6s", max: "2.5s" },
    ],
    highlights: [
      "Answers now read the synced knowledge graph instead of calling connectors live — median down 40% day-over-day.",
      "No model call used prompt caching today (0 cache reads across 46 questions) — the next fix.",
      "The plan's \"no memory search needed\" verdict is not honored yet (~6s spent anyway on those questions).",
      "Background report generation (ideation, evidence, PRD warms) competed with live questions for model capacity all day.",
    ],
  },
}

export const metadata: Metadata = {
  title: "Ask Timing · Sprntly",
  description: "Day-over-day timing for the chat ask flow, measured on staging.",
}

const pct = (a: number, b: number) => Math.round(((a - b) / b) * 100)

export default function AskTimingPage() {
  const { current, previous } = DATA
  const delta = pct(current.workerMedianS, previous.workerMedianS)
  const s: Record<string, React.CSSProperties> = {
    wrap: { maxWidth: 860, margin: "0 auto", padding: "44px 24px 72px", fontFamily: "ui-sans-serif, 'Segoe UI', system-ui, sans-serif", color: "#182620" },
    eyebrow: { fontFamily: "ui-monospace, Consolas, monospace", fontSize: 11.5, letterSpacing: "0.14em", textTransform: "uppercase" as const, color: "#10693c", fontWeight: 600 },
    h1: { fontSize: 34, fontWeight: 700, letterSpacing: "-0.02em", margin: "10px 0 6px" },
    sub: { color: "#5c6d64", margin: "0 0 26px", fontSize: 15 },
    kpis: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 12, marginBottom: 28 },
    kpi: { background: "#fff", border: "1px solid #d8e0db", borderRadius: 12, padding: "14px 16px" },
    kpiV: { fontSize: 26, fontWeight: 700, display: "block" },
    kpiL: { fontSize: 12.5, color: "#5c6d64" },
    card: { background: "#fff", border: "1px solid #d8e0db", borderRadius: 14, overflowX: "auto" as const },
    th: { textAlign: "left" as const, fontFamily: "ui-monospace, Consolas, monospace", fontSize: 11, letterSpacing: "0.12em", textTransform: "uppercase" as const, color: "#8fa098", padding: "13px 18px", borderBottom: "1px solid #d8e0db" },
    td: { padding: "11px 18px", borderBottom: "1px solid #d8e0db", fontSize: 14.5 },
    num: { textAlign: "right" as const, fontVariantNumeric: "tabular-nums" as const },
    note: { fontSize: 12.5, color: "#5c6d64", display: "block" },
    h2: { fontSize: 13, fontWeight: 650, letterSpacing: "0.12em", textTransform: "uppercase" as const, color: "#8fa098", margin: "36px 0 12px" },
    li: { margin: "0 0 8px", fontSize: 15, color: "#182620" },
  }
  return (
    <div style={{ background: "#f6f9f7", minHeight: "100vh" }}>
      <main style={s.wrap}>
        <div style={s.eyebrow}>Sprntly · internal · staging measurements</div>
        <h1 style={s.h1}>Ask timing — {current.date}</h1>
        <p style={s.sub}>
          Compared against {previous.date}. Hand-updated from the staging timing logs; steps overlap
          (retrieval runs in parallel), so step times do not sum to the total.
        </p>

        <div style={s.kpis}>
          <div style={s.kpi}><span style={{ ...s.kpiV, color: "#10693c" }}>{current.workerMedianS}s</span><span style={s.kpiL}>median answer · {current.date}</span></div>
          <div style={s.kpi}><span style={s.kpiV}>{previous.workerMedianS}s</span><span style={s.kpiL}>median answer · {previous.date}</span></div>
          <div style={s.kpi}><span style={{ ...s.kpiV, color: delta <= 0 ? "#10693c" : "#a8631f" }}>{delta > 0 ? `+${delta}` : delta}%</span><span style={s.kpiL}>day-over-day</span></div>
          <div style={s.kpi}><span style={s.kpiV}>{Math.round(current.workerMaxS / 60 * 10) / 10}m</span><span style={s.kpiL}>worst case (was {Math.round(previous.workerMaxS / 60)}m)</span></div>
          <div style={s.kpi}><span style={s.kpiV}>{current.asks}</span><span style={s.kpiL}>questions measured</span></div>
        </div>

        <div style={s.card}>
          <table style={{ width: "100%", borderCollapse: "collapse" as const, minWidth: 560 }}>
            <thead>
              <tr>
                <th style={s.th}>Step ({current.date})</th>
                <th style={{ ...s.th, ...s.num }}>Typical</th>
                <th style={{ ...s.th, ...s.num }}>Fastest</th>
                <th style={{ ...s.th, ...s.num }}>Slowest</th>
              </tr>
            </thead>
            <tbody>
              {current.steps.map((step, i) => (
                <tr key={step.label}>
                  <td style={{ ...s.td, borderBottom: i === current.steps.length - 1 ? "none" : s.td.borderBottom }}>
                    {step.label}
                    {step.note ? <span style={s.note}>{step.note}</span> : null}
                  </td>
                  <td style={{ ...s.td, ...s.num }}>{step.typical}</td>
                  <td style={{ ...s.td, ...s.num }}>{step.min}</td>
                  <td style={{ ...s.td, ...s.num }}>{step.max}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <h2 style={s.h2}>What changed / what we learned</h2>
        <ul style={{ margin: 0, paddingLeft: 20 }}>
          {current.highlights.map((h) => <li key={h} style={s.li}>{h}</li>)}
        </ul>

        <h2 style={s.h2}>Context from {previous.date}</h2>
        <ul style={{ margin: 0, paddingLeft: 20 }}>
          {previous.highlights.map((h) => <li key={h} style={s.li}>{h}</li>)}
        </ul>
      </main>
    </div>
  )
}
