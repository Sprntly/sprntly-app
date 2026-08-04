"use client"

// Skills · the company's own skill library.
//
// Top-level surface listing the CUSTOM skills this company uploaded (PRD 1854
// — .md/.zip files, company-scoped so every workspace shares one library).
// Clicking a card hands off to the chat via setPendingOndemandDraft with the
// skill's `/trigger ` pre-filled, so the user lands in a focused thread ready
// to type their specifics; the backend's slash fast-path (qa_agent) then pins
// that skill with confidence 1.0 on send. That fast-path is now CUSTOM-ONLY,
// which is exactly what this screen lists.
//
// The vendored BUILT-IN catalog used to render here as a second, grouped
// listing off GET /v1/ask/skills. It is gone: the library is down to nine
// method docs, each bound by name from the one pipeline that needs it, and a
// chat turn can invoke none of them — so every card was a trigger that would
// have done nothing. "Create or upload skill" opens the upload modal
// (UploadSkillModal → POST /v1/skills); a created skill appears here
// immediately, byline = uploader.
//
// Each card carries a hover-revealed pencil and trash pair. The pencil opens
// EditSkillModal, which fetches the skill's method text (GET /v1/skills/{id} —
// the list is metadata-only) and PATCHes name/description/method back. Two
// outcomes of a save need handling here rather than in the modal: a rename
// re-derives the trigger, so the response's `slug`/`trigger` replace the
// card's, and a rename onto another of the company's skill names DELETES that
// skill server-side, so `replaced_skill_id` takes its card out of the grid.
//
// The view layer (SkillsView) is pure and prop-driven so it can be
// markup-tested without the API; SkillsScreen owns state, API, and navigation.

import { Suspense, useEffect, useMemo, useState } from "react"
import { useSearchParams } from "next/navigation"
import {
  IconPencil,
  IconPlus,
  IconTrash,
  IconUser,
  IconWand,
} from "@tabler/icons-react"
import { AppLayout } from "./AppLayout"
import { UploadSkillModal } from "../../shared/UploadSkillModal"
import { EditSkillModal } from "../../shared/EditSkillModal"
import { useNavigation } from "../../../context/NavigationContext"
import {
  skillsApi,
  type CustomSkillDetail,
  type CustomSkillInfo,
} from "../../../lib/api"

/** First sentence of a skill's description, minus the routing-guidance tail
 *  ("Use when the user says …"). Descriptions are written for the classifier,
 *  so cards show just the lead. Pure → testable. */
export function skillBlurb(description: string, label: string): string {
  const d = (description || "").trim()
  if (!d) return `Run the ${label} workflow`
  const useWhen = d.search(/\bUse when\b/)
  const head = useWhen > 0 ? d.slice(0, useWhen) : d
  const sentence = head.match(/^[^.!?]*[.!?]/)
  return (sentence ? sentence[0] : head).trim().replace(/[.!?]+$/, "")
}

/** Pure presentational view — all state arrives as props, so it renders
 *  identically in a static-markup test (no API, no effects). */
export function SkillsView({
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
  onEditRequest,
}: {
  /** The company's uploaded skills (already search-filtered by the caller). */
  customSkills: CustomSkillInfo[]
  /** Inline notice when the fetch failed after a list was already on screen. */
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
  /** Open the edit modal for a skill. Non-destructive on its own — the modal
   *  loads the method text and owns every confirm from there. */
  onEditRequest: (id: string) => void
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
                // Wrapper div: the card is itself a <button>, so the edit and
                // delete affordances must be siblings — nested buttons are
                // invalid.
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
                    // Edit and delete are one hover-revealed pair; the pencil
                    // sits left of the trash so the destructive one stays in
                    // the corner it has always been in.
                    <span className="skl-card-actions">
                      <button
                        type="button"
                        className="skl-card-act"
                        aria-label={`Edit ${s.name}`}
                        title="Edit this skill"
                        onClick={() => onEditRequest(s.id)}
                      >
                        <IconPencil size={13} />
                      </button>
                      <button
                        type="button"
                        className="skl-card-act skl-card-del"
                        aria-label={`Delete ${s.name}`}
                        title="Delete this skill"
                        onClick={() => onDeleteRequest(s.id)}
                      >
                        <IconTrash size={13} />
                      </button>
                    </span>
                  )}
                </div>
              ))}
            </div>
          </section>
        )}

        {loading ? (
          <p className="skl-placeholder">Loading skills…</p>
        ) : customSkills.length === 0 ? (
          <p className="skl-placeholder">
            {query.trim()
              ? `No skills match “${query.trim()}”.`
              : "No skills yet — upload one to get started."}
          </p>
        ) : null}
      </div>
    </div>
  )
}

