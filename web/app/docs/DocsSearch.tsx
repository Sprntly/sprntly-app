"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { IconSearch, IconCornerDownLeft } from "@tabler/icons-react"
import { publicPath } from "../lib/public-path"
import { searchDocs, type SearchResult } from "./content"

/**
 * Search box with a live results dropdown, searching across every section of
 * every doc (client-side, over the hardcoded content). Enter or click a result
 * navigates to /docs/<slug>#<sectionId>. Shared by the docs home and the doc
 * reader.
 */
export function DocsSearch({
  placeholder = "Search the docs…",
  autoFocus = false,
}: {
  placeholder?: string
  autoFocus?: boolean
}) {
  const router = useRouter()
  const [query, setQuery] = useState("")
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const rootRef = useRef<HTMLDivElement>(null)

  const results = useMemo<SearchResult[]>(() => searchDocs(query), [query])

  useEffect(() => setActive(0), [query])

  // Close on outside click.
  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener("mousedown", onDocClick)
    return () => document.removeEventListener("mousedown", onDocClick)
  }, [])

  function go(r: SearchResult) {
    setOpen(false)
    setQuery("")
    router.push(publicPath(`/docs/${r.slug}#${r.sectionId}`))
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (!open || results.length === 0) return
    if (e.key === "ArrowDown") {
      e.preventDefault()
      setActive((a) => (a + 1) % results.length)
    } else if (e.key === "ArrowUp") {
      e.preventDefault()
      setActive((a) => (a - 1 + results.length) % results.length)
    } else if (e.key === "Enter") {
      e.preventDefault()
      go(results[active])
    } else if (e.key === "Escape") {
      setOpen(false)
    }
  }

  const showDropdown = open && query.trim().length > 0

  return (
    <div className="docs-search" ref={rootRef}>
      <div className="docs-search-field">
        <IconSearch size={17} stroke={1.8} className="docs-search-icon" />
        <input
          type="text"
          value={query}
          placeholder={placeholder}
          autoFocus={autoFocus}
          onChange={(e) => {
            setQuery(e.target.value)
            setOpen(true)
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
          aria-label="Search the documentation"
        />
      </div>

      {showDropdown ? (
        <div className="docs-search-results" role="listbox">
          {results.length === 0 ? (
            <div className="docs-search-empty">
              No results for “{query.trim()}”
            </div>
          ) : (
            results.map((r, i) => (
              <button
                key={`${r.slug}-${r.sectionId}`}
                type="button"
                role="option"
                aria-selected={i === active}
                className={`docs-search-result${i === active ? " is-active" : ""}`}
                onMouseEnter={() => setActive(i)}
                onClick={() => go(r)}
              >
                <div className="docs-search-result-head">
                  <span className="docs-search-result-title">
                    {r.sectionTitle}
                  </span>
                  <span className="docs-search-result-doc">{r.docTitle}</span>
                </div>
                <div className="docs-search-result-snippet">{r.snippet}</div>
                {i === active ? (
                  <IconCornerDownLeft
                    size={14}
                    stroke={1.8}
                    className="docs-search-result-enter"
                  />
                ) : null}
              </button>
            ))
          )}
        </div>
      ) : null}
    </div>
  )
}
