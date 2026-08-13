"use client"

import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"

/**
 * Renders a `skill=="saved-chat"` report's stored body — RAW markdown, not an
 * HTML document — as rich prose: headings, bold/italic, lists, tables.
 *
 * Deliberately the SAME renderer `AskReplyBody.tsx` uses for an ordinary
 * markdown answer (`<ReactMarkdown remarkPlugins={[remarkGfm]}>`, WITHOUT
 * `rehype-raw`) — one markdown rendering path in this app, not two. No
 * `rehype-raw` is the whole safety property: react-markdown never executes
 * raw HTML embedded in the source, so a `<script>` tag in a saved chat output
 * prints as inert text instead of running. `HtmlReportView`'s sandboxed
 * iframe is a DIFFERENT tool for a DIFFERENT shape of content — a
 * self-contained HTML document (VoC, competitive-intelligence, ...) — and
 * stays untouched; this component is only ever handed markdown.
 *
 * `ai-bar-reply-answer` reuses AskReplyBody's answer-prose styling
 * (globals.css) rather than inventing a second one.
 */
export function SavedChatMarkdown({ markdown }: { markdown: string }) {
  return (
    <div className="ai-bar-reply-answer" data-testid="saved-chat-markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{markdown}</ReactMarkdown>
    </div>
  )
}
