import { Fragment, type ReactNode } from "react"

// Shared empty-state primitive for the prototype route. One component covers
// every non-canvas surface on /prototype: the simple prompts (no PRD selected)
// and the rich landing hero that introduces generation. The hero is just the
// richest configuration of this same primitive — not a separate component —
// so the markup, classes, and styling stay in one place.
//
// Simple config: pass `title`, `sub`, and an `action` slot. Renders the
// established da-prototype-empty / -title / -sub markup.
//
// Hero config: set `variant="hero"` and additionally pass `art` (the icon
// tile content), `meta` (short qualifier strings shown with dot separators),
// and `chips` (icon + label highlight pills). The action slot carries the CTA
// button (already styled by the caller).

export type EmptyStateChip = {
  icon: ReactNode
  label: string
}

export type PrototypeEmptyStateProps = {
  testid: string
  title: string
  sub: ReactNode
  action?: ReactNode
  variant?: "default" | "hero"
  art?: ReactNode
  meta?: ReactNode[]
  chips?: EmptyStateChip[]
}

export function PrototypeEmptyState({
  testid,
  title,
  sub,
  action,
  variant = "default",
  art,
  meta,
  chips,
}: PrototypeEmptyStateProps) {
  const isHero = variant === "hero"
  const rootClass = isHero
    ? "design-agent-surface da-prototype-empty da-empty-hero-stage"
    : "design-agent-surface da-prototype-empty"

  if (isHero) {
    return (
      <div className={rootClass} data-testid={testid}>
        <div className="da-empty-hero">
          {art != null && (
            <div className="da-empty-hero-art" aria-hidden="true">
              {art}
            </div>
          )}
          <h2 className="da-empty-hero-title">{title}</h2>
          <p className="da-empty-hero-sub">{sub}</p>
          {action}
          {meta != null && meta.length > 0 && (
            <div className="da-empty-hero-meta">
              {meta.map((item, index) => (
                <Fragment key={index}>
                  {index > 0 && (
                    <span className="da-empty-hero-dot" aria-hidden="true" />
                  )}
                  <span>{item}</span>
                </Fragment>
              ))}
            </div>
          )}
          {chips != null && chips.length > 0 && (
            <div className="da-empty-hero-chips">
              {chips.map((chip, index) => (
                <span className="da-empty-hero-chip" key={index}>
                  {chip.icon}
                  {chip.label}
                </span>
              ))}
            </div>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className={rootClass} data-testid={testid}>
      <h2 className="da-prototype-empty-title">{title}</h2>
      <p className="da-prototype-empty-sub">{sub}</p>
      {action}
    </div>
  )
}
