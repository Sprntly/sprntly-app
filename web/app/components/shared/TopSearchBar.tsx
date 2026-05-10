"use client"

import { useCallback, useState } from "react"
import { useNavigation } from "../../context/NavigationContext"
import { useContent } from "../../context/ContentContext"
import { ONBOARDING_SCREENS } from "../../types"
import { ApiError, askApi } from "../../lib/api"

export function TopSearchBar() {
  const { currentScreen, goTo, setPendingSearchHandoff, showToast } = useNavigation()
  const { content, setContent } = useContent()
  const [q, setQ] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const runSearch = useCallback(async () => {
    const query = q.trim()
    if (query.length < 3) {
      showToast("Search too short", "Use at least 3 characters.")
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      const res = await askApi.ask(query)

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

      setPendingSearchHandoff({ query, reply: res })
      goTo("ondemand")
      setQ("")
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
  }, [q, content.conversations.length, goTo, setContent, setPendingSearchHandoff, showToast])

  if (ONBOARDING_SCREENS.includes(currentScreen)) {
    return null
  }

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
        {error ? (
          <div className="app-top-search-error" role="alert">
            {error}
          </div>
        ) : null}
      </div>
    </header>
  )
}
