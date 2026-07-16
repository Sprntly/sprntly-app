"use client"

// Skills · the PM-skill gallery.
//
// Top-level surface listing every skill the chat can route to (GET
// /v1/ask/skills — the routable manifest computed from backend/skills/).
// Clicking a card hands off to the chat via setPendingOndemandDraft with the
// skill's `/trigger ` pre-filled, so the user lands in a focused thread ready
// to type their specifics; the backend's slash fast-path (qa_agent) then pins
// that skill with confidence 1.0 on send.
//
// What's REAL here: the listing (grouped by the backend catalog's category)
// and the click-to-chat hand-off. "Create or upload skill" is a roadmap
// affordance — user-authored skills have no backend yet, so it shows a
// coming-soon toast rather than faking a flow. Every card carries the Sprntly
// byline: all skills are first-party today; per-creator attribution arrives
// with user-authored skills.
//
// The view layer (SkillsView) is pure and prop-driven so it can be
// markup-tested without the API; SkillsScreen owns state, API, and navigation.

import { useEffect, useMemo, useState } from "react"
import {
  IconChartLine,
  IconCompass,
  IconFileText,
  IconListCheck,
  IconPlus,
  IconRocket,
  IconSearch,
  IconSparkles,
  IconSpeakerphone,
  IconWand,
} from "@tabler/icons-react"
import { AppLayout } from "./AppLayout"
import { useNavigation } from "../../../context/NavigationContext"
import { askApi, type SkillInfo } from "../../../lib/api"

// Backend category → display order, tagline, and icon. Categories are owned by
// the backend catalog (app/skills/catalog.py); this map only decorates them.
// A category the backend adds later still renders (appended, wand icon) — it
// just won't have a tagline until listed here.
const CATEGORY_DISPLAY: {
  id: string
  tagline: string
  icon: React.ComponentType<{ size?: number | string }>
}[] = [
  { id: "Discovery & Research", tagline: "figuring out what's worth building", icon: IconSearch },
  { id: "Strategy & Vision", tagline: "deciding where to play and how to win", icon: IconCompass },
  { id: "Documentation & Specification", tagline: "writing it down so it ships right", icon: IconFileText },
  { id: "Prioritization & Decision", tagline: "choosing the next bet with a clear head", icon: IconListCheck },
  { id: "Metrics, Experimentation & Growth", tagline: "measuring what matters and growing it", icon: IconChartLine },
  { id: "Delivery & Operations", tagline: "shipping it and keeping it running", icon: IconRocket },
  { id: "Stakeholder & Communication", tagline: "keeping everyone aligned and informed", icon: IconSpeakerphone },
]

/** First sentence of a skill's frontmatter description, minus the router
 *  guidance tail ("Use when the user says …"). The catalog descriptions are
 *  written for the LLM router, so cards show just the lead. Pure → testable. */
export function skillBlurb(description: string, label: string): string {
  const d = (description || "").trim()
  if (!d) return `Run the ${label} workflow`
  const useWhen = d.search(/\bUse when\b/)
  const head = useWhen > 0 ? d.slice(0, useWhen) : d
  const sentence = head.match(/^[^.!?]*[.!?]/)
  return (sentence ? sentence[0] : head).trim().replace(/[.!?]+$/, "")
}

export type SkillGroup = {
  category: string
  tagline: string
  icon: React.ComponentType<{ size?: number | string }>
  skills: SkillInfo[]
}

/** Group skills by backend category in CATEGORY_DISPLAY order; categories the
 *  map doesn't know yet are appended alphabetically rather than dropped. */
export function groupSkills(skills: SkillInfo[]): SkillGroup[] {
  const byCategory = new Map<string, SkillInfo[]>()
  for (const s of skills) {
    const key = s.category || "Other"
    const list = byCategory.get(key)
    if (list) list.push(s)
    else byCategory.set(key, [s])
  }
  const groups: SkillGroup[] = []
  for (const c of CATEGORY_DISPLAY) {
    const list = byCategory.get(c.id)
    if (!list) continue
    byCategory.delete(c.id)
    groups.push({ category: c.id, tagline: c.tagline, icon: c.icon, skills: list })
  }
  for (const [category, list] of [...byCategory.entries()].sort((a, b) => a[0].localeCompare(b[0]))) {
    groups.push({ category, tagline: "", icon: IconWand, skills: list })
  }
  return groups
}

/** Pure presentational view — all state arrives as props, so it renders
 *  identically in a static-markup test (no API, no effects). */
