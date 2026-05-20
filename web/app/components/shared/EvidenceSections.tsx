/**
 * Renderer for the evidence document. Handles every variant in PrdSection,
 * including the semantic blocks (hero strip, context chip, cuts index,
 * source chips, rules callout, quote cards, forecast-omitted notice) plus
 * standard markdown primitives (`p`, `h2`, `ul`, `table`, `chart`).
 *
 * Each block type is a small subcomponent below for legibility and so
 * styles colocate with markup. Section types are still prefixed `v2-*`
 * for historical reasons; they're the canonical evidence blocks (no v1).
 */
"use client"

import type {
  EvidenceV2CutsIndexRow,
  EvidenceV2HeroCard,
  EvidenceV2SourceChip,
  PrdSection,
  PrdState,
} from "../../types/content"
import { renderInline } from "../../lib/inline-md"
import { InlineChart } from "./InlineChart"

export function EvidenceSections({
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

function ForecastOmitted({ reason }: { reason: string }) {
  return (
    <div className="evv2-forecast-omitted">
      <strong>Forecast omitted:</strong>{" "}
      <span>{renderInline(reason || "no trend basis in cuts")}</span>
    </div>
  )
}
