"use client"

import ReactMarkdown, { type Components } from "react-markdown"
import remarkGfm from "remark-gfm"

/**
 * Markdown renderer for doc bodies. Semantic HTML only — all styling lives in
 * the `.docs-prose` block in globals.css. GFM tables/lists come from
 * remark-gfm. Blockquotes render as green callout boxes via `.docs-prose
 * blockquote`.
 */
const components: Components = {
  a({ href, children, ...rest }) {
    const external = !!href && /^https?:\/\//.test(href)
    return (
      <a
        href={href}
        {...(external ? { target: "_blank", rel: "noopener noreferrer" } : {})}
        {...rest}
      >
        {children}
      </a>
    )
  },
  table({ children }) {
    return (
      <div className="docs-table-wrap">
        <table>{children}</table>
      </div>
    )
  },
}

export function DocMarkdown({ body }: { body: string }) {
  return (
    <div className="docs-prose">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {body}
      </ReactMarkdown>
    </div>
  )
}
