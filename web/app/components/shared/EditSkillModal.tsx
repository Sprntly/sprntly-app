/**
 * Modal for editing a custom skill in place (PRD 1854) — name, description,
 * and the method text itself, no file round-trip.
 *
 * Sibling of UploadSkillModal and shaped the same way: a pure View (props in,
 * JSX out — testable without hooks) wrapped by a small state-owning component,
 * with the server as the authoritative validator (routes/custom_skills.py
 * PATCH returns readable `detail` strings) and only the cheap checks mirrored
 * client-side so obvious mistakes never cost a round-trip.
 *
 * Three things this form has to say out loud, because each one has a
 * consequence the user cannot see from the fields alone:
 *
 *   - RENAMING MOVES THE TRIGGER. The slug is derived from the name, so
 *     `/old-slug` stops working the moment a rename saves. Shown as a status
 *     notice while the name differs from the one loaded.
 *   - RENAMING ONTO ANOTHER OF THEIR SKILLS DELETES THAT SKILL. The server
 *     replaces it (same equivalence the re-upload path uses: slugify(name)),
 *     so this is gated behind an explicit two-step confirm in the footer — the
 *     same shape as the Skills screen's delete confirm. Never silent.
 *   - A .ZIP SKILL'S SUPPORTING FILES SURVIVE. The form edits the main method
 *     only; modules and references stay attached, and they still count against
 *     the 50,000-character cap, which is why `attached_chars` is in the
 *     content pre-check rather than a bare method-length test.
 *
 * On a server rejection every input is kept so the user fixes and retries,
 * matching the upload modal's retry contract.
 */
"use client"

import { useEffect, useState } from "react"
import type { CustomSkillDetail } from "../../lib/api"
import {
  MAX_SKILL_CONTENT_CHARS,
  SKILL_CONTENT_CAP_ERROR,
  slugifyName,
} from "./UploadSkillModal"

/** The company's other skills, as the collision check needs them. */
export type OtherSkill = { id: string; slug: string; name: string }

/** The one of `others` this name would replace on save, or undefined.
 *  Compared slugified — the same equivalence the server matches on, so
 *  "PRD  author!" finds the skill named "PRD Author". Exported for the test
 *  and for the screen, which shows nothing of its own but must not disagree. */
export function replacementTarget(
  name: string,
  others: OtherSkill[],
): OtherSkill | undefined {
  const base = slugifyName(name)
  if (!base) return undefined
  return others.find((s) => slugifyName(s.name) === base)
}

export type EditSkillModalViewProps = {
  open: boolean
  /** The skill being edited, once loaded. Null while the fetch is in flight
   *  or after it failed — the form renders its own status instead. */
  skill: CustomSkillDetail | null
  /** True while the skill's detail (its method text) is being fetched. */
  loading: boolean
  /** The detail fetch failed — the form can't be shown at all. */
  loadError: string | null
  name: string
  description: string
  method: string
  /** True while the save is in flight. */
  submitting: boolean
  /** Inline error — client pre-check or server `detail`. */
  error: string | null
  /** Marks empty required fields once the user has interacted with them. */
  touched: { name: boolean; description: boolean; method: boolean }
  /** The skill this save would REPLACE (delete), or null. Drives both the
   *  assertive warning and the two-step confirm on the save button. */
  replaces: OtherSkill | null
  /** True once the user has asked to save a replacing rename — the footer
   *  shows the confirm, and only its Replace button actually saves. */
  confirmingReplace: boolean
  /** The trigger the skill currently answers to, when the name has changed
   *  and saving will move it. Null when nothing about the trigger changes. */
  triggerLeaving: string | null
  onNameChange: (next: string) => void
  onDescriptionChange: (next: string) => void
  onMethodChange: (next: string) => void
  /** Save, or arm the replace confirm when the rename is destructive. */
  onSubmit: () => void
  /** Stand down the armed replace confirm without saving. */
  onCancelReplace: () => void
  onClose: () => void
}

