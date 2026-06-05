/**
 * Renderer for the PRD format. Dispatches every variant in PrdSection,
 * including the prd-* semantic blocks (tldr triptych, problem +
 * impact-cells, hypothesis card, requirements table, acceptance-criteria
 * list, metrics hero, risks list, milestone phases, definition-of-done
 * checklist). Also handles the shared `v2-context-chip` (same component
 * shape as evidence) plus the standard h2/p/ul/table/chart primitives.
 *
 * Each block type is a small subcomponent below for legibility and so
 * styles colocate with markup. Mirrors EvidenceSections.tsx structure.
 *
 * CSS class names retain the historical `prdv2-` prefix (the rename to
 * canonical only touched names visible to TS code; CSS rules in
 * globals.css are untouched to keep this change reviewable).
 */
"use client"

import type {
  PrdAcceptanceCriterionRow,
  PrdGuardrail,
  PrdMetricPoint,
  PrdMilestonePhase,
  PrdProblemImpactCell,
  PrdRequirementRow,
  PrdRiskRow,
  PrdSection,
  PrdState,
} from "../../types/content"
import { renderInline } from "../../lib/inline-md"
import { InlineChart } from "./InlineChart"
import { DesignAgentLauncher } from "../design-agent/DesignAgentLauncher"

export function PrdSections({
  sections,
  prdId,
  figmaFileKey,
  prdTitle,
}: {
  sections: PrdState["sections"]
  /** PRD DB id, threaded to the prd-design block so the F2 launcher can call
   *  the Design Agent. Optional so non-PRD callers (and the empty/demo states)
   *  still render the section without a Generate button. */
  prdId?: number
  /** Figma file key for the prd-design launcher; null/undefined → no source. */
  figmaFileKey?: string | null
  /** PRD title, threaded to the prd-design launcher so the preview card and the
   *  canvas breadcrumb can label the PRD. Optional so non-PRD callers (and the
   *  empty/demo states) keep type-checking. */
  prdTitle?: string | null
}) {
  return (
    <>
      {sections.map((block, i) => (
        <RenderBlock
          key={i}
          block={block}
          prdId={prdId}
          figmaFileKey={figmaFileKey}
          prdTitle={prdTitle}
        />
      ))}
    </>
  )
}