export function SkillsView({
  groups,
  loading,
  error,
  query,
  onQueryChange,
  onInvoke,
  onCreate,
}: {
  groups: SkillGroup[]
  loading: boolean
  error: string | null
  query: string
  onQueryChange: (value: string) => void
  onInvoke: (skill: SkillInfo) => void
  onCreate: () => void
}) {
  return (
    <div className="skl-wrap">
      {/* Header */}
      <div className="skl-top">
        <div className="skl-title">
          <IconWand size={16} className="skl-title-icon" />
          Skills
          <span className="skl-sub">
            PM skills you can invoke — pick one to start a focused thread
          </span>
        </div>
        <button type="button" className="btn btn-primary skl-create" onClick={onCreate}>
          <IconPlus size={14} />
          Create or upload skill
        </button>
      </div>

      <div className="skl-body">
        {/* Intro banner */}
        <div className="skl-intro">
          <IconWand size={16} className="skl-intro-icon" />
          <span>
            Each skill is a focused PM workflow Sprntly runs with you. Pick one —
            it opens a new thread with that skill already invoked, ready for your
            specifics.
          </span>
        </div>

        {/* Search — matches a skill's name, trigger, description, or category. */}
        <div className="skl-search">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>
          <input
            type="search"
            value={query}
            onChange={(e) => onQueryChange(e.target.value)}
            placeholder="Search skills — e.g. journey map, RACI, pricing…"
            aria-label="Search skills"
          />
        </div>

        {error && <div className="skl-msg skl-msg-error" role="alert">{error}</div>}

        {loading ? (
          <p className="skl-placeholder">Loading skills…</p>
        ) : groups.length === 0 ? (
          <p className="skl-placeholder">
            {query.trim()
              ? `No skills match “${query.trim()}”.`
              : "No skills available."}
          </p>
        ) : (
          groups.map((g, i) => (
            <section key={g.category} className="skl-group" aria-label={g.category}>
              <div className="skl-group-head">
                <h2 className="skl-group-title">
                  {i + 1} · {g.category}
                </h2>
                {g.tagline && <span className="skl-group-tag">{g.tagline}</span>}
              </div>
              <div className="skl-grid">
                {g.skills.map((s) => (
                  <button
                    key={s.id}
                    type="button"
                    className="skl-card"
                    onClick={() => onInvoke(s)}
                    title={`${s.trigger} — start a thread with this skill`}
                  >
                    <span className="skl-card-icon">
                      <g.icon size={16} />
                    </span>
                    <span className="skl-card-t">{s.label}</span>
                    <span className="skl-card-d">{skillBlurb(s.description, s.label)}</span>
                    <span className="skl-card-foot">
                      <span className="skl-by">
                        <IconSparkles size={11} />
                        Sprntly
                      </span>
                    </span>
                  </button>
                ))}
              </div>
            </section>
          ))
        )}
      </div>
    </div>
  )
}

export function SkillsScreen() {
  const { goTo, setPendingOndemandDraft, showToast } = useNavigation()
  const [skills, setSkills] = useState<SkillInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState("")

  useEffect(() => {
    let cancelled = false
    askApi
      .skills()
      .then((r) => {
        if (cancelled) return
        setSkills(r.skills)
        setError(null)
      })
      .catch((e) => {
        if (cancelled) return
        setError(e instanceof Error ? e.message : "Could not load skills")
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  // Search filters BEFORE grouping, so empty categories drop out and the
  // section numbering re-flows over what's visible. Matching runs over the
  // full router description (not just the card blurb) — searching "RACI"
  // should surface stakeholder-map even though its blurb doesn't say it.
  const groups = useMemo(() => {
    const q = query.trim().toLowerCase()
    const visible = !q
      ? skills
      : skills.filter(
          (s) =>
            s.label.toLowerCase().includes(q) ||
            s.trigger.toLowerCase().includes(q) ||
            s.description.toLowerCase().includes(q) ||
            s.category.toLowerCase().includes(q),
        )
    return groupSkills(visible)
  }, [skills, query])

  // Hand off to the chat: pre-fill the composer with the skill's trigger and
  // navigate. ChatScreen consumes pendingOndemandDraft once — no active tab →
  // pre-filled composer; active tab → a fresh tab seeded with the trigger.
  function onInvoke(skill: SkillInfo) {
    setPendingOndemandDraft(`${skill.trigger} `)
    goTo("chat")
  }

  function onCreate() {
    showToast(
      "Custom skills are coming soon",
      "Soon you'll be able to write or upload your own skills for your workspace.",
    )
  }

  return (
    // hideChromeStrip: the surface has its own .skl-top title bar, so the
    // main-column chrome strip would just duplicate "Skills" above it.
    <AppLayout mainClassName="main--skills" hideChromeStrip>
      <SkillsView
        groups={groups}
        loading={loading}
        error={error}
        query={query}
        onQueryChange={setQuery}
        onInvoke={onInvoke}
        onCreate={onCreate}
      />
    </AppLayout>
  )
}
