"use client"

import { useState } from "react"
import { useNavigation } from "../../../context/NavigationContext"
import { useContent } from "../../../context/ContentContext"
import { AppLayout } from "./AppLayout"
import { EmptyPane } from "../../shared/EmptyPane"

export function ShippedScreen() {
  const { goTo } = useNavigation()
  const { content } = useContent()
  const [range, setRange] = useState<30 | 60 | 90>(30)
  const { stats, primary, supporting } = content.shipped

  const empty =
    stats.length === 0 && primary.length === 0 && supporting.length === 0

  return (
    <AppLayout>
      <div className="main-header">
        <div>
          <h1 className="main-title">Shipped</h1>
          <p className="main-sub">
            Everything Sprntly surfaced that made it to production — and the impact
            we measured.
          </p>
        </div>
        <div className="shipped-range-tabs">
          {([30, 60, 90] as const).map((r) => (
            <button
              key={r}
              type="button"
              className={`shipped-range-tab ${range === r ? "active" : ""}`}
              onClick={() => setRange(r)}
            >
              {r}d
            </button>
          ))}
        </div>
      </div>

      {empty ? (
        <EmptyPane
          title="Nothing in the shipped ledger yet"
          hint="When tickets close and your worker attributes impact, fill `content.shipped.stats`, `primary`, and `supporting` from the API."
          placeholders={4}
        />
      ) : (
        <>
          <div className="shipped-summary">
            <div className="shipped-stats-row">
              {stats.map((s, i) => (
                <div key={i} className="shipped-stat">
                  <div className={`shipped-stat-val ${s.valueClass ?? ""}`}>{s.value}</div>
                  <div className="shipped-stat-label">{s.label}</div>
                </div>
              ))}
            </div>
          </div>

          {primary.length > 0 ? (
            <div className="shipped-section">
              <h2 className="shipped-section-title">Primary impact</h2>
              <p className="shipped-section-sub">
                Top revenue and activation wins from Sprntly findings.
              </p>
              <div className="shipped-primary-row">
                {primary.map((item, i) => (
                  <div
                    key={i}
                    className="shipped-primary-card"
                    onClick={() => goTo("detail")}
                  >
                    <div className="shipped-primary-title">{item.title}</div>
                    <div className="shipped-primary-date">{item.date}</div>
                    <div className="shipped-primary-right">
                      {item.mrr ? (
                        <div className="shipped-primary-metric">
                          <div className="shipped-primary-val pos">{item.mrr}</div>
                          <div className="shipped-primary-label">MRR</div>
                        </div>
                      ) : null}
                      {item.metric ? (
                        <div className="shipped-primary-metric">
                          <div className="shipped-primary-val pos">{item.metric}</div>
                          <div className="shipped-primary-label">Metric</div>
                        </div>
                      ) : null}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ) : null}

          {supporting.length > 0 ? (
            <div className="shipped-section">
              <h2 className="shipped-section-title">Supporting wins</h2>
              <p className="shipped-section-sub">
                Smaller fixes that add up — support deflection, uptime, polish.
              </p>
              <div className="shipped-supporting">
                {supporting.map((item, i) => (
                  <div
                    key={i}
                    className="shipped-supporting-card"
                    onClick={() => goTo("detail")}
                  >
                    <div className="shipped-supporting-title">{item.title}</div>
                    <div className="shipped-supporting-date">{item.date}</div>
                    <div className="shipped-supporting-impact">
                      {item.mrr ? <span className="pos">{item.mrr}</span> : null}
                      {item.metric ? <span className="pos">{item.metric}</span> : null}
                      {item.tickets ? <span>{item.tickets} tickets</span> : null}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ) : null}
        </>
      )}
    </AppLayout>
  )
}
