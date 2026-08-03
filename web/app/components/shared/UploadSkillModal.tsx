/**
 * Modal for uploading a custom skill (PRD 1854) — a .md file or a .zip
 * archive containing at least one .md, plus a required name + description.
 *
 * Mirrors UploadSourceModal's shape: a pure View (props in, JSX out — testable
 * without hooks) wrapped by a small state-owning component. The server is the
 * authoritative validator (routes/custom_skills.py returns readable `detail`
 * strings); this modal mirrors only the cheap checks client-side so obvious
 * mistakes never cost a round-trip:
 *   - file type: .md / .zip only (case-insensitive; also pre-filtered via
 *     `accept`, but a picked-anyway file still gets the inline error)
 *   - size: ≤ 20 MB, rejected before any bytes are transmitted
 *   - content: ≤ 50,000 characters — checked client-side for bare .md files
 *     only (zip content is parsed server-side), on submit
 *   - name + description: required, non-whitespace — the submit gates on them
 *     and empty-but-touched fields are highlighted (aria-invalid + field-error)
 * A name that's already taken shows a notice as the user types — neither case
 * blocks the upload, but they end differently:
 *   - taken by a BUILT-IN Sprntly skill → the upload is accepted and nothing
 *     is replaced; both skills stay invocable, so this one gets the next free
 *     trigger (/prd-author-2). The notice previews that trigger.
 *   - taken by one of the company's OWN custom skills → the upload REPLACES
 *     that skill with this version (same trigger, new content), so the notice
 *     says so before the user commits to it. Announced assertively because it
 *     overwrites something the team already has.
 * Both are advisory: the client only knows the ROUTABLE built-in catalog (the
 * server also guards the non-routable ids) and its custom list can be stale,
 * so the server's `trigger` / `name_conflict` / `replaced` are the
 * authoritative answers.
 * On a server rejection the modal keeps every input intact so the user fixes
 * and retries (the failure ACs across the PRD's validation tickets).
 */
"use client"

import { useState } from "react"

/** 20 MB — mirrors skills_storage.MAX_SKILL_UPLOAD_BYTES (the PRD cap). */
export const MAX_SKILL_FILE_BYTES = 20 * 1024 * 1024

/** Characters of skill text — mirrors skills.custom.MAX_SKILL_CONTENT_CHARS
 *  (the parsed method is injected into the prompt on every invocation). */
export const MAX_SKILL_CONTENT_CHARS = 50_000

/** The server's over-cap `detail`, mirrored so the .md pre-check reads the
 *  same whether it was caught client- or server-side. */
export const SKILL_CONTENT_CAP_ERROR = `Skill content exceeds the ${MAX_SKILL_CONTENT_CHARS.toLocaleString(
  "en-US",
)} character limit. Please trim the skill text and try again.`

/** File text via FileReader (File.text() is missing in jsdom — same reason
 *  ChatScreen's attachment reader uses it). */
function readSkillFileText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result ?? ""))
    reader.onerror = () => reject(reader.error ?? new Error("Could not read the file."))
    reader.readAsText(file)
  })
}

/** Client mirror of skills.custom.slugify — display name → /trigger slug. */
export function slugifyName(name: string): string {
  return name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .replace(/-{2,}/g, "-")
}

/** Client mirror of skills.custom.available_slug — the first free id in the
 *  `base`, `base-2`, `base-3` series, given the ids already spoken for. Used
 *  only to PREVIEW the trigger a colliding name will get; the server picks the
 *  real one against the live library. */
export function availableSlug(base: string, taken: string[]): string {
  const used = new Set(taken)
  if (!used.has(base)) return base
  let n = 2
  while (used.has(`${base}-${n}`)) n += 1
  return `${base}-${n}`
}

/** Client-side mirror of the accepted upload formats. */
export function skillFileError(file: File): string | null {
  const ext = file.name.includes(".") ? file.name.split(".").pop()!.toLowerCase() : ""
  if (ext !== "md" && ext !== "zip") {
    return "Only .md files and .zip archives are accepted. Please try again with the correct format."
  }
  if (file.size > MAX_SKILL_FILE_BYTES) {
    return "File size exceeds the 20 MB limit. Please upload a smaller file."
  }
  return null
}

