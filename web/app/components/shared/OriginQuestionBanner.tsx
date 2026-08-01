"use client"

/**
 * Renders the chat question that produced a PRD/Evidence doc (`PrdContent.question`
 * — see db/reports.py's identical `question` precedent, extended to `prds`/
 * `evidences`), right above the document frame — mirrors how `PrdPatchBanner`
 * mounts above the PRD frame.
 *
 * Deliberately a plain text strip, NOT a chat-thread renderer: rendering the full
 * connected conversation (ChatScreen/BriefChat) was investigated and found to need
 * a genuinely new component (neither is a drop-in presentational piece — both are
 * large stateful orchestrators with no extracted turn-list renderer), which this
 * ticket flags rather than builds unilaterally. This banner is the safe subset:
 * the single `question` field the artifact row itself carries, which needs no new
 * component and no conversation-privacy exception (see NavigationContext /
 * conversations.py: chats are per-user-private; the `question` column is not).
 *
 * Renders nothing when there's no question — a doc generated before this shipped
 * (or from any non-chat path) simply shows no banner (graceful, not broken).
 */
// Inline styles rather than a globals.css addition — mirrors the ad hoc inline
// style objects already used for small strips like this throughout
// EvidenceScreen.tsx/ContentPanel.tsx, and keeps this component's CSS
// co-located rather than growing the shared stylesheet.
const wrapStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "baseline",
  gap: 8,
  padding: "10px 14px",
  marginBottom: 14,
  borderRadius: 8,
  background: "var(--surface-2, #F4F1EA)",
  border: "1px solid var(--line, #E8E6E0)",
  fontSize: 12.5,
  lineHeight: 1.5,
}

const labelStyle: React.CSSProperties = {
  fontWeight: 600,
  color: "var(--ink-3, #8C8A84)",
  whiteSpace: "nowrap",
}

const textStyle: React.CSSProperties = {
  color: "var(--ink-2, #5A5853)",
  fontStyle: "italic",
  overflow: "hidden",
  textOverflow: "ellipsis",
}

export function OriginQuestionBanner({ question }: { question?: string | null }) {
  const text = (question ?? "").trim()
  if (!text) return null
  return (
    <div style={wrapStyle} data-testid="origin-question-banner">
      <span style={labelStyle}>Generated from</span>
      <span style={textStyle}>&ldquo;{text}&rdquo;</span>
    </div>
  )
}
