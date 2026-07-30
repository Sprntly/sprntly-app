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
// Two listings render here: the built-in catalog (grouped by the backend
// catalog's category) and the company's CUSTOM skills (PRD 1854 — uploaded
// .md/.zip files, company-scoped so every workspace shares one library).
// "Create or upload skill" opens the upload modal (UploadSkillModal → POST
// /v1/skills); a created skill appears in the "Custom skills" section
// immediately, byline = uploader. Built-in cards keep the Sprntly byline.
// The two fetches fail independently: a custom-skills error never blanks the
// built-in catalog — it shows an inline notice instead.
//
// The view layer (SkillsView) is pure and prop-driven so it can be
// markup-tested without the API; SkillsScreen owns state, API, and navigation.

import { Suspense, useEffect, useMemo, useState } from "react"
import { useSearchParams } from "next/navigation"
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
  IconTrash,
  IconUser,
  IconWand,
} from "@tabler/icons-react"
import { AppLayout } from "./AppLayout"
import { UploadSkillModal } from "../../shared/UploadSkillModal"
import { useNavigation } from "../../../context/NavigationContext"
import {
  askApi,
  skillsApi,
  type CustomSkillInfo,
  type SkillInfo,
} from "../../../lib/api"

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
  customSkills,
  customError,
  loading,
  error,
  query,
  onQueryChange,
  onInvoke,
  onCreate,
  deletePendingId,
  deletingId,
  onDeleteRequest,
  onDeleteConfirm,
}: {
  groups: SkillGroup[]
  /** The company's uploaded skills (already search-filtered by the caller). */
  customSkills: CustomSkillInfo[]
  /** Non-blocking: custom-skills fetch failed but built-ins still render. */
  customError: string | null
  loading: boolean
  error: string | null
  query: string
  onQueryChange: (value: string) => void
  onInvoke: (skill: { trigger: string }) => void
  onCreate: () => void
  /** Custom-skill id with the inline delete confirm armed (one at a time). */
  deletePendingId: string | null
  /** Custom-skill id whose delete is in flight — shows the Deleting… state. */
  deletingId: string | null
  /** Arm the confirm for an id; null disarms (Cancel / picking another card). */
  onDeleteRequest: (id: string | null) => void
  /** Actually delete — reachable only through the armed confirm. */
  onDeleteConfirm: (id: string) => void
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
        {customError && (
          <div className="skl-msg skl-msg-error" role="alert">
            Custom skills couldn’t load: {customError}
          </div>
        )}

        {/* Custom skills — the company's own uploads, above the catalog so a
            just-uploaded skill is immediately visible. Byline = uploader. */}
        {!loading && customSkills.length > 0 && (
          <section className="skl-group" aria-label="Custom skills">
            <div className="skl-group-head">
              <h2 className="skl-group-title">Custom skills</h2>
              <span className="skl-group-tag">uploaded by your team</span>
            </div>
            <div className="skl-grid">
              {customSkills.map((s) => (
                // Wrapper div: the card is itself a <button>, so the delete
                // affordance must be a sibling — nested buttons are invalid.
                <div key={s.id} className="skl-card-wrap">
                  <button
                    type="button"
                    className="skl-card"
                    onClick={() => onInvoke(s)}
                    title={`${s.trigger} — start a thread with this skill`}
                  >
                    <span className="skl-card-icon">
                      <IconWand size={16} />
                    </span>
                    <span className="skl-card-t">{s.name}</span>
                    <span className="skl-card-d">{skillBlurb(s.description, s.name)}</span>
                    <span className="skl-card-foot">
                      <span className="skl-by">
                        <IconUser size={11} />
                        {s.uploader_name || "Your team"}
                      </span>
                    </span>
                  </button>
                  {deletingId === s.id ? (
                    <span className="skl-card-confirm" role="status">
                      <span className="skl-del-spin" aria-hidden />
                      Deleting…
                    </span>
                  ) : deletePendingId === s.id ? (
                    <span
                      className="skl-card-confirm"
                      role="group"
                      aria-label={`Confirm deleting ${s.name}`}
                    >
                      Delete for the whole company?
                      <button
                        type="button"
                        className="btn btn-sm skl-del-yes"
                        onClick={() => onDeleteConfirm(s.id)}
                      >
                        Delete
                      </button>
                      <button
                        type="button"
                        className="btn btn-sm"
                        onClick={() => onDeleteRequest(null)}
                      >
                        Cancel
                      </button>
                    </span>
                  ) : (
                    <button
                      type="button"
                      className="skl-card-del"
                      aria-label={`Delete ${s.name}`}
                      title="Delete this skill"
                      onClick={() => onDeleteRequest(s.id)}
                    >
                      <IconTrash size={13} />
                    </button>
                  )}
                </div>
              ))}
            </div>
          </section>
        )}

        {loading ? (
          <p className="skl-placeholder">Loading skills…</p>
        ) : groups.length === 0 && customSkills.length === 0 ? (
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

function SkillsScreenContent() {
  const { goTo, setPendingOndemandDraft, showToast } = useNavigation()
  const searchParams = useSearchParams()
  const [skills, setSkills] = useState<SkillInfo[]>([])
  const [customSkills, setCustomSkills] = useState<CustomSkillInfo[]>([])
  const [customError, setCustomError] = useState<string | null>(null)
  const [deletePendingId, setDeletePendingId] = useState<string | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [uploadOpen, setUploadOpen] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  // `?q=` seeds the filter so the global search palette can deep-link a
  // specific skill (`/skills?q=<label>`); after mount the input owns it.
  const qParam = searchParams.get("q")
  const [query, setQuery] = useState(() => qParam ?? "")

  // Already on /skills when the palette pushes a new ?q= → no remount, so the
  // initializer above won't re-run. Adopt the changed param; typing in the
  // input doesn't change the param, so this never fights the user.
  useEffect(() => {
    if (qParam != null) setQuery(qParam)
  }, [qParam])

  useEffect(() => {
    let cancelled = false
    // Independent fetches: a custom-skills failure must not blank the built-in
    // catalog (and vice versa) — each surfaces its own inline error.
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
    skillsApi
      .list()
      .then((r) => {
        if (cancelled) return
        setCustomSkills(r.skills)
        setCustomError(null)
      })
      .catch((e) => {
        if (cancelled) return
        setCustomError(e instanceof Error ? e.message : "Could not load custom skills")
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
    // A custom skill that shares a built-in's id REPLACES it (PRD 1854
    // override) — drop the built-in card so the catalog can't advertise a
    // skill that no longer runs; the Custom section carries the replacement.
    const customSlugs = new Set(customSkills.map((s) => s.slug))
    const catalog = skills.filter((s) => !customSlugs.has(s.id))
    const visible = !q
      ? catalog
      : catalog.filter(
          (s) =>
            s.label.toLowerCase().includes(q) ||
            s.trigger.toLowerCase().includes(q) ||
            s.description.toLowerCase().includes(q) ||
            s.category.toLowerCase().includes(q),
        )
    return groupSkills(visible)
  }, [skills, customSkills, query])

  // Custom skills join the same search — name, trigger, or description.
  const visibleCustom = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return customSkills
    return customSkills.filter(
      (s) =>
        s.name.toLowerCase().includes(q) ||
        s.trigger.toLowerCase().includes(q) ||
        s.description.toLowerCase().includes(q),
    )
  }, [customSkills, query])

  // Hand off to the chat: pre-fill the composer with the skill's trigger and
  // navigate. ChatScreen consumes pendingOndemandDraft once — no active tab →
  // pre-filled composer; active tab → a fresh tab seeded with the trigger.
  function onInvoke(skill: { trigger: string }) {
    setPendingOndemandDraft(`${skill.trigger} `)
    goTo("chat")
  }

  // Delete a custom skill (company-wide — every workspace shares one library,
  // so the inline confirm says as much). While the call is in flight the card
  // shows a Deleting… spinner; it leaves the grid only after the API confirms,
  // and a failure keeps it with the reason surfaced as a toast.
  async function onDeleteConfirm(id: string) {
    const skill = customSkills.find((s) => s.id === id)
    setDeletePendingId(null)
    setDeletingId(id)
    try {
      await skillsApi.remove(id)
      setCustomSkills((prev) => prev.filter((s) => s.id !== id))
      showToast(
        "Skill deleted",
        `${skill?.name ?? "The skill"} was removed from your company's library and no longer routes in chat.`,
      )
    } catch (e) {
      showToast(
        "Couldn't delete the skill",
        e instanceof Error ? e.message : "Please try again.",
      )
    } finally {
      setDeletingId(null)
    }
  }

  // Upload a custom skill: POST, then prepend the created skill so it appears
  // in the library immediately (the list endpoint orders newest-first too).
  async function onUpload(file: File, name: string, description: string) {
    const created = await skillsApi.upload(file, name, description)
    setCustomSkills((prev) => [created, ...prev])
    showToast(
      "Skill uploaded",
      created.overrides_builtin
        ? `${created.name} replaces the built-in Sprntly skill ${created.trigger} for your whole company.`
        : `${created.name} is in your library — invoke it with ${created.trigger} in chat.`,
    )
  }

  return (
    // hideChromeStrip: the surface has its own .skl-top title bar, so the
    // main-column chrome strip would just duplicate "Skills" above it.
    <AppLayout mainClassName="main--skills" hideChromeStrip>
      <SkillsView
        groups={groups}
        customSkills={visibleCustom}
        customError={customError}
        loading={loading}
        error={error}
        query={query}
        onQueryChange={setQuery}
        onInvoke={onInvoke}
        onCreate={() => setUploadOpen(true)}
        deletePendingId={deletePendingId}
        deletingId={deletingId}
        onDeleteRequest={setDeletePendingId}
        onDeleteConfirm={onDeleteConfirm}
      />
      <UploadSkillModal
        open={uploadOpen}
        onUpload={onUpload}
        onClose={() => setUploadOpen(false)}
        builtinSlugs={skills.map((s) => s.id)}
      />
    </AppLayout>
  )
}

export function SkillsScreen() {
  // useSearchParams (the ?q= deep link) needs a Suspense boundary in Next 15 —
  // same pattern as SettingsScreen.
  return (
    <Suspense fallback={<AppLayout mainClassName="main--skills" hideChromeStrip><p>Loading skills…</p></AppLayout>}>
      <SkillsScreenContent />
    </Suspense>
  )
}
