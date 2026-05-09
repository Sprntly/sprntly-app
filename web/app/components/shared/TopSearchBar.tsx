"use client"

import { useCallback, useEffect, useState } from "react"
import { useNavigation } from "../../context/NavigationContext"
import { useContent } from "../../context/ContentContext"
import { ONBOARDING_SCREENS } from "../../types"
import { ApiError, askApi, type AskResponse } from "../../lib/api"

export function TopSearchBar() {
  const { currentScreen, goTo, setAIBarValue, showToast } = useNavigation()
  const { content, setContent } = useContent()
  const [q, setQ] = useState("")
  const [open, setOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [result, setResult] = useState<AskResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [lastQuery, setLastQuery] = useState("")

  const runSearch = useCallback(async () => {
    const query = q.trim()
    if (query.length < 3) {
      showToast("Search too short", "Use at least 3 characters.")
      return
    }
    setSubmitting(true)
    setError(null)
    setResult(null)
    setLastQuery(query)
    setOpen(true)
    try {
      const res = await askApi.ask(query)
      setResult(res)

      const convId =
        typeof crypto !== "undefined" && crypto.randomUUID
          ? crypto.randomUUID()
          : `search-${Date.now()}`
      const title = query.length > 52 ? `${query.slice(0, 49)}…` : query
      const timeStr = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
      const n = content.conversations.length + 1
      setContent({
        conversations: [{ id: convId, title, time: timeStr }, ...content.conversations],
        sidebarConvCount: n,
      })
    } catch (e) {
      const detail = e instanceof ApiError && e.body && typeof e.body === "object" && "detail" in e.body
        ? (e.body as { detail: unknown }).detail
        : null
      const detailStr =
        typeof detail === "string"
          ? detail
          : Array.isArray(detail)
            ? detail.map((x) => (typeof x === "object" && x && "msg" in x ? String((x as { msg: string }).msg) : String(x))).join(" · ")
            : null
      const msg =
        e instanceof ApiError ? detailStr || e.message : e instanceof Error ? e.message : "Search failed"
      setError(msg)
      showToast("Search failed", msg.slice(0, 120))
    } finally {
      setSubmitting(false)
    }
  }, [q, content.conversations.length, setContent, showToast])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false)
    }
    document.addEventListener("keydown", onKey)
    return () => document.removeEventListener("keydown", onKey)
  }, [open])

  if (ONBOARDING_SCREENS.includes(currentScreen)) {
    return null
  }

  const showPanel = open && (submitting || error || result)

  return (
    <header className="app-top-search">
      <div className="app-top-search-inner">
        <form
          className="app-top-search-row"
          role="search"
          onSubmit={(e) => {
            e.preventDefault()
            void runSearch()
          }}
        >
          <span className="app-top-search-icon" aria-hidden>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="11" cy="11" r="7" />
              <path d="m21 21-4.3-4.3" strokeLinecap="round" />
            </svg>
          </span>
          <div className="app-top-search-input-wrap">
            <input
              type="search"
              className="app-top-search-input"
              placeholder="Search product memory…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              aria-label="Search product memory"
              autoComplete="off"
            />
            <button type="submit" className="app-top-search-submit" disabled={submitting || q.trim().length < 3}>
              {submitting ? "…" : "Search"}
            </button>
          </div>
        </form>
        <div className="app-top-search-hint">Powered by the same Q&amp;A as Ask Sprntly · Enter to run</div>

        {showPanel ? (
          <div className="app-top-search-panel">
            {submitting ? (
              <div className="app-top-search-panel-loading">Searching corpus…</div>
            ) : error ? (
              <div className="app-top-search-panel-error">{error}</div>
            ) : result ? (
              <>
                <div className="ai-bar-reply-answer">{result.answer}</div>
                {result.key_points?.length ? (
                  <ul className="ai-bar-reply-kp">
                    {result.key_points.map((kp, i) => (
                      <li key={i}>{kp}</li>
                    ))}
                  </ul>
                ) : null}
                {result.citations?.length ? (
                  <div className="ai-bar-reply-cites">
                    {result.citations.map((c, i) => (
                      <div key={i} className="ai-bar-reply-cite">
                        <div className="ai-bar-reply-cite-src">{c.source}</div>
                        <div className="ai-bar-reply-cite-ev">{c.evidence}</div>
                      </div>
                    ))}
                  </div>
                ) : null}
                {result.unanswered ? (
                  <div className="ai-bar-reply-gap">Gap: {result.unanswered}</div>
                ) : null}
                <div className="app-top-search-panel-actions">
                  <button
                    type="button"
                    className="btn btn-sm"
                    onClick={() => {
                      setAIBarValue(lastQuery)
                      goTo("ondemand")
                      setOpen(false)
                    }}
                  >
                    Open in Ask Sprntly
                  </button>
                  <button type="button" className="btn btn-ghost btn-sm" onClick={() => setOpen(false)}>
                    Close
                  </button>
                </div>
              </>
            ) : null}
          </div>
        ) : null}
      </div>
    </header>
  )
}
