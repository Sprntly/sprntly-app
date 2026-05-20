/**
 * Renderer for the v2 evidence format. Handles every variant in PrdSection,
 * including the v2-prefixed semantic blocks (hero strip, context chip,
 * cuts index, source chips, rules callout, quote cards, experiment card,
 * forecast-omitted notice). Falls back to InlineChart / standard markdown
 * blocks for the legacy variants (`p`, `h2`, `ul`, `table`, `chart`).
 *
 * Each block type is a small subcomponent below for legibility and so
 * styles colocate with markup.
 */
"use client"

import type {
  EvidenceV2CutsIndexRow,
  EvidenceV2Experiment,
  EvidenceV2HeroCard,
  EvidenceV2SourceChip,
  PrdSection,
  PrdState,
} from "../../types/content"
import { renderInline } from "../../lib/inline-md"
import { InlineChart } from "./InlineChart"

export function EvidenceV2Sections({
  sections,
}: {
  sections: PrdState["sections"]
}) {
  return (
    <>
      {sections.map((block, i) => (
        <RenderBlock key={i} block={block} />
      ))}
    </>
  )
}

function RenderBlock({ block }: { block: PrdSection }) {
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
    case "v2-hero":
      return <HeroStrip cards={block.cards} />
    case "v2-context-chip":
      return <ContextChip text={block.text} />
    case "v2-cuts-index":
      return <CutsIndex rows={block.rows} />
    case "v2-source":
      return <SourceChips chips={block.chips} />
    case "v2-rules-callout":
      return (
        <RulesCallout supports={block.supports} rulesOut={block.rulesOut} />
      )
    case "v2-quote":
      return (
        <QuoteCard
          body={block.body}
          channel={block.channel}
          context={block.context}
        />
      )
    case "v2-experiment":
      return <ExperimentCard experiment={block.experiment} />
    case "v2-forecast-omitted":
      return <ForecastOmitted reason={block.reason} />
    default:
      return null
  }
}

/* ---------- subcomponents ---------- */

function HeroStrip({ cards }: { cards: EvidenceV2HeroCard[] }) {
  return (
    <div className="evv2-hero">
      {cards.map((c, i) => (
        <div key={i} className={`evv2-hero-card evv2-tone-${c.tone}`}>
          <div className="evv2-hero-label">{c.label}</div>
          <div className="evv2-hero-value">{c.value}</div>
          {c.delta ? <div className="evv2-hero-delta">{c.delta}</div> : null}
          {c.baseline ? (
            <div className="evv2-hero-baseline">{c.baseline}</div>
          ) : null}
        </div>
      ))}
    </div>
  )
}

function ContextChip({ text }: { text: string }) {
  return <div className="evv2-context-chip">{renderInline(text)}</div>
}

function CutsIndex({ rows }: { rows: EvidenceV2CutsIndexRow[] }) {
  return (
    <div className="evv2-cuts-index">
      {rows.map((r, i) => (
        <div key={i} className="evv2-cuts-index-row">
          <span className="evv2-cuts-index-n">Cut {r.n}</span>
          <span className="evv2-cuts-index-headline">
            {renderInline(r.headline)}
          </span>
          <ConfidenceChip value={r.confidence} />
        </div>
      ))}
    </div>
  )
}

function SourceChips({ chips }: { chips: EvidenceV2SourceChip[] }) {
  return (
    <div className="evv2-source">
      {chips.map((c, i) => {
        if (c.kind === "confidence") {
          // confidence chip is already styled separately
          const conf =
            c.label === "High" || c.label === "Medium" || c.label === "Low"
              ? c.label
              : "Medium"
          return <ConfidenceChip key={i} value={conf} />
        }
        return (
          <span key={i} className={`evv2-source-chip evv2-source-${c.kind}`}>
            <span className="evv2-source-kind">{c.kind}</span>
            <span className="evv2-source-label">{c.label}</span>
          </span>
        )
      })}
    </div>
  )
}

function ConfidenceChip({
  value,
}: {
  value: "High" | "Medium" | "Low"
}) {
  return (
    <span className={`evv2-conf evv2-conf-${value.toLowerCase()}`}>
      <span className="evv2-conf-dot" aria-hidden="true" />
      <span>{value} confidence</span>
    </span>
  )
}

