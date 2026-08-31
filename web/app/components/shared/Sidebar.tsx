"use client"

import { useEffect, useLayoutEffect, useRef, useState } from "react"
import { useNavigation } from "../../context/NavigationContext"
import { useContent } from "../../context/ContentContext"
import { useAuth } from "../../lib/auth"
import { useRouter } from "next/navigation"
import { profileDisplayName, useWorkspace } from "../../context/WorkspaceContext"
import { trialDaysLeft, trialLabel } from "../../lib/billingAccess"
import type { ScreenId } from "../../types"
import { IconSources } from "./sidebar-icons"
import {
  SIDEBAR_DEFAULT_WIDTH,
  SIDEBAR_MAX_WIDTH,
  SIDEBAR_MIN_WIDTH,
  applySidebarWidth,
  clampSidebarWidth,
  loadSidebarWidth,
  saveSidebarWidth,
} from "../../lib/sidebarWidth"
import {
  chatsCacheKey,
  chatStamp,
  recentChats,
  resumeConversation,
  useChatsList,
} from "../../lib/recentChats"
import { IconLayoutKanban, IconMessageCircle, IconPrompt, IconBulb, IconSettings, IconHistory, IconMessagePlus, IconBookmark, IconFiles, IconWand, IconSearch, IconSparkles, IconBrowser, IconFolder, IconRefresh, IconCheck } from "@tabler/icons-react"
import { usePipelineStatus } from "../../lib/usePipelineStatus"
import { CreateWorkspaceModal } from "./CreateWorkspaceModal"

interface SidebarProps {
  activeCompany?: string
  onSwitchCompany?: (slug: string) => void
}

