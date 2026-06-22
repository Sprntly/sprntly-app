import type { ChatCardIconId } from "../../types/content"

const stroke = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.75,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
}

export function IconClose({ size = 14, title }: { size?: number; title?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden={title ? undefined : true} role={title ? "img" : undefined}>
      {title ? <title>{title}</title> : null}
      <path d="M6 6l12 12M18 6L6 18" {...stroke} />
    </svg>
  )
}

export function IconCheck({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="M5 12.5l4.5 4.5L19 7" {...stroke} />
    </svg>
  )
}

/** Plus / add — primary "Generate" action affordance. */
export function IconPlus({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="M12 5v14M5 12h14" {...stroke} />
    </svg>
  )
}

/** Small stroke sparkle (replaces decorative star dingbat). */
export function IconSparkle({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M12 3v5M12 16v5M3 12h5M16 12h5M6.5 6.5l3 3M14.5 14.5l3 3M17.5 6.5l-3 3M10.5 14.5l-3 3"
        stroke="currentColor"
        strokeWidth="1.35"
        strokeLinecap="round"
      />
    </svg>
  )
}

export function IconSendUp({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="M12 19V5M12 5l-5 5M12 5l5 5" {...stroke} />
    </svg>
  )
}

export function IconMessage({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="M8 10h.01M12 10h.01M16 10h.01M5 18V6a2 2 0 012-2h10a2 2 0 012 2v8a2 2 0 01-2 2H8l-3 3z" {...stroke} />
    </svg>
  )
}

export function IconChart({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="M4 19h16M7 15v-4m5 4V8m5 7V5" {...stroke} />
    </svg>
  )
}

export function IconDocument({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8l-6-6z" {...stroke} />
      <path d="M14 2v6h6M9 13h6M9 17h4" {...stroke} />
    </svg>
  )
}

export function IconRocket({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="M5 15l-2 5 5-2 11-11-4-4L5 15zM12 4l4 4" {...stroke} />
      <path d="M9 18l-2 2M14 9h.01" stroke="currentColor" strokeWidth={2} strokeLinecap="round" />
    </svg>
  )
}

export function IconDiamond({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="M12 3l8.5 9L12 21 3.5 12 12 3z" {...stroke} />
    </svg>
  )
}

export function IconMail({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="M4 6h16v12H4V6z" {...stroke} />
      <path d="M4 7l8 6 8-6" {...stroke} />
    </svg>
  )
}

/** Copy / duplicate (share link). */
export function IconCopy({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
      <rect x="8" y="8" width="12" height="12" rx="2" {...stroke} />
      <path d="M4 16V6a2 2 0 012-2h10" {...stroke} />
    </svg>
  )
}

export function IconGrid({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
      <rect x="4" y="4" width="6" height="6" rx="1" {...stroke} />
      <rect x="14" y="4" width="6" height="6" rx="1" {...stroke} />
      <rect x="4" y="14" width="6" height="6" rx="1" {...stroke} />
      <rect x="14" y="14" width="6" height="6" rx="1" {...stroke} />
    </svg>
  )
}

/** Two overlapping rectangles — insert link. */
export function IconLinkInsert({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
      <rect x="3" y="5" width="12" height="12" rx="2" {...stroke} />
      <rect x="9" y="7" width="12" height="12" rx="2" {...stroke} />
    </svg>
  )
}

export function IconUndo({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="M9 14H5l-2-4 2-4h4M5 10h11a4 4 0 014 4v0a4 4 0 01-4 4h-7" {...stroke} />
    </svg>
  )
}

export function IconRedo({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="M15 14h4l2-4-2-4h-4M19 10H8a4 4 0 00-4 4v0a4 4 0 004 4h7" {...stroke} />
    </svg>
  )
}

export function IconListBullet({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="M9 6h12M9 12h12M9 18h12M5 6h.01M5 12h.01M5 18h.01" {...stroke} />
    </svg>
  )
}

/** Circular-arrow reload — manual "refresh preview" affordance. */
export function IconRefresh({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="M21 12a9 9 0 1 1-2.64-6.36M21 4v5h-5" {...stroke} />
    </svg>
  )
}

// UX-EXPLORE (throwaway — REVERT): control-bar + sidebar-collapse icons for the
// reworked post-generation canvas. Same inline-stroke style as the set above.
export function IconFullscreen({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" {...stroke} />
    </svg>
  )
}

export function IconChevronLeft({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="M15 6l-6 6 6 6" {...stroke} />
    </svg>
  )
}

// UX-EXPLORE (throwaway — REVERT): compact control-bar icons — Share / overflow /
// chevron-down for the reworked post-gen tool bar. Same inline-stroke style.
export function IconChevronDown({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="M6 9l6 6 6-6" {...stroke} />
    </svg>
  )
}

export function IconShare({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="M4 12v7a1 1 0 001 1h14a1 1 0 001-1v-7M12 3v12M12 3L8 7M12 3l4 4" {...stroke} />
    </svg>
  )
}

// UX-EXPLORE (throwaway — REVERT, CHANGE 3): map-pin / "Mark & comment" tool icon
// (David's `ti-pin`). Same inline-stroke style as the set above.
export function IconPin({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="M12 21s7-6.5 7-12a7 7 0 10-14 0c0 5.5 7 12 7 12z" {...stroke} />
      <circle cx="12" cy="9" r="2.5" {...stroke} />
    </svg>
  )
}

export function IconMore({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle cx="5" cy="12" r="1.4" fill="currentColor" stroke="none" />
      <circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none" />
      <circle cx="19" cy="12" r="1.4" fill="currentColor" stroke="none" />
    </svg>
  )
}

// UX-EXPLORE (throwaway — REVERT, CHANGE 2/3): an "open / go to" arrow used by the
// canvas breadcrumb chevrons and the PRD-screen prototype preview card's open
// affordance. Same inline-stroke style as the rest of the set.
export function IconArrowRight({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="M5 12h14M13 6l6 6-6 6" {...stroke} />
    </svg>
  )
}

const LEGACY_CARD_ICON: Record<string, ChatCardIconId> = {
  "✦": "sparkle",
  "💬": "message",
  "📈": "chart",
  "◇": "diamond",
  "📄": "document",
  "🚀": "rocket",
}

function resolveCardIcon(id: ChatCardIconId | string): ChatCardIconId {
  if (
    id === "sparkle" ||
    id === "message" ||
    id === "chart" ||
    id === "diamond" ||
    id === "document" ||
    id === "rocket"
  ) {
    return id
  }
  return LEGACY_CARD_ICON[id] ?? "sparkle"
}

export function ChatSuggestionIcon({ id, size = 18 }: { id: ChatCardIconId | string; size?: number }) {
  switch (resolveCardIcon(id)) {
    case "sparkle":
      return <IconSparkle size={size} />
    case "message":
      return <IconMessage size={size} />
    case "chart":
      return <IconChart size={size} />
    case "document":
      return <IconDocument size={size} />
    case "rocket":
      return <IconRocket size={size} />
    case "diamond":
      return <IconDiamond size={size} />
  }
}