function RulesCallout({
  supports,
  rulesOut,
}: {
  supports: string
  rulesOut: string
}) {
  return (
    <div className="evv2-rules">
      {supports ? (
        <div className="evv2-rules-half evv2-rules-supports">
          <div className="evv2-rules-h">Supports</div>
          <div className="evv2-rules-body">{renderInline(supports)}</div>
        </div>
      ) : null}
      {rulesOut ? (
        <div className="evv2-rules-half evv2-rules-out">
          <div className="evv2-rules-h">Rules out</div>
          <div className="evv2-rules-body">{renderInline(rulesOut)}</div>
        </div>
      ) : null}
    </div>
  )
}

function QuoteCard({
  body,
  channel,
  context,
}: {
  body: string
  channel: string
  context?: string
}) {
  return (
    <figure className="evv2-quote">
      <span className="evv2-quote-mark" aria-hidden="true">
        “
      </span>
      <blockquote className="evv2-quote-body">{body}</blockquote>
      <figcaption className="evv2-quote-caption">
        <span className="evv2-quote-channel">{channel}</span>
        {context ? <span className="evv2-quote-context">{context}</span> : null}
      </figcaption>
    </figure>
  )
}

function ExperimentCard({ experiment }: { experiment: EvidenceV2Experiment }) {
  const pm = experiment.primary_metric
  return (
    <div className="evv2-experiment">
      <div className="evv2-experiment-head">Proposed experiment</div>

      <div className="evv2-experiment-row">
        <div className="evv2-experiment-k">Change</div>
        <div className="evv2-experiment-v">{renderInline(experiment.change)}</div>
      </div>

      <div className="evv2-experiment-row">
        <div className="evv2-experiment-k">Primary metric</div>
        <div className="evv2-experiment-v">
          <div className="evv2-experiment-metric">
            <span className="evv2-experiment-metric-name">{pm.name}</span>
            <span className="evv2-experiment-metric-move">
              <span className="evv2-experiment-current">{pm.current}</span>
              <span className="evv2-experiment-arrow" aria-hidden="true">
                →
              </span>
              <span className="evv2-experiment-target">{pm.target}</span>
            </span>
          </div>
          {pm.mechanism ? (
            <div className="evv2-experiment-mechanism">
              {renderInline(pm.mechanism)}
            </div>
          ) : null}
        </div>
      </div>

      {experiment.sample_size || experiment.duration ? (
        <div className="evv2-experiment-row">
          <div className="evv2-experiment-k">Test plan</div>
          <div className="evv2-experiment-v">
            {experiment.sample_size ? (
              <span className="evv2-experiment-pill">
                {experiment.sample_size}
              </span>
            ) : null}
            {experiment.duration ? (
              <span className="evv2-experiment-pill">
                {experiment.duration}
              </span>
            ) : null}
          </div>
        </div>
      ) : null}

      {experiment.secondary_effects && experiment.secondary_effects.length > 0 ? (
        <div className="evv2-experiment-row">
          <div className="evv2-experiment-k">Secondary effects</div>
          <div className="evv2-experiment-v">
            <ul className="evv2-experiment-list">
              {experiment.secondary_effects.map((s, i) => (
                <li key={i}>{renderInline(s)}</li>
              ))}
            </ul>
          </div>
        </div>
      ) : null}

      {experiment.risks && experiment.risks.length > 0 ? (
        <div className="evv2-experiment-row">
          <div className="evv2-experiment-k">Risks</div>
          <div className="evv2-experiment-v">
            <ul className="evv2-experiment-list">
              {experiment.risks.map((s, i) => (
                <li key={i}>{renderInline(s)}</li>
              ))}
            </ul>
          </div>
        </div>
      ) : null}
    </div>
  )
}

function ForecastOmitted({ reason }: { reason: string }) {
  return (
    <div className="evv2-forecast-omitted">
      <strong>Forecast omitted:</strong>{" "}
      <span>{renderInline(reason || "no trend basis in cuts")}</span>
    </div>
  )
}