export function Sidebar({ activeCompany }: SidebarProps = {}) {
  const { currentScreen, goTo, goToNewChat, goToWorkbench, sidebarCollapsed, toggleSidebar, openPalette, openFeedback } = useNavigation()
  const { content } = useContent()
  const router = useRouter()
  const auth = useAuth()
  const {
    profile,
    workspace,
    workspaces = [],
    activeWorkspace,
    orgRole,
    setActiveWorkspace,
  } = useWorkspace()
  const trialDays = trialDaysLeft(workspace)
  // Sync-your-data (2026-08-13): one click runs the FULL pipeline for the
  // active dataset — the same run the scheduler triggers, not a bespoke
  // sync-all. The backend collapses repeat clicks onto the in-flight run
  // (routes/pipeline.py _INFLIGHT), so the button never stacks runs; the
  // spinning state is what a second click sees instead.
  const { runStatus, isTriggering, showCompleted, triggerRun } = usePipelineStatus(activeCompany ?? "")
  const syncRunning = isTriggering || runStatus?.status === "running"
  const syncFailed = !syncRunning && runStatus?.status === "failed"
  const lastSyncedAgo =
    runStatus?.status === "completed" ? relTimeAgo(runStatus.completed_at) : null
  const syncTitle = syncRunning
    ? "Syncing your data…"
    : syncFailed
      ? "Last sync failed — click to retry"
      : lastSyncedAgo
        ? `Sync your data — last synced ${lastSyncedAgo}`
        : "Sync your data"
  // Workspace switcher (multi-workspace 2026-07): the brand name doubles as
  // the trigger; the menu lists the caller's workspaces + a create affordance.
  const [wsMenuOpen, setWsMenuOpen] = useState(false)
  const [createWsOpen, setCreateWsOpen] = useState(false)
  const wsMenuRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (!wsMenuRef.current) return
      if (!wsMenuRef.current.contains(e.target as Node)) setWsMenuOpen(false)
    }
    if (wsMenuOpen) document.addEventListener("mousedown", onClick)
    return () => document.removeEventListener("mousedown", onClick)
  }, [wsMenuOpen])

  const RailItem = ({
    screen,
    icon,
    label,
  }: {
    screen: ScreenId
    icon: React.ReactNode
    label: string
  }) => (
    <button
      type="button"
      className={`sb-rail-item${currentScreen === screen ? " active" : ""}`}
      title={label}
      onClick={() => goTo(screen)}
      aria-label={label}
      /* Anchor for the first-run product tour (components/tour). Derived from
         the screen id rather than listed separately, so a new rail item is
         spotlightable without touching the tour. */
      data-tour={`nav-${screen === "ideation" ? "backlog" : screen}`}
    >
      {icon}
      <span className="sb-rail-label">{label}</span>
      <span className="nav-tooltip">{label}</span>
    </button>
  )

  const displayName =
    content.userName ??
    (auth.kind === "authed" ? profileDisplayName(profile, auth.user.email) : null) ??
    "Guest"
  const initials =
    content.userInitials ??
    displayName
      .split(/\s+/)
      .map((p) => p[0])
      .join("")
      .slice(0, 2)
      .toUpperCase()

  // The header shows the ACTIVE WORKSPACE (multi-workspace 2026-07); company
  // display name is the fallback while the workspaces list loads.
  const brandName =
    activeWorkspace?.name ??
    workspace?.display_name ??
    workspace?.product?.name ??
    content.homeHeadline ??
    "Sprntly"
  const companyInitial = brandName.charAt(0).toUpperCase()
  // Workspace creation is ORG owner/admin only (backend-enforced) — a
  // workspace-level admin who is a plain org member doesn't get the button.
  const canCreateWs = orgRole === "owner" || orgRole === "admin"
  const wsInteractive = workspaces.length > 1 || canCreateWs

  return (
    <aside className={`sidebar ${sidebarCollapsed ? "sidebar--collapsed" : "sidebar--expanded"}`}>
      {/* Drag the right edge. Only when expanded: collapsed, the rail is a
          fixed strip of icons and there is no width to choose. */}
      {!sidebarCollapsed && <SidebarResizer />}
      {/* Logo + workspace switcher + expand/collapse toggle */}
      <div className="sb-rail-header">
        <div
          className="sb-rail-logo"
          title={content.homeHeadline ?? "Sprntly"}
          onClick={() => goTo("chat")}
          style={{ cursor: "pointer" }}
        >
          <span className="sb-rail-logo-text">
            {companyInitial}
            <span className="sb-rail-logo-dot">.</span>
          </span>
        </div>
        <div className="sb-ws-wrap" ref={wsMenuRef}>
          <button
            type="button"
            className={`sb-rail-brand-name sb-ws-trigger${wsInteractive ? "" : " sb-ws-trigger--static"}`}
            onClick={() => wsInteractive && setWsMenuOpen((v) => !v)}
            aria-haspopup="listbox"
            aria-expanded={wsMenuOpen}
            title={brandName}
            data-testid="workspace-switcher"
            data-tour="workspace-switcher"
          >
            <span className="sb-ws-name">{brandName}</span>
            {wsInteractive && (
              <svg width="10" height="10" viewBox="0 0 24 24" aria-hidden>
                <path d="M6 9 L12 15 L18 9" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" />
              </svg>
            )}
          </button>
          {wsMenuOpen && (
            <div className="sb-ws-menu" role="listbox">
              {workspaces.map((w) => (
                <button
                  key={w.id}
                  type="button"
                  className={`sb-ws-row${w.id === activeWorkspace?.id ? " active" : ""}`}
                  onClick={() => {
                    setActiveWorkspace(w.id)
                    setWsMenuOpen(false)
                  }}
                  role="option"
                  aria-selected={w.id === activeWorkspace?.id}
                >
                  <span className="sb-ws-row-name">{w.name}</span>
                  {w.id === activeWorkspace?.id && (
                    <span className="sb-ws-row-meta">active</span>
                  )}
                </button>
              ))}
              {canCreateWs && (
                <>
                  <div className="sb-ws-sep" />
                  <button
                    type="button"
                    className="sb-ws-row sb-ws-row--create"
                    onClick={() => {
                      setWsMenuOpen(false)
                      setCreateWsOpen(true)
                    }}
                  >
                    + New workspace
                  </button>
                </>
              )}
            </div>
          )}
        </div>
        <button
          type="button"
          className="sb-rail-expand"
          onClick={toggleSidebar}
          title={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
          aria-label={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
          aria-expanded={!sidebarCollapsed}
        >
          <ChevronIcon collapsed={sidebarCollapsed} />
        </button>
      </div>

      {/* New chat */}
      <button
        type="button"
        className="sb-rail-new"
        title="New chat"
        aria-label="New chat"
        onClick={goToNewChat}
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <line x1="12" y1="5" x2="12" y2="19" />
          <line x1="5" y1="12" x2="19" y2="12" />
        </svg>
        <span className="sb-rail-label">New chat</span>
      </button>

      {/* Main nav icons */}
      <div className="sb-rail-nav">
        {/* Workbench is HIDDEN from the rail for now (product call, 2026-08-07),
            the same way Search is: the surface it opens is untouched. New chat
            still lands on `/`, Top Insights still activates the pinned tab, and
            goToWorkbench + the `/?tab=last` one-shot ChatScreen consumes are all
            still wired — only this trigger is withheld. Uncomment to put it back.

            Workbench — the door to your OPEN WORK on the home surface: it lands
            on the last chat tab you were on, never on the pinned Top Insights
            tab (which has its own item right below). Not a RailItem because it
            isn't a plain screen nav: goToWorkbench pushes the one-shot
            `/?tab=last` that ChatScreen consumes to restore that tab. Highlighted
            on the chat surface (`/`), the route it always lands on. */}
        {/* <button
          type="button"
          className={`sb-rail-item${currentScreen === "chat" ? " active" : ""}`}
          title="Workbench"
          aria-label="Workbench"
          onClick={goToWorkbench}
          data-testid="sidebar-workbench"
        >
          <IconBrowser size={18} />
          <span className="sb-rail-label">Workbench</span>
          <span className="nav-tooltip">Workbench</span>
        </button> */}
        <RailItem screen="brief" icon={<IconSparkles size={18} />} label="Top Insights" />
        {/* NO "Chat history" ITEM. The threads themselves are in the nav now
            (see `RecentChats` below), and its last row is the door to the full
            history screen — a second door one row above it, leading to the same
            place, is just a longer nav. The screen, its route and the command
            palette entry are all untouched. */}
        <RailItem screen="artifacts" icon={<IconFiles size={18} />} label="Artifacts" />
        <RailItem screen="projects" icon={<IconFolder size={18} />} label="Projects" />
        <RailItem screen="ideation" icon={<IconBulb size={18} />} label="Backlog" />
        {/* NO Templates or Skills ITEMS. Both moved into Settings (see
            `SETTINGS_NAV`'s "How Sprntly writes" group): they are things a
            workspace sets up once and returns to, which is what Settings is
            for, and the nav they left is now carrying the threads instead.
            Their screens, routes, ScreenIds and command-palette entries are
            all unchanged — only the door moved. */}
        {/* <RailItem screen="sources" icon={<IconSources />} label="Sources" /> */}
        {/* <RailItem screen="prototype" icon={<IconPrompt size={18} />} label="Prototype" /> */}
        {/* <RailItem screen="tickets" icon={<IconLayoutKanban size={18} />} label="Project Management" /> */}
      </div>

      {/* Recent threads, then the way to all of them. Only when the sidebar is
          expanded: collapsed it is a 42px icon rail, and a column of truncated
          chat titles has nowhere to go in it. */}
      {!sidebarCollapsed && <RecentChats activeCompany={activeCompany ?? null} />}

      <div className="sb-rail-spacer" />

      {/* NO BOTTOM NAV BLOCK. Guide, Settings and Feedback used to be three
          full rows here, above the divider. Settings and Feedback are icons in
          the identity row below now, beside Sync — three actions on one line
          instead of three lines — and Guide moved into Settings itself, where
          the rest of the read-about-it surfaces already live. */}
      {/* ON TRIAL, EVERYWHERE. A countdown that lives only on the billing
          screen is a countdown nobody sees: you visit that screen once, at
          signup, and then not again until something goes wrong. This sits
          above the identity row so the fact travels with you, and it is a
          button rather than a label because the one question it raises —
          "what happens when it ends?" — is answered one click away.

          It renders ONLY while trialling, so it costs a fully-paid workspace
          no rail space at all. */}
      {trialDays != null && (
        <button
          type="button"
          className="sb-trial"
          data-testid="sidebar-trial"
          data-tour="sidebar-trial"
          title={`Free trial — ${trialLabel(trialDays)}`}
          aria-label={`Free trial, ${trialLabel(trialDays)}. Open billing.`}
          onClick={() => router.push("/settings?section=billing")}
        >
          {/* Collapsed, the rail hides the words and keeps the number — the
              one part that still means something at 56px wide. */}
          <span className="sb-trial-num">{trialDays}</span>
          <span className="sb-trial-copy">
            <span className="sb-trial-label">Free trial</span>
            <span className="sb-trial-days">{trialLabel(trialDays)}</span>
          </span>
        </button>
      )}

      <div className="divider-nav" />

      {/* User identity row — the avatar/name are display only (signing out
          moved to Settings → Account; no sign-out affordance on icon or
          avatar click). The sync button is the one interactive element. */}
      {/* ONE ROW: avatar, name, then sync / feedback / search / settings.

          WHAT GIVES WAY IS THE NAME. The initials chip is a fixed 32px circle
          (`flex: none` — without it the row squashed it into an oval when
          space ran out), and the four actions keep their size too. The name
          takes whatever is left and ellipsizes into it, so dragging the rail
          wider reveals more of it and narrower reveals less. Its full value is
          on the hover title of both the chip and the name. */}
      <div className="sb-rail-user">
        <span className="sb-rail-avatar" title={displayName}>
          {initials}
        </span>
        <span className="sb-rail-username" title={displayName}>{displayName}</span>
        <div className="sb-rail-actions">
        {/* Sync, feedback, search, settings — reachable from EVERY screen in
            both rail modes, which is why they live here rather than in the
            scrolling nav above (product call 2026-08-13: identity chrome earns
            no rail slot, an action does). Collapsed, the avatar and name are
            CSS-hidden and these four are the whole footer. The row's identity
            half stays display-only: none of these is the avatar or the name. */}
        <button
          type="button"
          className={`sb-sync-btn${syncRunning ? " sb-sync-btn--running" : ""}${showCompleted ? " sb-sync-btn--done" : ""}${syncFailed ? " sb-sync-btn--failed" : ""}`}
          title={syncTitle}
          aria-label="Sync your data"
          aria-busy={syncRunning || undefined}
          disabled={!activeCompany}
          data-testid="sidebar-sync"
          data-tour="rail-sync"
          onClick={() => {
            if (!syncRunning) void triggerRun()
          }}
        >
          {showCompleted ? <IconCheck size={15} /> : <IconRefresh size={15} />}
          <span className="nav-tooltip">{syncTitle}</span>
        </button>
        {/* Feedback, then Search, then Settings — left to right: sync,
            feedback, search, settings. Settings sits last because it is the one
            you reach for on purpose; sync leads because it is the one that
            reports a state you might need to act on. */}
        <button
          type="button"
          className="sb-rail-action"
          title="Feedback"
          aria-label="Feedback"
          data-testid="sidebar-feedback"
          data-tour="rail-feedback"
          onClick={openFeedback}
        >
          <IconMessagePlus size={15} />
          <span className="nav-tooltip">Feedback</span>
        </button>
        {/* Search (⌘K) — the palette itself is rendered by AppShell, which also
            owns the hotkey, so this is purely the visible door to it: the one
            thing a user who doesn't know the shortcut had no way to find. It
            sits here rather than up in the nav because it is an action, not a
            screen, and the actions row is the part of the rail that survives
            both collapsed and expanded. */}
        <button
          type="button"
          className="sb-rail-action"
          title="Search (⌘K)"
          aria-label="Search (Ctrl+K)"
          data-testid="palette-trigger"
          data-tour="rail-search"
          onClick={openPalette}
        >
          <IconSearch size={15} />
          <span className="nav-tooltip">Search</span>
        </button>
        <button
          type="button"
          className={`sb-rail-action${currentScreen === "settings" ? " active" : ""}`}
          title="Settings"
          aria-label="Settings"
          data-testid="sidebar-settings"
          data-tour="rail-settings"
          onClick={() => goTo("settings")}
        >
          <IconSettings size={15} />
          <span className="nav-tooltip">Settings</span>
        </button>
        </div>
      </div>

      {/* NO <FeedbackModal/> HERE. It moved to AppShell when the palette gained
          a "Send feedback" action: two triggers, one modal, and the palette
          cannot reach this component's state. */}
      <CreateWorkspaceModal open={createWsOpen} onClose={() => setCreateWsOpen(false)} />
    </aside>
  )
}

// Coarse relative time for the sync tooltip ("last synced 2h ago"). The
// staleness hint is the passive replacement for a sync-reminder nudge (product
// call 2026-08-13: no notifications), so it only needs day-level precision.
function relTimeAgo(iso: string | null): string | null {
  if (!iso) return null
  const then = Date.parse(iso)
  if (Number.isNaN(then)) return null
  const mins = Math.floor((Date.now() - then) / 60_000)
  if (mins < 1) return "just now"
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.floor(hours / 24)}d ago`
}

/**
 * The sidebar's draggable right edge.
 *
 * The drag writes straight to the CSS custom property rather than to React
 * state: a pointermove fires at screen refresh rate, and re-rendering the whole
 * sidebar (nav, twenty chat rows, the workspace switcher) on each one is how a
 * resize handle ends up feeling like it is dragging through mud. React owns
 * WHETHER the handle exists; the browser owns where the edge is while it moves.
 *
 * `setPointerCapture` is what makes the drag survive the pointer leaving the
 * 6px handle — without it, moving faster than the layout can follow drops the
 * drag, which is exactly what happens on the first fast pull.
 */
function SidebarResizer() {
  const ref = useRef<HTMLDivElement | null>(null)
  // The live width during a drag, so the keyboard path and the pointer path
  // share one notion of "where it is now" without reading layout back.
  const widthRef = useRef<number>(SIDEBAR_DEFAULT_WIDTH)

  // Restore before paint, or the sidebar renders at its default width and
  // visibly snaps to the remembered one.
  useLayoutEffect(() => {
    const stored = loadSidebarWidth()
    widthRef.current = stored
    applySidebarWidth(stored)
  }, [])

  const setWidth = (px: number) => {
    const next = clampSidebarWidth(px)
    widthRef.current = next
    applySidebarWidth(next)
    return next
  }

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    // Left button only: a right-click here belongs to the context menu.
    if (e.button !== 0) return
    e.preventDefault()
    const el = ref.current
    el?.setPointerCapture(e.pointerId)
    // Kills the sidebar's own width transition for the duration — a 200ms ease
    // on every pointermove is a sidebar that lags the cursor.
    document.body.classList.add("is-sidebar-resizing")

    const onMove = (ev: PointerEvent) => {
      // The sidebar is pinned to the left edge, so its width IS the pointer's
      // x. No offset bookkeeping, and no drift when the drag starts a pixel or
      // two off-centre in the handle.
      setWidth(ev.clientX)
    }
    const onUp = (ev: PointerEvent) => {
      el?.releasePointerCapture(ev.pointerId)
      document.body.classList.remove("is-sidebar-resizing")
      el?.removeEventListener("pointermove", onMove)
      el?.removeEventListener("pointerup", onUp)
      el?.removeEventListener("pointercancel", onUp)
      saveSidebarWidth(widthRef.current)
    }
    el?.addEventListener("pointermove", onMove)
    el?.addEventListener("pointerup", onUp)
    // A cancelled pointer (a system gesture, a lost window) must clean up too,
    // or the body keeps the resizing class and the transition never comes back.
    el?.addEventListener("pointercancel", onUp)
  }

  // Arrow keys move it too. A control that can only be operated by dragging a
  // 6px target is a control some people simply do not have.
  const onKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const step = e.shiftKey ? 32 : 8
    if (e.key === "ArrowLeft") {
      e.preventDefault()
      saveSidebarWidth(setWidth(widthRef.current - step))
    } else if (e.key === "ArrowRight") {
      e.preventDefault()
      saveSidebarWidth(setWidth(widthRef.current + step))
    }
  }

  return (
    <div
      ref={ref}
      className="sb-resizer"
      data-testid="sidebar-resizer"
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize sidebar"
      aria-valuemin={SIDEBAR_MIN_WIDTH}
      aria-valuemax={SIDEBAR_MAX_WIDTH}
      tabIndex={0}
      onPointerDown={onPointerDown}
      onKeyDown={onKeyDown}
    >
      {/* A grip, not a bare hit-area. An invisible 6px target is a feature
          nobody finds: the bar sits at mid-height where a hand reaches for it,
          faint until the pointer is near, and the tooltip names the gesture. */}
      <span className="sb-resizer-grip" aria-hidden />
      <span className="nav-tooltip sb-resizer-tip">Drag to resize</span>
    </div>
  )
}

/**
 * The threads themselves, in the nav.
 *
 * WHY THE NAV AND NOT A SCREEN. Chat history used to be one icon among nine,
 * and every return to a thread was two navigations: open the screen, find the
 * row. The work people come back to IS their chats, so the nav shows them —
 * twenty of them, most recent first — and keeps a single row at the bottom for
 * the rest.
 *
 * Twenty is the cap because the list has to end somewhere above the fold on a
 * laptop and below the point where the nav stops being useful. Past that,
 * "View all chats" is the same screen it always was.
 */
function RecentChats({ activeCompany }: { activeCompany: string | null }) {
  const { goTo } = useNavigation()
  const auth = useAuth()
  // Signed out, there is nothing to list and nothing to ask for — the route is
  // authed. Null rather than an "anon" key, so no request is made at all.
  const key =
    auth.kind === "authed" ? chatsCacheKey(auth.user.id, activeCompany) : null
  const { chats, loaded } = useChatsList(key)
  const rows = recentChats(chats)

  // Nothing yet, and nothing to say about it: a nav section announcing "no
  // chats" to someone who has not had one is noise in the one place that has
  // to stay scannable. The section simply is not there until there is a thread
  // in it. `loaded` keeps the header from flashing in before the list lands.
  if (!loaded || rows.length === 0) return null

  return (
    <div className="sb-chats" data-testid="sidebar-recent-chats">
      <div className="sb-chats-head">Chats</div>
      <div className="sb-chats-list">
        {rows.map((chat) => (
          <button
            key={chat.id}
            type="button"
            className="sb-chat-item"
            // The full title, for the row that truncates to one line.
            title={chat.title}
            data-testid={`sidebar-chat-${chat.id}`}
            onClick={() => resumeConversation(chat, () => goTo("chat"))}
          >
            {/* A marker per row. Twenty left-aligned strings of different
                lengths read as a wall; a fixed dot gives every title the same
                starting line and the list a rhythm. */}
            <span className="sb-chat-dot" aria-hidden />
            <span className="sb-chat-title">{chat.title}</span>
            {/* When it was asked. A title is the first message verbatim, so
                the same question asked twice gives two identical rows — this
                is what tells them apart. */}
            <span className="sb-chat-when">{chatStamp(chat.created_at)}</span>
          </button>
        ))}
      </div>
      <button
        type="button"
        className="sb-chats-all"
        data-testid="sidebar-view-all-chats"
        onClick={() => goTo("chats")}
      >
        View all chats
      </button>
    </div>
  )
}

function ChevronIcon({ collapsed }: { collapsed: boolean }) {
  // Points right (»/›) when collapsed to invite expansion, left when expanded.
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      style={{ transform: collapsed ? "none" : "rotate(180deg)" }}
      aria-hidden
    >
      <polyline points="9 18 15 12 9 6" />
    </svg>
  )
}

