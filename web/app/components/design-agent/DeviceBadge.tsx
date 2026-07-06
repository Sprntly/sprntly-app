import { IconMonitor, IconPhone } from "../shared/app-icons"

/** Static, non-interactive device pill shown in the public viewer's header slot
 *  when a shared prototype targets a SINGLE device — it replaces the Desktop/Mobile
 *  toggle (there is nothing to toggle to) and tells the recipient which form factor
 *  the prototype was built for. Mirrors the internal single-device indicator
 *  (`.proto-fs-device` in PostGenerationResult), reusing the same shared
 *  `IconPhone` / `IconMonitor` line icons for visual consistency.
 *
 *  Renders nothing for "both"/legacy/null — the caller shows the normal toggle in
 *  that case. Display-only: not keyboard-focusable, no hover/focus state (see the
 *  `.device-badge` rule in design-agent.css). SSR-safe leaf (no hooks / no state).
 */
export function DeviceBadge({ platform }: { platform: string }) {
  if (platform !== "mobile" && platform !== "desktop") return null
  const isMobile = platform === "mobile"
  const label = isMobile ? "Mobile" : "Desktop"
  return (
    <div className="device-badge" aria-label={`${label} prototype`}>
      {isMobile ? <IconPhone size={12} /> : <IconMonitor size={12} />}
      {label}
    </div>
  )
}