export function EditSkillModalView({
  open,
  skill,
  loading,
  loadError,
  name,
  description,
  method,
  submitting,
  error,
  touched,
  replaces,
  confirmingReplace,
  triggerLeaving,
  onNameChange,
  onDescriptionChange,
  onMethodChange,
  onSubmit,
  onCancelReplace,
  onClose,
}: EditSkillModalViewProps) {
  if (!open) return null
  const nameMissing = touched.name && name.trim().length === 0
  const descriptionMissing = touched.description && description.trim().length === 0
  const methodMissing = touched.method && method.trim().length === 0
  const attached = (skill?.modules.length ?? 0) + (skill?.references.length ?? 0)
  const canSubmit =
    skill != null &&
    name.trim().length > 0 &&
    description.trim().length > 0 &&
    method.trim().length > 0 &&
    !submitting
  return (
    <div
      className="modal-overlay open"
      onClick={(e) => {
        // Backdrop click closes; clicks inside the modal shouldn't.
        if (e.target === e.currentTarget) onClose()
      }}
      aria-hidden={false}
    >
      <div className="modal modal-md" role="dialog" aria-label="Edit skill">
        <div className="modal-head">
          <h2 className="modal-title">Edit skill</h2>
          <button
            type="button"
            className="modal-close"
            onClick={onClose}
            aria-label="Close"
          >
            ✕
          </button>
        </div>
        <div className="modal-body skl-edit-body">
          {loading ? (
            <p className="modal-sub" role="status">
              Loading the skill…
            </p>
          ) : loadError ? (
            <p className="settings-msg settings-msg-error" role="alert">
              {loadError}
            </p>
          ) : (
            <>
              <p className="modal-sub">
                Change what this skill is called, what it says it does, and the
                method text Sprntly follows when you invoke it. Everyone in your
                company shares this library, so the edit applies for the whole
                team.
              </p>

              <label className="field-label" htmlFor="edit-skill-name">
                Skill name <span aria-hidden>*</span>
              </label>
              <input
                id="edit-skill-name"
                type="text"
                className="input"
                value={name}
                onChange={(e) => onNameChange(e.target.value)}
                placeholder="e.g. Estimation helper"
                autoComplete="off"
                maxLength={64}
                aria-required
                aria-invalid={nameMissing || undefined}
              />
              {nameMissing ? (
                <p className="settings-msg settings-msg-error" role="alert">
                  Skill name is required.
                </p>
              ) : null}
              {replaces ? (
                // role="alert": saving DELETES a skill the team already has.
                <p className="settings-msg settings-warning" role="alert">
                  You already have a skill named “{name.trim()}”. Saving replaces
                  it — the skill at /{replaces.slug} is removed from your
                  library and this one takes its place.
                </p>
              ) : triggerLeaving ? (
                // Informational: the rename is fine, it just moves the trigger.
                <p className="settings-msg settings-warning" role="status">
                  Renaming changes this skill’s trigger, so {triggerLeaving} stops
                  working in chat. Its new trigger is shown after you save.
                </p>
              ) : null}

              <label className="field-label" htmlFor="edit-skill-desc">
                What does this skill do? <span aria-hidden>*</span>
              </label>
              <textarea
                id="edit-skill-desc"
                className="input"
                rows={3}
                value={description}
                onChange={(e) => onDescriptionChange(e.target.value)}
                placeholder="e.g. Scores features by reach × confidence using our team's estimation template."
                maxLength={1024}
                aria-required
                aria-invalid={descriptionMissing || undefined}
              />
              {descriptionMissing ? (
                <p className="settings-msg settings-msg-error" role="alert">
                  Skill description is required.
                </p>
              ) : null}
              <p className="modal-sub">
                Teammates see this in the skill library and the / picker, and
                Sprntly reads it when choosing a skill — so say when to reach for
                it.
              </p>

              <label className="field-label" htmlFor="edit-skill-method">
                Method <span aria-hidden>*</span>
              </label>
              <textarea
                id="edit-skill-method"
                className="input skl-edit-method"
                rows={14}
                value={method}
                onChange={(e) => onMethodChange(e.target.value)}
                placeholder="# My method&#10;&#10;Steps Sprntly should follow…"
                spellCheck={false}
                aria-required
                aria-invalid={methodMissing || undefined}
              />
              {methodMissing ? (
                <p className="settings-msg settings-msg-error" role="alert">
                  The skill method is empty — add the skill&rsquo;s method text
                  and try again.
                </p>
              ) : null}
              <p className="modal-sub">
                Markdown, up to {MAX_SKILL_CONTENT_CHARS.toLocaleString("en-US")}{" "}
                characters across the whole skill.
                {attached > 0
                  ? ` The ${attached} supporting file${attached === 1 ? "" : "s"} from the uploaded archive stay attached — you're editing the main method only.`
                  : ""}
                {skill?.has_file
                  ? " Saving drops the original uploaded file, since this text is what the skill now says."
                  : ""}
              </p>

              {error ? (
                <p className="settings-msg settings-msg-error" role="alert">
                  {error}
                </p>
              ) : null}
            </>
          )}
        </div>
        <div className="modal-foot">
          {confirmingReplace && replaces ? (
            // Two-step confirm, in the spirit of the Skills screen's delete
            // confirm: the destructive outcome is named, and only the explicit
            // button below performs it.
            <span
              className="skl-edit-confirm"
              role="group"
              aria-label={`Confirm replacing ${replaces.name}`}
            >
              Replace “{replaces.name}”? It is deleted for the whole company.
              <button
                type="button"
                className="btn btn-sm skl-del-yes"
                disabled={submitting}
                onClick={onSubmit}
              >
                {submitting ? "Saving…" : "Replace and save"}
              </button>
              <button
                type="button"
                className="btn btn-sm"
                disabled={submitting}
                onClick={onCancelReplace}
              >
                Cancel
              </button>
            </span>
          ) : (
            <>
              <button type="button" className="btn btn-sm" onClick={onClose}>
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-sm btn-primary"
                disabled={!canSubmit}
                onClick={onSubmit}
              >
                {submitting ? "Saving…" : "Save changes"}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

// ───────────────────── Hooks-wired wrapper ─────────────────────

type Props = {
  open: boolean
  /** The skill being edited, once its detail (method text) has loaded. */
  skill: CustomSkillDetail | null
  loading: boolean
  loadError: string | null
  /**
   * The company's OTHER custom skills (this one excluded by the caller). Used
   * only to detect the rename that would replace one of them; the server
   * re-runs the same match against the live library and stays authoritative.
   */
  others: OtherSkill[]
  /**
   * Performs the save. Throws or rejects on failure — the modal catches and
   * shows the message inline, keeping the user's inputs so they can retry.
   */
  onSave: (patch: {
    name: string
    description: string
    method: string
  }) => Promise<void>
  onClose: () => void
}

export function EditSkillModal({
  open,
  skill,
  loading,
  loadError,
  others,
  onSave,
  onClose,
}: Props) {
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [method, setMethod] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [touched, setTouched] = useState({
    name: false,
    description: false,
    method: false,
  })
  const [confirmingReplace, setConfirmingReplace] = useState(false)

  // Seed the fields from the loaded skill. Keyed on the id so re-rendering
  // (or the parent re-fetching the library) never stomps on what the user has
  // typed — only opening a DIFFERENT skill re-seeds.
  const skillId = skill?.id ?? null
  useEffect(() => {
    if (!skill) return
    setName(skill.name)
    setDescription(skill.description)
    setMethod(skill.method)
    setError(null)
    setTouched({ name: false, description: false, method: false })
    setConfirmingReplace(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- re-seed on identity, not on every field
  }, [skillId])

  const replaces = replacementTarget(name, others) ?? null
  // The trigger only moves when the NAME changes — the server leaves the slug
  // alone otherwise, including a `-2` this skill was handed at upload time.
  const renaming =
    skill != null && slugifyName(name) !== slugifyName(skill.name) && slugifyName(name) !== ""
  const triggerLeaving = renaming && skill ? skill.trigger : null

  async function save() {
    if (!skill) return
    // Cheap client mirror of the server's cap — measured over the WHOLE parsed
    // skill, so the archive's attached files count too.
    if (method.length + skill.attached_chars > MAX_SKILL_CONTENT_CHARS) {
      setError(SKILL_CONTENT_CAP_ERROR)
      setConfirmingReplace(false)
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      await onSave({
        name: name.trim(),
        description: description.trim(),
        // The method keeps its whitespace verbatim — it is markdown, and
        // indentation inside a fenced block is load-bearing.
        method,
      })
      setConfirmingReplace(false)
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setConfirmingReplace(false)
    } finally {
      setSubmitting(false)
    }
  }

  function handleSubmit() {
    if (!skill || submitting) return
    if (
      name.trim().length === 0 ||
      description.trim().length === 0 ||
      method.trim().length === 0
    ) {
      setTouched({ name: true, description: true, method: true })
      return
    }
    // A rename that would delete one of the company's other skills never goes
    // through on the first click — arm the confirm and wait for the explicit
    // "Replace and save".
    if (replaces && !confirmingReplace) {
      setConfirmingReplace(true)
      return
    }
    void save()
  }

  return (
    <EditSkillModalView
      open={open}
      skill={skill}
      loading={loading}
      loadError={loadError}
      name={name}
      description={description}
      method={method}
      submitting={submitting}
      error={error}
      touched={touched}
      replaces={replaces}
      confirmingReplace={confirmingReplace && replaces != null}
      triggerLeaving={triggerLeaving}
      onNameChange={(v) => {
        setName(v)
        setTouched((t) => ({ ...t, name: true }))
        // The armed confirm names a specific skill; a changed name may point at
        // a different one (or none), so it has to be re-armed deliberately.
        setConfirmingReplace(false)
      }}
      onDescriptionChange={(v) => {
        setDescription(v)
        setTouched((t) => ({ ...t, description: true }))
      }}
      onMethodChange={(v) => {
        setMethod(v)
        setTouched((t) => ({ ...t, method: true }))
      }}
      onSubmit={handleSubmit}
      onCancelReplace={() => setConfirmingReplace(false)}
      onClose={onClose}
    />
  )
}
