// Inline SVG glyphs for the v4 auth scenes. The design markup uses Tabler
// icon font classes (ti ti-*); this app does not load that font, so the
// equivalent glyphs are rendered inline to preserve the design's iconography
// without adding a webfont dependency.
import type { SVGProps } from "react"

const base = (props: SVGProps<SVGSVGElement>) => ({
  width: 16,
  height: 16,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 2,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
  ...props,
})

export function ArrowRight(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base(props)}>
      <line x1="5" y1="12" x2="19" y2="12" />
      <polyline points="12 5 19 12 12 19" />
    </svg>
  )
}

export function Eye(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base(props)}>
      <circle cx="12" cy="12" r="3" />
      <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z" />
    </svg>
  )
}

export function EyeOff(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base(props)}>
      <path d="M3 3l18 18" />
      <path d="M10.6 10.6a3 3 0 0 0 4.2 4.2" />
      <path d="M9.4 5.2A9.5 9.5 0 0 1 12 5c6.5 0 10 7 10 7a16 16 0 0 1-3 3.8" />
      <path d="M6.6 6.6A16 16 0 0 0 2 12s3.5 7 10 7a9.5 9.5 0 0 0 3-.5" />
    </svg>
  )
}

export function Google(props: SVGProps<SVGSVGElement>) {
  return (
    <svg width={16} height={16} viewBox="0 0 24 24" aria-hidden {...props}>
      <path fill="#4285F4" d="M22.5 12.2c0-.7-.06-1.4-.18-2.06H12v3.9h5.9a5 5 0 0 1-2.2 3.3v2.74h3.56c2.08-1.92 3.24-4.74 3.24-7.88z" />
      <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.56-2.74c-.98.66-2.24 1.06-3.72 1.06-2.86 0-5.28-1.93-6.14-4.53H2.18v2.84A11 11 0 0 0 12 23z" />
      <path fill="#FBBC05" d="M5.86 14.13a6.6 6.6 0 0 1 0-4.26V7.03H2.18a11 11 0 0 0 0 9.94l3.68-2.84z" />
      <path fill="#EA4335" d="M12 5.5c1.62 0 3.06.56 4.2 1.64l3.15-3.15A11 11 0 0 0 12 1 11 11 0 0 0 2.18 7.03l3.68 2.84C6.72 7.43 9.14 5.5 12 5.5z" />
    </svg>
  )
}

export function Key(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base(props)}>
      <circle cx="8" cy="15" r="4" />
      <path d="M10.8 12.2 20 3" />
      <path d="M17 6l2 2" />
      <path d="M15 8l2 2" />
    </svg>
  )
}

export function CircleCheck(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base(props)}>
      <circle cx="12" cy="12" r="9" />
      <path d="M8.5 12.5l2.5 2.5 4.5-5" />
    </svg>
  )
}

export function MailCheck(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base(props)}>
      <path d="M3 7a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v6" />
      <path d="M3 7l9 6 9-6" />
      <path d="M3 7v10a2 2 0 0 0 2 2h7" />
      <path d="M15 19l2 2 4-4" />
    </svg>
  )
}

export function Refresh(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base(props)}>
      <path d="M20 11a8 8 0 0 0-14.3-4.5L4 8" />
      <polyline points="4 4 4 8 8 8" />
      <path d="M4 13a8 8 0 0 0 14.3 4.5L20 16" />
      <polyline points="20 20 20 16 16 16" />
    </svg>
  )
}

export function InfoCircle(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base(props)}>
      <circle cx="12" cy="12" r="9" />
      <line x1="12" y1="11" x2="12" y2="16" />
      <circle cx="12" cy="8" r="0.6" fill="currentColor" stroke="none" />
    </svg>
  )
}

export function ArrowLeft(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base(props)}>
      <line x1="19" y1="12" x2="5" y2="12" />
      <polyline points="12 19 5 12 12 5" />
    </svg>
  )
}

export function Sparkles(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base(props)}>
      <path d="M12 3l1.6 4.4L18 9l-4.4 1.6L12 15l-1.6-4.4L6 9l4.4-1.6z" />
      <path d="M18 14l.7 1.9L20.6 16.6l-1.9.7L18 19.2l-.7-1.9-1.9-.7 1.9-.7z" />
    </svg>
  )
}

export function Plus(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base(props)}>
      <line x1="12" y1="5" x2="12" y2="19" />
      <line x1="5" y1="12" x2="19" y2="12" />
    </svg>
  )
}

export function Check(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base(props)}>
      <polyline points="5 12 10 17 19 7" />
    </svg>
  )
}

export function Palette(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base(props)}>
      <path d="M12 21a9 9 0 0 1 0 -18c4.97 0 9 3.582 9 8c0 1.06 -.474 2.078 -1.318 2.828c-.844 .75 -1.989 1.172 -3.182 1.172h-2.5a2 2 0 0 0 -1 3.75a1.3 1.3 0 0 1 -1 2.25" />
      <circle cx="7.5" cy="10.5" r="0.6" fill="currentColor" stroke="none" />
      <circle cx="12" cy="7.5" r="0.6" fill="currentColor" stroke="none" />
      <circle cx="16.5" cy="10.5" r="0.6" fill="currentColor" stroke="none" />
    </svg>
  )
}

export function ChartBar(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base(props)}>
      <rect x="3" y="12" width="6" height="8" rx="1" />
      <rect x="9" y="8" width="6" height="12" rx="1" />
      <rect x="15" y="4" width="6" height="16" rx="1" />
      <line x1="4" y1="20" x2="18" y2="20" />
    </svg>
  )
}

export function Settings(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base(props)}>
      <path d="M10.325 4.317c.426 -1.756 2.924 -1.756 3.35 0a1.724 1.724 0 0 0 2.573 1.066c1.543 -.94 3.31 .826 2.37 2.37a1.724 1.724 0 0 0 1.065 2.572c1.756 .426 1.756 2.924 0 3.35a1.724 1.724 0 0 0 -1.066 2.573c.94 1.543 -.826 3.31 -2.37 2.37a1.724 1.724 0 0 0 -2.572 1.065c-.426 1.756 -2.924 1.756 -3.35 0a1.724 1.724 0 0 0 -2.573 -1.066c-1.543 .94 -3.31 -.826 -2.37 -2.37a1.724 1.724 0 0 0 -1.065 -2.572c-1.756 -.426 -1.756 -2.924 0 -3.35a1.724 1.724 0 0 0 1.066 -2.573c-.94 -1.543 .826 -3.31 2.37 -2.37c1 .608 2.296 .07 2.572 -1.065" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  )
}

export function Trash(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base(props)}>
      <line x1="4" y1="7" x2="20" y2="7" />
      <path d="M10 11v6" />
      <path d="M14 11v6" />
      <path d="M5 7l1 12a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2l1-12" />
      <path d="M9 7V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v3" />
    </svg>
  )
}
