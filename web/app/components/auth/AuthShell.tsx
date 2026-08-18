// v4 auth shell — ported from the design's .auth-shell / .auth-brand /
// .auth-card structure: centered branding (sprntly + brand-green dot) with a
// pill tag, a single card, and a footer meta line.
import type { ReactNode } from "react"
import { SprntlyLockup } from "../shared/SprntlyMark"

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
        {/* The drawn lockup, not the word typed in the UI font with a green
            full stop after it. This is the first screen anyone sees and the
            only one they see signed out, so it is the place the brand should
            actually be the brand. */}
        <span className="auth-logo">
          <SprntlyLockup height={26} />
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