export type UploadSkillModalViewProps = {
  open: boolean
  /** Skill name (controlled). Required — submit gates on it. */
  name: string
  /** What the skill does (controlled). Required — submit gates on it. */
  description: string
  /** The picked file, if any (single file — one skill per upload). */
  file: File | null
  /** True while the upload is in flight. */
  submitting: boolean
  /** Inline error — client pre-check or server `detail`. */
  error: string | null
  /** Non-blocking: the typed name is already taken. `assertive` marks the
   *  consequential case (the company's own library has the name, so this
   *  upload replaces that skill) so it is announced as an alert rather than an
   *  FYI — submit stays enabled either way, since the server is the authority
   *  on both. */
  nameNotice: { text: string; assertive: boolean } | null
  /** Marks empty required fields once the user has interacted with them. */
  touched: { name: boolean; description: boolean }
  onNameChange: (next: string) => void
  onDescriptionChange: (next: string) => void
  onFileChange: (next: File | null) => void
  onSubmit: () => void
  onClose: () => void
}

export function UploadSkillModalView({
  open,
  name,
  description,
  file,
  submitting,
  error,
  nameNotice,
  touched,
  onNameChange,
  onDescriptionChange,
  onFileChange,
  onSubmit,
  onClose,
}: UploadSkillModalViewProps) {
  if (!open) return null
  const nameMissing = touched.name && name.trim().length === 0
  const descriptionMissing = touched.description && description.trim().length === 0
  const canSubmit =
    file != null &&
    name.trim().length > 0 &&
    description.trim().length > 0 &&
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
      <div className="modal modal-sm" role="dialog" aria-label="Upload a custom skill">
        <div className="modal-head">
          <h2 className="modal-title">Upload a custom skill</h2>
          <button
            type="button"
            className="modal-close"
            onClick={onClose}
            aria-label="Close"
          >
            ✕
          </button>
        </div>
        <div className="modal-body">
          <p className="modal-sub">
            Encode your own workflow as a Markdown skill file and invoke it from
            chat like any built-in — a .md file, or a .zip with a SKILL.md plus
            supporting files.
          </p>

          <label className="field-label" htmlFor="upload-skill-name">
            Skill name <span aria-hidden>*</span>
          </label>
          <input
            id="upload-skill-name"
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
          {nameNotice ? (
            // role="status" for the FYI (the upload proceeds, it just gets its
            // own trigger); role="alert" when it will overwrite a skill the
            // company already has.
            <p
              className="settings-msg settings-warning"
              role={nameNotice.assertive ? "alert" : "status"}
            >
              {nameNotice.text}
            </p>
          ) : null}

          <label className="field-label" htmlFor="upload-skill-desc">
            What does this skill do? <span aria-hidden>*</span>
          </label>
          <textarea
            id="upload-skill-desc"
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
            Teammates see this in the skill library and the / picker, so say
            when to reach for it.
          </p>

          <label className="set-conn-upload" title="Choose a skill file">
            <i className="ti ti-cloud-upload" aria-hidden />
            {file ? file.name : "Choose a skill file"}
            <span className="muted">.md or .zip · up to 20 MB · up to 50,000 characters</span>
            <input
              type="file"
              accept=".md,.zip"
              disabled={submitting}
              style={{ display: "none" }}
              onChange={(e) => {
                const picked = e.target.files?.[0] ?? null
                if (picked) onFileChange(picked)
                // Reset so the same file can be picked again after a failed run.
                e.target.value = ""
              }}
            />
          </label>

          {error ? (
            <p className="settings-msg settings-msg-error" role="alert">
              {error}
            </p>
          ) : null}
        </div>
        <div className="modal-foot">
          <button type="button" className="btn btn-sm" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-sm btn-primary"
            disabled={!canSubmit}
            onClick={onSubmit}
          >
            {submitting ? "Uploading…" : "Upload skill"}
          </button>
        </div>
      </div>
    </div>
  )
}

