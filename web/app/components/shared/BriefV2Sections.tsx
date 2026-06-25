"use client"

import type {
  BriefV2CompactFinding,
  BriefV2Convergence,
  BriefV2HeroFinding,
  BriefV2KpiTile,
  BriefV2State,
} from "../../lib/brief-v2-adapter"
import { InlineChart } from "./InlineChart"

export interface BriefV2Callbacks {
  prdBusyKey: string | null
  onViewEvidence: (detailKey: string | undefined) => void
  onAskAI: (question: string) => void
  onSecondary: (card: BriefV2HeroFinding | BriefV2CompactFinding) => void
}

export function BriefV2Render({
  state,
  callbacks,
}: {
  state: BriefV2State
  callbacks: BriefV2Callbacks
}) {
  if (!state.hero && state.supporting.length === 0) return null
  return (
    <div className="briefv2-doc">
      <BriefV2Header state={state} />
      {state.kpiTiles.length > 0 ? (
        <BriefV2KpiStrip tiles={state.kpiTiles} />
      ) : null}
      {state.hero ? (
        <HeroFindingCard card={state.hero} callbacks={callbacks} />
      ) : null}
      {state.supporting.length > 0 ? (
        <div className="briefv2-supporting">
          {state.supporting.map((c) => (
            <CompactFindingCard
              key={c.detailKey ?? `${c.tagType}-${c.title}`}
              card={c}
              callbacks={callbacks}
            />
          ))}
        </div>
      ) : null}
      {state.sourcesLine ? (
        <div className="briefv2-sources">
          <span className="briefv2-sources-label">Sources this week</span>
          <span className="briefv2-sources-line">{state.sourcesLine}</span>
        </div>
      ) : null}
    </div>
  )
}

function BriefV2Header({ state }: { state: BriefV2State }) {
  return (
    <header className="briefv2-header">
      <div className="briefv2-header-meta">
        <span>{state.company}</span>
        {state.weekOf ? <span>· {state.weekOf}</span> : null}
        {state.productArea ? <span>· {state.productArea}</span> : null}
      </div>
      {state.headline ? (
        <h1 className="briefv2-headline">{state.headline}</h1>
      ) : (
        <h1 className="briefv2-headline">This week in {state.company}</h1>
      )}
    </header>
  )
}

function BriefV2KpiStrip({ tiles }: { tiles: BriefV2KpiTile[] }) {
  return (
    <div className="briefv2-kpi-strip" role="list">
      {tiles.map((t, i) => (
        <div key={i} className={`briefv2-kpi briefv2-kpi--${t.tone}`} role="listitem">
          <div className="briefv2-kpi-value">{t.value}</div>
          <div className="briefv2-kpi-label">{t.label}</div>
        </div>
      ))}
    </div>
  )
}

function HeroFindingCard({
  card,
  callbacks,
}: {
  card: BriefV2HeroFinding
  callbacks: BriefV2Callbacks
}) {
  const busy =
    callbacks.prdBusyKey === card.detailKey &&
    card.secondaryCtaBehavior === "generate_prd"
  return (
    <article
      className="briefv2-hero briefv2-card briefv2-card--skill"
      style={{ ["--card-accent"]: card.skillAccent } as React.CSSProperties}
    >
      <div className="briefv2-card-inner">
        <div className="briefv2-card-top">
          <span className="briefv2-card-action briefv2-card-action--skill">{card.skillLabel}</span>
          <span className="briefv2-card-eyebrow">HEADLINE FINDING</span>
          <span className="briefv2-card-metric">{card.metricHighlight}</span>        </div>
        <h2 className="briefv2-card-headline">{card.title}</h2>
        <p className="briefv2-card-body">{card.body}</p>
        <ConvergenceChipRow rows={card.convergence} extra={0} />
        {card.chart ? (
          <div className="briefv2-hero-chart">
            <InlineChart
              kind={card.chart.kind}
              title={card.chart.title}
              subtitle={card.chart.subtitle}
              data={card.chart.data}
            />
          </div>
        ) : null}
        {card.quote ? <HeroQuote quote={card.quote} /> : null}
        <CardActions
          busy={busy}
          secondaryLabel={card.secondaryCtaLabel}
          accent={card.actionAccent}
          onViewEvidence={() => callbacks.onViewEvidence(card.detailKey)}
          onAskAI={() => callbacks.onAskAI(card.askQuestion)}
          onSecondary={() => callbacks.onSecondary(card)}
        />
      </div>
    </article>
  )
}

