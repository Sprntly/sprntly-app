/**
 * Tiny inline-markdown renderer for PRD paragraphs and list items.
 * Supports: **bold**, *italic*, _italic_, `code`, [text](url).
 * Not a full markdown parser — just enough to make the PRD body look like
 * formatted prose instead of raw asterisks and backticks.
 */
import type { ReactNode } from "react"

type Token =
  | { type: "text"; value: string }
  | { type: "bold"; children: ReactNode }
  | { type: "italic"; children: ReactNode }
  | { type: "code"; value: string }
  | { type: "link"; href: string; children: ReactNode }

const PATTERN =
  /(\*\*([^*]+)\*\*|__([^_]+)__|\*([^*\n]+)\*|_([^_\n]+)_|`([^`]+)`|\[([^\]]+)\]\(([^)]+)\))/

function tokenize(text: string): Token[] {
  const out: Token[] = []
  let rest = text
  while (rest.length > 0) {
    const m = rest.match(PATTERN)
    if (!m || m.index === undefined) {
      out.push({ type: "text", value: rest })
      break
    }
    if (m.index > 0) out.push({ type: "text", value: rest.slice(0, m.index) })
    const matched = m[0]
    if (matched.startsWith("**") || matched.startsWith("__")) {
      const inner = (m[2] ?? m[3]) || ""
      out.push({ type: "bold", children: renderInline(inner) })
    } else if (matched.startsWith("*") || matched.startsWith("_")) {
      const inner = (m[4] ?? m[5]) || ""
      out.push({ type: "italic", children: renderInline(inner) })
    } else if (matched.startsWith("`")) {
      out.push({ type: "code", value: m[6] ?? "" })
    } else if (matched.startsWith("[")) {
      const linkText = m[7] ?? ""
      const href = m[8] ?? ""
      out.push({ type: "link", href, children: renderInline(linkText) })
    }
    rest = rest.slice(m.index + matched.length)
  }
  return out
}

export function renderInline(text: string): ReactNode {
  if (!text) return null
  const tokens = tokenize(text)
  return tokens.map((t, i) => {
    if (t.type === "text") return <span key={i}>{t.value}</span>
    if (t.type === "bold") return <strong key={i}>{t.children}</strong>
    if (t.type === "italic") return <em key={i}>{t.children}</em>
    if (t.type === "code") return <code key={i}>{t.value}</code>
    if (t.type === "link") {
      const safe = /^https?:\/\//i.test(t.href) ? t.href : "#"
      return (
        <a key={i} href={safe} target="_blank" rel="noreferrer noopener">
          {t.children}
        </a>
      )
    }
    return null
  })
}