// ───────────────────── Hooks-wired wrapper ─────────────────────

type Props = {
  open: boolean
  /**
   * Performs the upload. Throws or rejects on failure — the modal catches and
   * shows the message inline, keeping the user's inputs so they can retry.
   */
  onUpload: (file: File, name: string, description: string) => Promise<void>
  onClose: () => void
  /**
   * Built-in Sprntly skill ids, for the live "that name is taken" notice as
   * the user types. The caller passes the routable catalog it already
   * fetched; the server knows the non-routable ids too, so its 201 stays
   * authoritative about the trigger actually assigned.
   */
  builtinSlugs?: string[]
  /**
   * The company's existing custom skills. `name` catches the one this upload
   * would replace (compared slugified, since that's the equivalence the server
   * matches on); `slug` names the trigger that replacement keeps, and keeps a
   * previewed built-in trigger clear of one already handed out.
   */
  customSkills?: { slug: string; name: string }[]
}

export function UploadSkillModal({
  open,
  onUpload,
  onClose,
  builtinSlugs,
  customSkills,
}: Props) {
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [file, setFile] = useState<File | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [touched, setTouched] = useState({ name: false, description: false })

  function reset() {
    setName("")
    setDescription("")
    setFile(null)
    setError(null)
    setTouched({ name: false, description: false })
  }

  async function handleSubmit() {
    if (!file) return
    // Cheap client mirror of the server gates — no round-trip for the obvious.
    const fileError = skillFileError(file)
    if (fileError) {
      setError(fileError)
      return
    }
    // Content cap, checkable client-side only for a bare .md (its text IS the
    // skill content; a zip's members are parsed server-side). Fail-open on a
    // read error — the server stays the authoritative validator.
    if (file.name.toLowerCase().endsWith(".md")) {
      const text = await readSkillFileText(file).catch(() => null)
      if (text != null && text.length > MAX_SKILL_CONTENT_CHARS) {
        setError(SKILL_CONTENT_CAP_ERROR)
        return
      }
    }
    setSubmitting(true)
    setError(null)
    try {
      await onUpload(file, name.trim(), description.trim())
      reset()
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSubmitting(false)
    }
  }

  // Live "that name is taken" notice, recomputed as the user types. Two very
  // different cases behind one message slot: a repeat of the company's OWN
  // name REPLACES that skill (the server updates the row in place, keeping its
  // trigger), while a built-in's name is fine — nothing is replaced, the
  // upload just gets the next free trigger, which we preview here.
  const base = slugifyName(name)
  const mine = customSkills ?? []
  const replacing = base ? mine.find((s) => slugifyName(s.name) === base) : undefined
  const nameNotice = !base
    ? null
    : replacing
      ? {
          text:
            `You already have a skill named “${name.trim()}”. Uploading ` +
            `replaces it with this version — it stays at /${replacing.slug}.`,
          assertive: true,
        }
      : (builtinSlugs ?? []).includes(base)
        ? {
            text:
              `“${name.trim()}” is also the name of a built-in Sprntly skill. ` +
              `Both stay in your library — yours is invoked with /${availableSlug(base, [
                ...(builtinSlugs ?? []),
                ...mine.map((s) => s.slug),
              ])}.`,
            assertive: false,
          }
        : null

  return (
    <UploadSkillModalView
      open={open}
      name={name}
      description={description}
      file={file}
      submitting={submitting}
      error={error}
      nameNotice={nameNotice}
      touched={touched}
      onNameChange={(v) => {
        setName(v)
        setTouched((t) => ({ ...t, name: true }))
      }}
      onDescriptionChange={(v) => {
        setDescription(v)
        setTouched((t) => ({ ...t, description: true }))
      }}
      onFileChange={(f) => {
        setFile(f)
        // Surface a bad pick immediately (wrong type / oversize) rather than
        // waiting for a submit attempt.
        setError(f ? skillFileError(f) : null)
      }}
      onSubmit={() => void handleSubmit()}
      onClose={() => {
        reset()
        onClose()
      }}
    />
  )
}