function SkillsScreenContent() {
  const { goTo, setPendingOndemandDraft, showToast } = useNavigation()
  const searchParams = useSearchParams()
  const [customSkills, setCustomSkills] = useState<CustomSkillInfo[]>([])
  const [customError, setCustomError] = useState<string | null>(null)
  const [deletePendingId, setDeletePendingId] = useState<string | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [uploadOpen, setUploadOpen] = useState(false)
  // Edit modal: the id opens it immediately (so the dialog is on screen while
  // the method text loads), the detail arrives from GET /v1/skills/{id}.
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editSkill, setEditSkill] = useState<CustomSkillDetail | null>(null)
  const [editLoading, setEditLoading] = useState(false)
  const [editError, setEditError] = useState<string | null>(null)
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

  // ONE fetch now. The built-in catalog this screen used to list alongside the
  // uploads is gone — those skills are bound by name from their own pipelines
  // and a chat turn cannot invoke any of them, so every card would have been a
  // trigger that does nothing. `error` / `loading` stay: they now describe this
  // fetch, and `customError` remains for the case where it fails after a
  // successful first load.
  useEffect(() => {
    let cancelled = false
    skillsApi
      .list()
      .then((r) => {
        if (cancelled) return
        setCustomSkills(r.skills)
        setCustomError(null)
        setError(null)
      })
      .catch((e) => {
        if (cancelled) return
        const message = e instanceof Error ? e.message : "Could not load custom skills"
        setCustomError(message)
        setError(message)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  // Custom skills search — name, trigger, or description.
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

  // Open the edit modal for a skill. The library list is metadata-only, so the
  // method text has to be fetched before the form can pre-fill — the dialog
  // opens straight away and shows its own loading state rather than leaving the
  // pencil looking dead while the request is in flight.
  function onEditRequest(id: string) {
    setEditingId(id)
    setEditSkill(null)
    setEditError(null)
    setEditLoading(true)
    skillsApi
      .get(id)
      .then((detail) => {
        setEditSkill(detail)
      })
      .catch((e) => {
        setEditError(
          e instanceof Error ? e.message : "Could not load this skill.",
        )
      })
      .finally(() => {
        setEditLoading(false)
      })
  }

  function closeEdit() {
    setEditingId(null)
    setEditSkill(null)
    setEditError(null)
    setEditLoading(false)
  }

  // Save an edit. The server answers with the edited skill (its `slug` and
  // `trigger` are authoritative — a rename re-derives them) plus, when the new
  // name was already one of the company's, the id of the skill it replaced.
  // That row is gone server-side, so its card has to leave the grid with it.
  async function onEditSave(
    id: string,
    patch: { name: string; description: string; method: string },
  ) {
    const updated = await skillsApi.update(id, patch)
    setCustomSkills((prev) =>
      prev
        .filter((s) => s.id !== updated.replaced_skill_id)
        .map((s) => (s.id === updated.id ? { ...s, ...updated } : s)),
    )
    showToast(
      "Skill updated",
      updated.replaced_skill_id != null
        // A replacing rename always ends with two skills sharing one name, so
        // naming the removed one would just repeat the new one back.
        ? `${updated.name} is saved and replaced your other skill of the same name — invoke it with ${updated.trigger} in chat.`
        : `${updated.name} is saved — invoke it with ${updated.trigger} in chat.`,
    )
  }

  // Upload a custom skill: POST, then prepend the created skill so it appears
  // in the library immediately (the list endpoint orders newest-first too).
  //
  // A name this company already used REPLACES that skill server-side (same
  // row, same id, same trigger), so the response has to swap the existing card
  // in place — prepending would render the same id twice. `replaced` comes
  // back on the 201; the id check is the belt-and-braces version for a list
  // that predates the field.
  async function onUpload(file: File, name: string, description: string) {
    const created = await skillsApi.upload(file, name, description)
    const wasReplaced =
      created.replaced === true || customSkills.some((s) => s.id === created.id)
    setCustomSkills((prev) =>
      prev.some((s) => s.id === created.id)
        ? prev.map((s) => (s.id === created.id ? created : s))
        : [created, ...prev],
    )
    if (wasReplaced) {
      showToast(
        "Skill updated",
        `${created.name} now uses the file you just uploaded — it is still invoked with ${created.trigger} in chat.`,
      )
      return
    }
    showToast(
      "Skill uploaded",
      created.name_conflict
        ? `That name was already taken, so ${created.name} is invoked with ${created.trigger} in chat — the skill that had the name still works too.`
        : `${created.name} is in your library — invoke it with ${created.trigger} in chat.`,
    )
  }

  return (
    // hideChromeStrip: the surface has its own .skl-top title bar, so the
    // main-column chrome strip would just duplicate "Skills" above it.
    <AppLayout mainClassName="main--skills" hideChromeStrip>
      <SkillsView
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
        onEditRequest={onEditRequest}
      />
      <EditSkillModal
        open={editingId != null}
        skill={editSkill}
        loading={editLoading}
        loadError={editError}
        // The skill being edited is excluded: renaming it to its own name is
        // not a replacement, and passing it would warn about replacing itself.
        // Off the full library, not the search-filtered view — a collision with
        // a skill the query happens to hide is still a collision.
        others={customSkills.filter((s) => s.id !== editingId)}
        onSave={(patch) => onEditSave(editingId as string, patch)}
        onClose={closeEdit}
      />
      <UploadSkillModal
        open={uploadOpen}
        onUpload={onUpload}
        onClose={() => setUploadOpen(false)}
        // No `builtinSlugs`: this screen no longer fetches the vendored
        // catalog, so it has nothing honest to pass. That only costs the
        // "…is also the name of a built-in" trigger preview. The other notice
        // (a name this company already used, which the server replaces in
        // place) is driven by `customSkills` below and is unaffected, and the
        // server's 201 remains authoritative about the trigger assigned.
        customSkills={customSkills}
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
