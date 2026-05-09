"use client"

type EmptyPaneProps = {
  title: string
  hint?: string
  /** Number of dashed placeholder rows (cards) */
  placeholders?: number
}

export function EmptyPane({ title, hint, placeholders = 0 }: EmptyPaneProps) {
  return (
    <div className="empty-pane">
      <div className="empty-pane-inner">
        <div className="empty-pane-title">{title}</div>
        {hint ? <p className="empty-pane-hint">{hint}</p> : null}
      </div>
      {placeholders > 0 ? (
        <div className="empty-pane-cards">
          {Array.from({ length: placeholders }, (_, i) => (
            <div key={i} className="empty-pane-card" aria-hidden />
          ))}
        </div>
      ) : null}
    </div>
  )
}
