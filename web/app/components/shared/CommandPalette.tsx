"use client"

import {
  useCallback,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import { useRouter } from "next/navigation"
import {
  IconBookmark,
  IconBuildings,
  IconFiles,
  IconFileText,
  IconHistory,
  IconMessage,
  IconMessageCircle,
  IconPlug,
  IconPrompt,
  IconSearch,
  IconSettings,
  IconUser,
  IconWand,
} from "@tabler/icons-react"
import { useNavigation } from "../../context/NavigationContext"
import { useWorkspace } from "../../context/WorkspaceContext"
import { useCompany } from "../../context/CompanyContext"
import { buildStaticItems } from "../../lib/search/registry"
import { fetchDynamicItems } from "../../lib/search/providers"
import { getRecents, pushRecent } from "../../lib/search/recents"
import { GROUP_LABELS, searchItems } from "../../lib/search/score"
import type {
  MatchRange,
  ResultGroup,
  ScoredItem,
  SearchItem,
} from "../../lib/search/types"

// ── Global search / command palette (⌘K) ─────────────────────────────────────
//
// DocSearch-style top-aligned modal: one input, grouped results (pages,
// settings panes, skills, chats, artifacts, …) with breadcrumbs + URLs, full
// keyboard nav, and per-workspace recents when the query is empty. Static
// items (registry.ts) render instantly; workspace entities stream in from the
// session-cached providers (providers.ts). Parent owns only `open`/`onClose`
// (the FeedbackModal contract); AppShell renders it and binds Cmd/Ctrl+K.

const ICONS: Record<string, React.ComponentType<{ size?: number }>> = {
  chat: IconMessage,
  brief: IconMessageCircle,
  history: IconHistory,
  artifact: IconFiles,
  template: IconBookmark,
  skill: IconWand,
  source: IconFileText,
  team: IconUser,
  person: IconUser,
  settings: IconSettings,
  doc: IconFileText,
  connector: IconPlug,
  prototype: IconPrompt,
  workspace: IconBuildings,
}

function ItemIcon({ iconId }: { iconId: string }) {
  const Icon = ICONS[iconId] ?? IconFileText
  return <Icon size={16} />
}

/** Title with <mark> highlights over the matched ranges. */
function HighlightedTitle({
  title,
  ranges,
}: {
  title: string
  ranges: MatchRange[]
}) {
  if (ranges.length === 0) return <>{title}</>
  const parts: React.ReactNode[] = []
  let cursor = 0
  ranges.forEach((r, i) => {
    if (r.start > cursor) parts.push(title.slice(cursor, r.start))
    parts.push(
      <mark key={i} className="cmdp-hl">
        {title.slice(r.start, r.end)}
      </mark>,
    )
    cursor = r.end
  })
  if (cursor < title.length) parts.push(title.slice(cursor))
  return <>{parts}</>
}

export function CommandPalette({
  open,
  onClose,
}: {
  open: boolean
  onClose: () => void
}) {
  const router = useRouter()
  const { goTo, goToNewChat, openPrdTab } = useNavigation()
  const { workspaces = [], activeWorkspace, setActiveWorkspace } = useWorkspace()
  const { activeCompany } = useCompany()

  const [query, setQuery] = useState("")
  const [activeIndex, setActiveIndex] = useState(0)
  const [dynamicItems, setDynamicItems] = useState<SearchItem[] | null>(null)
  const [recents, setRecents] = useState<SearchItem[]>([])
  const [isMac, setIsMac] = useState(false)

  const inputRef = useRef<HTMLInputElement>(null)
  const activeRowRef = useRef<HTMLButtonElement>(null)
  /** Bumped on every open/workspace change so stale fetches are ignored. */
  const seqRef = useRef(0)

  const workspaceId = activeWorkspace?.id ?? null

  useEffect(() => {
    setIsMac(/mac/i.test(navigator.platform))
  }, [])

  // On open: reset, focus, load recents, kick the dynamic fan-out.
  useEffect(() => {
    if (!open) return
    setQuery("")
    setActiveIndex(0)
    setRecents(workspaceId ? getRecents(workspaceId) : [])
    requestAnimationFrame(() => inputRef.current?.focus())
    if (!workspaceId) return
    const seq = ++seqRef.current
    setDynamicItems(null)
    fetchDynamicItems(workspaceId, { activeCompany })
      .then((items) => {
        if (seqRef.current === seq) setDynamicItems(items)
      })
      .catch(() => {
        if (seqRef.current === seq) setDynamicItems([])
      })
  }, [open, workspaceId, activeCompany])

  const staticItems = useMemo(() => buildStaticItems(), [])

  const workspaceItems = useMemo<SearchItem[]>(
    () =>
      workspaces.length > 1
        ? workspaces.map((w) => ({
            id: `ws:${w.id}`,
            group: "workspaces" as const,
            title: w.name,
            subtitle:
              w.id === workspaceId ? "Current workspace" : "Switch workspace",
            breadcrumb: ["Workspaces"],
            keywords: ["workspace", "switch"],
            iconId: "workspace",
            action: { kind: "switch-workspace" as const, workspaceId: w.id },
          }))
        : [],
    [workspaces, workspaceId],
  )

  const allItems = useMemo(
    () => [...staticItems, ...workspaceItems, ...(dynamicItems ?? [])],
    [staticItems, workspaceItems, dynamicItems],
  )

  // Filtering is in-memory; deferral just keeps typing smooth on big lists.
  const deferredQuery = useDeferredValue(query)

  const groups = useMemo<ResultGroup[]>(() => {
    if (deferredQuery.trim()) return searchItems(deferredQuery, allItems)
    // Empty query — recents first, then the app's pages as a browsable index.
    const empty: ResultGroup[] = []
    if (recents.length > 0) {
      empty.push({
        group: "recent",
        label: GROUP_LABELS.recent,
        items: recents.map((item) => ({ item, score: 0, titleRanges: [] })),
      })
    }
    empty.push({
      group: "pages",
      label: GROUP_LABELS.pages,
      items: staticItems
        .filter((i) => i.group === "pages" || i.group === "actions")
        .map((item) => ({ item, score: 0, titleRanges: [] })),
    })
    return empty
  }, [deferredQuery, allItems, recents, staticItems])

  const flat = useMemo<ScoredItem[]>(
    () => groups.flatMap((g) => g.items),
    [groups],
  )

  // Keep the selection inside the list as results change; reset on new query.
  useEffect(() => {
    setActiveIndex(0)
  }, [deferredQuery])
  const clampedIndex = Math.min(activeIndex, Math.max(0, flat.length - 1))

  useEffect(() => {
    activeRowRef.current?.scrollIntoView({ block: "nearest" })
  }, [clampedIndex, groups])

  const perform = useCallback(
    (scored: ScoredItem) => {
      const { item } = scored
      if (workspaceId) pushRecent(workspaceId, item)
      const action = item.action
      switch (action.kind) {
        case "path":
          router.push(action.path)
          break
        case "screen":
          goTo(action.screen)
          break
        case "new-chat":
          goToNewChat()
          break
        case "resume-chat":
          // The ChatsScreen resume handoff: ChatScreen hydrates the turns.
          try {
            localStorage.setItem(
              "sprntly_resume_conv",
              JSON.stringify({
                dbId: action.dbId,
                title: action.title,
                fallbackTurns: [],
              }),
            )
          } catch {
            /* ignore */
          }
          goTo("chat")
          break
        case "prd-tab":
          openPrdTab({
            title: action.title,
            source: {
              kind: "load",
              prdId: action.prdId,
              meta:
                action.briefId != null && action.insightIndex != null
                  ? { briefId: action.briefId, insightIndex: action.insightIndex }
                  : null,
            },
          })
          break
        case "switch-workspace":
          setActiveWorkspace(action.workspaceId)
          break
      }
      onClose()
    },
    [workspaceId, router, goTo, goToNewChat, openPrdTab, setActiveWorkspace, onClose],
  )

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Escape") {
        e.preventDefault()
        onClose()
        return
      }
      if (flat.length === 0) return
      if (e.key === "ArrowDown") {
        e.preventDefault()
        setActiveIndex((i) => (i + 1) % flat.length)
      } else if (e.key === "ArrowUp") {
        e.preventDefault()
        setActiveIndex((i) => (i - 1 + flat.length) % flat.length)
      } else if (e.key === "Home" && !query) {
        e.preventDefault()
        setActiveIndex(0)
      } else if (e.key === "End" && !query) {
        e.preventDefault()
        setActiveIndex(flat.length - 1)
      } else if (e.key === "Enter" && !e.nativeEvent.isComposing) {
        e.preventDefault()
        const target = flat[clampedIndex]
        if (target) perform(target)
      }
    },
    [flat, clampedIndex, perform, onClose, query],
  )

  if (!open) return null

  const loading = dynamicItems === null && workspaceId != null
  const noResults = deferredQuery.trim() !== "" && flat.length === 0

  let rowIndex = -1

  return (
    <div
      className="modal-overlay open cmdp-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="Search"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="cmdp">
        <div className="cmdp-head">
          <IconSearch size={18} className="cmdp-head-icon" />
          <input
            ref={inputRef}
            className="cmdp-input"
            type="text"
            role="combobox"
            aria-expanded="true"
            aria-controls="cmdp-list"
            aria-activedescendant={
              flat[clampedIndex] ? `cmdp-opt-${flat[clampedIndex].item.id}` : undefined
            }
            placeholder="Search pages, settings, skills, chats…"
            spellCheck={false}
            autoComplete="off"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKeyDown}
          />
          <kbd className="cmdp-kbd">esc</kbd>
        </div>

        <div className="cmdp-list" id="cmdp-list" role="listbox" aria-label="Search results">
          {groups.map((g) => (
            <div key={g.group} className="cmdp-group">
              <div className="cmdp-group-label">{g.label}</div>
              {g.items.map((scored) => {
                rowIndex += 1
                const idx = rowIndex
                const active = idx === clampedIndex
                const { item } = scored
                return (
                  <button
                    key={item.id}
                    id={`cmdp-opt-${item.id}`}
                    ref={active ? activeRowRef : undefined}
                    type="button"
                    role="option"
                    aria-selected={active}
                    className={`cmdp-item${active ? " active" : ""}`}
                    onClick={() => perform(scored)}
                    onMouseMove={() => {
                      if (!active) setActiveIndex(idx)
                    }}
                  >
                    <span className="cmdp-item-icon">
                      <ItemIcon iconId={item.iconId} />
                    </span>
                    <span className="cmdp-item-main">
                      <span className="cmdp-item-title">
                        <HighlightedTitle title={item.title} ranges={scored.titleRanges} />
                      </span>
                      {item.subtitle && (
                        <span className="cmdp-item-sub">{item.subtitle}</span>
                      )}
                    </span>
                    <span className="cmdp-crumb">
                      {item.breadcrumb.length > 0 && (
                        <span className="cmdp-crumb-trail">
                          {item.breadcrumb.join(" › ")}
                        </span>
                      )}
                      {item.url && <span className="cmdp-crumb-url">{item.url}</span>}
                    </span>
                  </button>
                )
              })}
            </div>
          ))}
          {noResults && (
            <div className="cmdp-empty">
              No results for &ldquo;{deferredQuery.trim()}&rdquo;
            </div>
          )}
        </div>

        <div className="cmdp-foot">
          <span className="cmdp-foot-hint">
            <kbd className="cmdp-kbd">↑</kbd>
            <kbd className="cmdp-kbd">↓</kbd> navigate
          </span>
          <span className="cmdp-foot-hint">
            <kbd className="cmdp-kbd">↵</kbd> open
          </span>
          <span className="cmdp-foot-hint">
            <kbd className="cmdp-kbd">{isMac ? "⌘K" : "Ctrl K"}</kbd> toggle
          </span>
          {loading && <span className="cmdp-loading">Loading workspace items…</span>}
        </div>
      </div>
    </div>
  )
}