function CompactFindingCard({
  card,
  callbacks,
}: {
  card: BriefV2CompactFinding
  callbacks: BriefV2Callbacks
}) {
  const busy =
    callbacks.prdBusyKey === card.detailKey &&
    card.secondaryCtaBehavior === "generate_prd"
  return (
    <article
      className="briefv2-compact briefv2-card briefv2-card--skill"
      style={{ ["--card-accent"]: card.skillAccent } as React.CSSProperties}
    >
      <div className="briefv2-card-inner">
        <div className="briefv2-card-top">
          <span className="briefv2-card-action briefv2-card-action--skill">{card.skillLabel}</span>
          <span className="briefv2-card-metric">{card.metricHighlight}</span>        </div>
        <h3 className="briefv2-card-headline">{card.title}</h3>
        <p className="briefv2-card-body">{card.body}</p>
        <ConvergenceChipRow rows={card.convergence} extra={card.extraConvergenceCount} />
        <CardActions
          busy={busy}
          secondaryLabel={card.secondaryCtaLabel}
          accent={card.actionAccent}
          onViewEvidence={() => callbacks.onViewEvidence(card.detailKey)}
          onAskAI={() => callbacks.onAskAI(card.askQuestion)}
          onSecondary={() => callbacks.onSecondary(card)}
        />
      </div>
    </article>
  )
}

function ConvergenceChipRow({
  rows,
  extra,
}: {
  rows: BriefV2Convergence[]
  extra: number
}) {
  if (rows.length === 0 && extra === 0) return null
  return (
    <div className="briefv2-chip-row" role="list">
      {rows.map((r, i) => (
        <span
          key={i}
          className={`briefv2-chip briefv2-chip--${r.strength.toLowerCase()}`}
          role="listitem"
          title={r.signal ? `${r.source} — ${r.signal}` : r.source}
        >
          <span className="briefv2-chip-strength" aria-hidden="true" />
          <span className="briefv2-chip-source">{r.source}</span>
        </span>
      ))}
      {extra > 0 ? (
        <span className="briefv2-chip briefv2-chip--more" role="listitem">
          +{extra} more
        </span>
      ) : null}
    </div>
  )
}

function HeroQuote({ quote }: { quote: { body: string; source: string } }) {
  return (
    <figure className="briefv2-hero-quote">
      <blockquote>“{quote.body}”</blockquote>
      {quote.source ? (
        <figcaption>— {quote.source}</figcaption>
      ) : null}
    </figure>
  )
}

function CardActions({
  busy,
  secondaryLabel,
  accent,
  onViewEvidence,
  onAskAI,
  onSecondary,
}: {
  busy: boolean
  secondaryLabel: string
  accent: string
  onViewEvidence: () => void
  onAskAI: () => void
  onSecondary: () => void
}) {
  // Strip a trailing arrow from the label — the button affordance carries
  // the directionality; the glyph just wraps awkwardly on narrow cards.
  const cleanSecondary = busy
    ? "Generating…"
    : secondaryLabel.replace(/\s*→\s*$/, "")
  return (
    <div className="briefv2-card-actions">
      <button
        type="button"
        className="briefv2-action briefv2-action--ghost"
        onClick={onAskAI}
      >
        Ask
      </button>
      <button
        type="button"
        className="briefv2-action briefv2-action--ghost"
        onClick={onViewEvidence}
      >
        View evidence
      </button>
      <button
        type="button"
        className={`briefv2-action briefv2-action--primary briefv2-action--${accent}`}
        onClick={onSecondary}
        disabled={busy}
      >
        {cleanSecondary}
      </button>
    </div>
  )
}
