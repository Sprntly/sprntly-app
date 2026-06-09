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
              {tree.north_star_description.trim() && (
                <div className="kpi-meta">
                  <span>{tree.north_star_description}</span>
                </div>
              )}
            </div>
          )}
          {metrics.length > 0 && (
            <div className="kpi-children">
              {metrics.map((m, i) => (
                <div key={`${m.name}-${i}`} className="kpi-node">
                  <div className="kpi-node-row">
                    <span className="kpi-node-value">{m.name}</span>
                  </div>
                  {m.description.trim() && (
                    <div className="kpi-meta">
                      <span>{m.description}</span>
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
