// v4 auth shell — ported from the design's .auth-shell / .auth-brand /
// .auth-card structure: centered branding (sprntly + brand-green dot) with a
// pill tag, a single card, and a footer meta line.
import type { ReactNode } from "react"

export function AuthShell({
  children,
  tag,
  cardClassName,
  showMeta = true,
}: {
  children: ReactNode
  tag?: string
  cardClassName?: string
  showMeta?: boolean
}) {
  return (
    <div className="auth-shell">
      <div className="auth-brand">
        <span className="auth-logo">
          sprntly<span className="dot">.</span>
        </span>
        {tag && <span className="auth-tag">{tag}</span>}
      </div>
      <div className={`auth-card${cardClassName ? ` ${cardClassName}` : ""}`}>{children}</div>
      {showMeta && (
        <div className="auth-foot-meta">Sprntly · Product Intelligence for PMs</div>
      )}
    </div>
  )
}
