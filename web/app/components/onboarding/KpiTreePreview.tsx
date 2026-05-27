"use client"

import type { KpiTree } from "../../lib/onboarding/types"

export function KpiTreePreview({ tree }: { tree: KpiTree }) {
  const hasNorthStar = tree.north_star.trim().length > 0
  const metrics = tree.metrics.filter((m) => m.name.trim())

  return (
    <div className="kpi-preview">
      <div className="ob-preview-label">Live preview</div>
      <h3 className="ob-preview-title">KPI tree</h3>
      {!hasNorthStar && metrics.length === 0 ? (
        <p className="ob-preview-empty">
          Your KPI hierarchy will appear here as you define metrics.
        </p>
      ) : (
        <div className="kpi-tree">
          {hasNorthStar && (
            <div className="kpi-node kpi-node-root">
              <div className="kpi-node-label">North star</div>
              <div className="kpi-node-value">{tree.north_star}</div>
            </div>
          )}
          {metrics.length > 0 && (
            <div className="kpi-children">
              {metrics.map((m, i) => (
                <div key={`${m.name}-${i}`} className="kpi-node">
                  <div className="kpi-node-row">
                    <span className="kpi-node-value">{m.name}</span>
                    <span className="kpi-weight">{Math.round(m.weight * 100)}%</span>
                  </div>
                  {(m.current_value || m.target_value) && (
                    <div className="kpi-meta">
                      {m.current_value && <span>Now: {m.current_value}</span>}
                      {m.target_value && <span>Target: {m.target_value}</span>}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