function RenderBlock({
  block,
  prdId,
  figmaFileKey,
  prdTitle,
}: {
  block: PrdSection
  prdId?: number
  figmaFileKey?: string | null
  prdTitle?: string | null
}) {
  switch (block.type) {
    case "h2":
      return <h2 className="prd-h2">{renderInline(block.text)}</h2>
    case "p":
      return <p>{renderInline(block.text)}</p>
    case "ul":
      return (
        <ul>
          {block.items.map((li, j) => (
            <li key={j}>{renderInline(li)}</li>
          ))}
        </ul>
      )
    case "table":
      return (
        <table className="prd-table">
          <thead>
            <tr>
              {block.headers.map((h, j) => (
                <th key={j}>{renderInline(h)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {block.rows.map((row, j) => (
              <tr key={j}>
                {row.map((cell, k) => (
                  <td key={k}>{renderInline(cell)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )
    case "chart":
      return (
        <InlineChart
          kind={block.kind}
          title={block.title}
          subtitle={block.subtitle}
          data={block.data}
        />
      )
    case "v2-context-chip":
      // Shared with evidence — same look. Inline the tiny component to
      // keep PrdSections self-contained (no cross-import from
      // EvidenceSections).
      return <div className="evv2-context-chip">{renderInline(block.text)}</div>
    case "prd-tldr":
      return (
        <TldrTriptych
          problem={block.problem}
          fix={block.fix}
          impact={block.impact}
        />
      )
    case "prd-problem":
      return <ProblemBlock userStory={block.userStory} impact={block.impact} />
    case "prd-hypothesis":
      return (
        <HypothesisCard
          ifWe={block.ifWe}
          thenMetric={block.thenMetric}
          because={block.because}
          secondary={block.secondary}
        />
      )
    case "prd-requirements":
      return <RequirementsList rows={block.rows} />
    case "prd-acceptance-criteria":
      return <AcceptanceCriteriaList rows={block.rows} />
    case "prd-metrics":
      return (
        <MetricsBlock
          primary={block.primary}
          secondary={block.secondary}
          guardrails={block.guardrails}
        />
      )
    case "prd-risks":
      return <RisksList rows={block.rows} />
    case "prd-milestones":
      return <MilestonesBlock phases={block.phases} />
    case "prd-dod":
      return <DodChecklist items={block.items} />
    case "prd-design":
      return <DesignSection prdId={prdId} figmaFileKey={figmaFileKey} prdTitle={prdTitle} />
    default:
      // Evidence variants and any unknown future blocks render as no-op
      // in the PRD renderer; the dedicated EvidenceSections covers them.
      return null
  }
}

/* ---------- subcomponents ---------- */

/**
 * F1/F2 Design section. Renders the header, then — when a `prdId` is in scope
 * (PrdScreen passes `prd.prd_id`) — the F2 `DesignAgentLauncher` ("Generate
 * Prototype" button + drawer). Without a `prdId` (non-PRD callers, the
 * empty/demo states) it falls back to the original empty-state entry point.
 * Parsed `platformHint` / `notes` hints stay on the PrdState block
 * (for P1-05's scaffold prompt); the P1 renderer intentionally does not surface
 * them.
 */
function DesignSection({
  prdId,
  figmaFileKey,
  prdTitle,
}: {
  prdId?: number
  figmaFileKey?: string | null
  prdTitle?: string | null
}) {
  return (
    <section className="prd-design">
      {/* Hot-file exception (sanctioned): this append-only prd-design region
          carries the relocated generate trigger, which now opens the Approve
          modal / canvas flow instead of a bare inline button. The PRD-body
          contentEditable region is deliberately untouched. The "Design" section
          heading was removed in the redesign; the section wrapper + launcher are
          kept. */}
      {prdId !== undefined ? (
        <DesignAgentLauncher prdId={prdId} figmaFileKey={figmaFileKey} prdTitle={prdTitle} />
      ) : (
        <div className="design-agent-surface">
          <p className="prd-design-empty">
            No prototype yet. Open this PRD to generate an interactive prototype
            from it.
          </p>
        </div>
      )}
    </section>
  )
}

function TldrTriptych({
  problem,
  fix,
  impact,
}: {
  problem: string
  fix: string
  impact: string
}) {
  return (
    <div className="prdv2-tldr">
      <div className="prdv2-tldr-card prdv2-tldr-problem">
        <div className="prdv2-tldr-label">Problem</div>
        <div className="prdv2-tldr-body">{renderInline(problem)}</div>
      </div>
      <div className="prdv2-tldr-arrow" aria-hidden="true">
        →
      </div>
      <div className="prdv2-tldr-card prdv2-tldr-fix">
        <div className="prdv2-tldr-label">Fix</div>
        <div className="prdv2-tldr-body">{renderInline(fix)}</div>
      </div>
      <div className="prdv2-tldr-arrow" aria-hidden="true">
        →
      </div>
      <div className="prdv2-tldr-card prdv2-tldr-impact">
        <div className="prdv2-tldr-label">Impact</div>
        <div className="prdv2-tldr-body">{renderInline(impact)}</div>
      </div>
    </div>
  )
}

function ProblemBlock({
  userStory,
  impact,
}: {
  userStory: string
  impact: PrdProblemImpactCell[]
}) {
  return (
    <div className="prdv2-problem">
      {userStory ? (
        <div className="prdv2-problem-story">{renderInline(userStory)}</div>
      ) : null}
      {impact.length > 0 ? (
        <div className="prdv2-problem-impact">
          {impact.map((c, i) => (
            <div
              key={i}
              className={`prdv2-impact-card prdv2-tone-${c.tone || "neutral"}`}
            >
              <div className="prdv2-impact-label">{c.label}</div>
              <div className="prdv2-impact-value">{c.value}</div>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  )
}

function HypothesisCard({
  ifWe,
  thenMetric,
  because,
  secondary,
}: {
  ifWe: string
  thenMetric: PrdMetricPoint
  because: string
  secondary?: string
}) {
  return (
    <div className="prdv2-hypothesis">
      <div className="prdv2-hyp-head">Hypothesis</div>
      <div className="prdv2-hyp-row">
        <div className="prdv2-hyp-k">If we</div>
        <div className="prdv2-hyp-v">{renderInline(ifWe)}</div>
      </div>
      <div className="prdv2-hyp-row">
        <div className="prdv2-hyp-k">Then metric</div>
        <div className="prdv2-hyp-v">
          <div className="prdv2-hyp-metric">
            <span className="prdv2-hyp-metric-name">{thenMetric.name}</span>
            <span className="prdv2-hyp-metric-move">
              <span className="prdv2-hyp-current">{thenMetric.current}</span>
              <span className="prdv2-hyp-arrow" aria-hidden="true">
                →
              </span>
              <span className="prdv2-hyp-target">{thenMetric.target}</span>
            </span>
          </div>
        </div>
      </div>
      {because ? (
        <div className="prdv2-hyp-row">
          <div className="prdv2-hyp-k">Because</div>
          <div className="prdv2-hyp-v">{renderInline(because)}</div>
        </div>
      ) : null}
      {secondary ? (
        <div className="prdv2-hyp-row">
          <div className="prdv2-hyp-k">Secondary</div>
          <div className="prdv2-hyp-v prdv2-hyp-secondary">
            {renderInline(secondary)}
          </div>
        </div>
      ) : null}
    </div>
  )
}

function RequirementsList({ rows }: { rows: PrdRequirementRow[] }) {
  return (
    <div className="prdv2-reqs">
      {rows.map((r, i) => (
        <div key={i} className="prdv2-req-row">
          <span className={`prdv2-req-cat prdv2-req-cat-${r.category}`}>
            {r.category}
          </span>
          <div className="prdv2-req-main">
            <div className="prdv2-req-behavior">{renderInline(r.behavior)}</div>
            {r.detail ? (
              <div className="prdv2-req-detail">{renderInline(r.detail)}</div>
            ) : null}
          </div>
        </div>
      ))}
    </div>
  )
}

function AcceptanceCriteriaList({
  rows,
}: {
  rows: PrdAcceptanceCriterionRow[]
}) {
  return (
    <div className="prdv2-ac">
      {rows.map((r, i) => (
        <div key={i} className="prdv2-ac-row">
          <div className="prdv2-ac-head">
            {r.id ? <span className="prdv2-ac-id">{r.id}</span> : null}
            {r.kind ? <span className="prdv2-ac-kind">{r.kind}</span> : null}
          </div>
          <div className="prdv2-ac-body">{renderInline(r.givenWhenThen)}</div>
          {r.verifiedBy ? (
            <div className="prdv2-ac-foot">
              <span className="prdv2-ac-foot-k">Verified by:</span>{" "}
              <span className="prdv2-ac-foot-v">{renderInline(r.verifiedBy)}</span>
            </div>
          ) : null}
        </div>
      ))}
    </div>
  )
}

function MetricsBlock({
  primary,
  secondary,
  guardrails,
}: {
  primary: PrdMetricPoint
  secondary: PrdMetricPoint[]
  guardrails: PrdGuardrail[]
}) {
  return (
    <div className="prdv2-metrics">
      {primary.name ? (
        <div className="prdv2-metric-primary">
          <div className="prdv2-metric-label">Primary</div>
          <div className="prdv2-metric-name">{primary.name}</div>
          <div className="prdv2-metric-move prdv2-metric-move-lg">
            <span className="prdv2-metric-current">{primary.current}</span>
            <span className="prdv2-metric-arrow" aria-hidden="true">
              →
            </span>
            <span className="prdv2-metric-target">{primary.target}</span>
          </div>
        </div>
      ) : null}

      {secondary.length > 0 ? (
        <div className="prdv2-metric-secondary">
          <div className="prdv2-metric-section-h">Secondary</div>
          <div className="prdv2-metric-grid">
            {secondary.map((m, i) => (
              <div key={i} className="prdv2-metric-card">
                <div className="prdv2-metric-name">{m.name}</div>
                <div className="prdv2-metric-move">
                  <span className="prdv2-metric-current">{m.current}</span>
                  <span className="prdv2-metric-arrow" aria-hidden="true">
                    →
                  </span>
                  <span className="prdv2-metric-target">{m.target}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {guardrails.length > 0 ? (
        <div className="prdv2-metric-guardrails">
          <div className="prdv2-metric-section-h prdv2-metric-section-h-warn">
            Guardrails · must not degrade
          </div>
          <div className="prdv2-metric-grid">
            {guardrails.map((g, i) => (
              <div key={i} className="prdv2-guardrail-card">
                <div className="prdv2-metric-name">{g.name}</div>
                <div className="prdv2-guardrail-move">
                  <span className="prdv2-guardrail-baseline">{g.baseline}</span>
                  <span className="prdv2-guardrail-bound">{g.bound}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  )
}

function RisksList({ rows }: { rows: PrdRiskRow[] }) {
  return (
    <div className="prdv2-risks">
      {rows.map((r, i) => (
        <div key={i} className="prdv2-risk-row">
          <div className="prdv2-risk-head">
            <span className={`prdv2-sev prdv2-sev-${r.severity}`}>
              {r.severity}
            </span>
            <span className="prdv2-risk-text">{renderInline(r.risk)}</span>
          </div>
          {r.mitigation ? (
            <div className="prdv2-risk-mit">
              <span className="prdv2-risk-mit-k">Mitigation:</span>{" "}
              <span className="prdv2-risk-mit-v">
                {renderInline(r.mitigation)}
              </span>
            </div>
          ) : null}
        </div>
      ))}
    </div>
  )
}

function MilestonesBlock({ phases }: { phases: PrdMilestonePhase[] }) {
  return (
    <div className="prdv2-milestones">
      {phases.map((p, i) => (
        <div key={i} className="prdv2-milestone-phase">
          <div className="prdv2-milestone-h">{p.phase}</div>
          {p.items.length > 0 ? (
            <ul className="prdv2-milestone-list">
              {p.items.map((item, j) => (
                <li key={j}>{renderInline(item)}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ))}
    </div>
  )
}

function DodChecklist({ items }: { items: string[] }) {
  return (
    <ul className="prdv2-dod">
      {items.map((item, i) => (
        <li key={i} className="prdv2-dod-item">
          <span className="prdv2-dod-box" aria-hidden="true" />
          <span className="prdv2-dod-text">{renderInline(item)}</span>
        </li>
      ))}
    </ul>
  )
}
