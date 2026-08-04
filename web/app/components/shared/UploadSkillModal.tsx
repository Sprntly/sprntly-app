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
 *
 * ONE upload can create SEVERAL skills: a .zip holding a folder per SKILL.md
 * imports as one skill per folder. The form cannot name N skills, so the
 * server names each from its own SKILL.md and answers with a list — which the
 * modal has to show rather than close on, because the outcome (what got
 * created, under which trigger, what was updated rather than added, and what
 * it couldn't import) is not something a toast can carry. The single-skill
 * flow is untouched: it still closes the moment the upload succeeds.
 */
"use client"

import { useEffect, useState } from "react"
import {
  IconCircleCheckFilled,
  IconFolderOpen,
  IconLoader2,
  IconSearch,
  IconUpload,
} from "@tabler/icons-react"
import { connectorsApi } from "../../lib/api"
import type { GithubSkillDiscovery, MultiSkillUploadResult } from "../../lib/api"
import { getGenerateConnectorRowState } from "../../lib/generateConnectorRowState"
import { zipFolderFiles } from "../../lib/skillFolderZip"
import { SourceConnectHint } from "../design-agent/SourceConnectHint"

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
  /** Set once a MULTI-skill archive has been imported: the modal switches
   *  from the form to the outcome, because one archive can create, update and
   *  skip skills all in the same upload. Null for every single-skill upload,
   *  which closes on success as it always has. */
  result: MultiSkillUploadResult | null
  /** Which source the user is adding a skill from. The file branch is the
   *  original modal, unchanged; "github" swaps the body for the repo picker. */
  source: SkillSource
  onSourceChange: (next: SkillSource) => void
  /** Everything the GitHub panel renders from. Grouped rather than flattened
   *  into a dozen more props, and still plain data — the panel stays pure. */
  github: GithubPanelProps
  onNameChange: (next: string) => void
  onDescriptionChange: (next: string) => void
  onFileChange: (next: File | null) => void
  /** A whole folder was picked (`webkitdirectory`): the wrapper packs its
   *  .md files into a zip client-side and the upload proceeds as if that zip
   *  had been chosen — same server path, same guards. */
  onFolderFiles: (files: File[]) => void
  onSubmit: () => void
  onClose: () => void
}

export type SkillSource = "file" | "github"

export type GithubPanelProps = {
  /** Whether the company has GitHub connected. Null while it is being checked
   *  — rendering "not connected" before we know would flash a wrong answer. */
  connected: boolean | null
  /** The repos the installation can see. Null while loading. */
  repos: { full_name: string; default_branch: string }[] | null
  reposError: boolean
  repo: string
  branch: string
  folder: string
  searching: boolean
  discovery: GithubSkillDiscovery | null
  /** Skill paths the user ticked. Nothing is ticked by default — see the
   *  wrapper for why a bulk import is opt-in. */
  selected: string[]
  importing: boolean
  error: string | null
  onRepoChange: (next: string) => void
  onBranchChange: (next: string) => void
  onFolderChange: (next: string) => void
  onSearch: () => void
  onToggle: (path: string) => void
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
  result,
  source,
  onSourceChange,
  github,
  onNameChange,
  onDescriptionChange,
  onFileChange,
  onFolderFiles,
  onSubmit,
  onClose,
}: UploadSkillModalViewProps) {
  if (!open) return null
  if (result) return <MultiSkillResult result={result} onClose={onClose} />
  // A bare .md is the only pick that needs the form to name it; an archive
  // (.zip, or a folder packed into one) names every skill from its own
  // content. So the modal opens with just the two pickers, and what gets
  // picked decides what renders below: .md → the name/description fields,
  // archive → a one-line "skills name themselves" note.
  const isMd = file != null && file.name.toLowerCase().endsWith(".md")
  const needsIdentity = isMd
  const nameMissing = touched.name && name.trim().length === 0
  const descriptionMissing = touched.description && description.trim().length === 0
  const canSubmit =
    file != null &&
    !submitting &&
    (!isMd || (name.trim().length > 0 && description.trim().length > 0))
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
          {/* Where the skill comes from. Two sources, one modal: the file
              branch below is untouched, and GitHub swaps the body for a repo
              picker rather than opening a second dialog to learn. */}
          <div
            className="ob-chip-row skill-src-row"
            role="group"
            aria-label="Skill source"
          >
            <button
              type="button"
              className={`btn btn-sm${source === "file" ? " btn-primary" : ""}`}
              aria-pressed={source === "file"}
              onClick={() => onSourceChange("file")}
            >
              Upload a file
            </button>
            <button
              type="button"
              className={`btn btn-sm${source === "github" ? " btn-primary" : ""}`}
              aria-pressed={source === "github"}
              onClick={() => onSourceChange("github")}
            >
              From GitHub
            </button>
          </div>

          {source === "github" ? (
            <GithubSkillPanel {...github} />
          ) : (
          // One column, one rhythm: every direct child of the panel is a step
          // (intro → pick → name it), evenly spaced by the panel's own gap so
          // the fields that appear after a .md pick land on the same beat as
          // everything above them.
          <div className="skill-upload-panel">
          <p className="modal-sub">
            Encode your own workflow as Markdown and invoke it from chat like
            any built-in skill.
          </p>

          {/* The pick comes FIRST: what was picked decides the rest of the
              form. A bare .md can't name itself, so it brings up the name and
              description fields below; an archive names every skill from its
              own content, so for a .zip or a folder no fields appear. */}
          <div className="skill-upload-choices">
            <label
              className={`set-conn-upload skill-upload-tile${file ? " is-picked" : ""}`}
              title={file ? `${file.name} — choose a different file` : "Choose a skill file"}
            >
              {/* Picked reads as LOADED, not just tinted: the cloud becomes a
                  filled check, the filename takes the tile's headline slot,
                  and the format hint becomes the way back out of the pick. */}
              {file ? (
                <IconCircleCheckFilled size={22} className="skill-upload-icon" aria-hidden />
              ) : (
                <IconUpload size={22} stroke={1.6} className="skill-upload-icon" aria-hidden />
              )}
              {file ? (
                <span className="file-name">{file.name}</span>
              ) : (
                <span className="skill-upload-tile-label">Choose a skill file</span>
              )}
              <span className="muted">
                {file ? "Click to choose a different file" : ".md or .zip · up to 20 MB"}
              </span>
              <input
                type="file"
                accept=".md,.zip"
                disabled={submitting}
                className="skill-upload-input"
                aria-label="Choose a skill file (.md or .zip)"
                onChange={(e) => {
                  const picked = e.target.files?.[0] ?? null
                  if (picked) onFileChange(picked)
                  // Reset so the same file can be picked again after a failed run.
                  e.target.value = ""
                }}
              />
            </label>

            {/* Dimmed once a file is picked — still a live choice (it swaps the
                pick), just no longer the one in play, so the two tiles stop
                carrying equal weight the moment one of them is answered. */}
            <label
              className={`set-conn-upload skill-upload-tile${file ? " is-standby" : ""}`}
              title="Choose a folder of skills"
            >
              <IconFolderOpen size={22} stroke={1.6} className="skill-upload-icon" aria-hidden />
              <span className="skill-upload-tile-label">Choose a folder</span>
              <span className="muted">every .md inside becomes a skill</span>
              <input
                type="file"
                disabled={submitting}
                className="skill-upload-input"
                aria-label="Choose a folder of skills"
                // Non-standard but universal: makes the browser's picker select a
                // directory and report every file inside with its relative path.
                {...({ webkitdirectory: "" } as Record<string, string>)}
                onChange={(e) => {
                  const picked = Array.from(e.target.files ?? [])
                  if (picked.length > 0) onFolderFiles(picked)
                  e.target.value = ""
                }}
              />
            </label>
          </div>

          {needsIdentity ? (
          // The fields arrive as their own block, ruled off from the pick above
          // it, so their appearance reads as the next step rather than as the
          // modal suddenly growing.
          <div className="skill-upload-fields">
          <div className="skill-field">
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
          </div>

          <div className="skill-field">
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
          <p className="field-hint">
            Teammates see this in the skill library and the / picker, so say
            when to reach for it.
          </p>
          </div>
          </div>
          ) : file != null ? (
          <p className="skill-upload-note">
            Each skill names itself — from its own frontmatter, or its
            filename. You can edit any of them afterwards.
          </p>
          ) : null}

          {error ? (
            <p className="settings-msg settings-msg-error" role="alert">
              {error}
            </p>
          ) : null}
          </div>
          )}
        </div>
        <div className="modal-foot">
          <button type="button" className="btn btn-sm" onClick={onClose}>
            Cancel
          </button>
          {source === "github" ? (
            <button
              type="button"
              className="btn btn-sm btn-primary"
              // Gated on a selection, never on "we found some": importing is
              // always an explicit choice of which skills.
              disabled={github.selected.length === 0 || github.importing}
              onClick={onSubmit}
            >
              {github.importing
                ? "Importing…"
                : github.selected.length > 0
                  ? `Import ${github.selected.length} ${github.selected.length === 1 ? "skill" : "skills"}`
                  : "Import skills"}
            </button>
          ) : (
            <button
              type="button"
              className="btn btn-sm btn-primary"
              disabled={!canSubmit}
              onClick={onSubmit}
            >
              {submitting ? "Uploading…" : "Upload skill"}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

/** Import skills straight out of a connected repo.
 *
 *  Pure and prop-driven like the form it sits beside — the wrapper owns the
 *  connector lookups, the discovery call and the import. Three things this
 *  panel has to say plainly, because each has a consequence the user cannot
 *  see from a repo name:
 *
 *    - NOTHING IS TICKED BY DEFAULT. Every custom skill's description rides
 *      along in the router's per-company block on every ask, so importing a
 *      repo's worth of skills nobody asked for is a real cost. Selection is
 *      always deliberate.
 *    - A SKILL MAY REPLACE ONE YOU HAVE. The server already computed that
 *      against your library, so the row says so before you import, not after.
 *    - SOME SKILLS CAN'T BE IMPORTED. They are listed, disabled, with the
 *      reason — hiding them would just look like the repo has fewer skills. */
function GithubSkillPanel({
  connected,
  repos,
  reposError,
  repo,
  branch,
  folder,
  searching,
  discovery,
  selected,
  importing,
  error,
  onRepoChange,
  onBranchChange,
  onFolderChange,
  onSearch,
  onToggle,
}: GithubPanelProps) {
  if (connected === false) {
    // Not the stretch-everything panel: the connect hint is a natural-width
    // button, and a column flex parent would blow it out to full width.
    return (
      <div className="field">
        <p className="modal-sub">
          Keep your skills in the repo they belong to — connect GitHub and
          Sprntly can import every SKILL.md it finds.
        </p>
        <SourceConnectHint provider="github" />
      </div>
    )
  }
  return (
    <div className="skill-upload-panel">
      <p className="modal-sub">
        Point Sprntly at a repo and it lists every Markdown skill it finds —
        each .md file is a skill, and a folder with a SKILL.md comes in with
        its supporting files. Pick the ones you want.
      </p>

      <div className="skill-field">
        <label className="field-label" htmlFor="skill-github-repo">
          Repository
        </label>
        <select
          id="skill-github-repo"
          className="input"
          value={repo}
          onChange={(e) => onRepoChange(e.target.value)}
          disabled={connected === null || repos === null || repos.length === 0}
        >
          {connected === null || repos === null ? (
            <option value="">Loading repos…</option>
          ) : repos.length === 0 ? (
            <option value="">
              {reposError ? "Couldn’t load repos" : "No repos — install the Sprntly App"}
            </option>
          ) : (
            <>
              <option value="">Pick a repo…</option>
              {[...repos]
                .sort((a, b) => a.full_name.localeCompare(b.full_name))
                .map((r) => (
                  <option key={r.full_name} value={r.full_name}>
                    {r.full_name}
                  </option>
                ))}
            </>
          )}
        </select>
      </div>

      {/* Branch and folder are both short, both optional to think about, and
          both narrow the same lookup — one row, so the form is three steps
          deep instead of four. */}
      <div className="skill-gh-grid">
        <div className="skill-field">
          <label className="field-label" htmlFor="skill-github-branch">
            Branch
          </label>
          <input
            id="skill-github-branch"
            type="text"
            className="input"
            value={branch}
            onChange={(e) => onBranchChange(e.target.value)}
            placeholder="main"
            autoComplete="off"
          />
        </div>

        <div className="skill-field">
          <label className="field-label" htmlFor="skill-github-folder">
            Folder <span className="muted">(optional)</span>
          </label>
          <input
            id="skill-github-folder"
            type="text"
            className="input"
            value={folder}
            onChange={(e) => onFolderChange(e.target.value)}
            placeholder="e.g. skills"
            autoComplete="off"
          />
        </div>
      </div>

      <div className="skill-gh-actions">
        <button
          type="button"
          className="btn btn-sm skill-gh-find"
          disabled={!repo || searching || importing}
          onClick={onSearch}
          aria-busy={searching || undefined}
        >
          {searching ? (
            <IconLoader2 size={14} className="icon-spin" aria-hidden />
          ) : (
            <IconSearch size={14} stroke={1.8} aria-hidden />
          )}
          {searching ? "Looking…" : "Find skills"}
        </button>
      </div>

      {/* The search clears the previous results, so without this the panel
          just goes blank for the length of a tree walk. */}
      {searching ? (
        <p className="skill-gh-searching" aria-live="polite">
          Reading {repo} for Markdown skills…
        </p>
      ) : null}

      {discovery?.notes?.map((note) => (
        <p className="settings-msg settings-warning" role="status" key={note}>
          {note}
        </p>
      ))}

      {discovery && discovery.skills.length === 0 ? (
        <p className="modal-sub" role="status">
          No skills in {discovery.repo} on {discovery.ref}. A skill is a folder
          with a SKILL.md in it — try pointing at the folder yours live in.
        </p>
      ) : null}

      {discovery && discovery.skills.length > 0 ? (
        <div className="skill-gh-found">
          <div className="skill-gh-found-head">
            <span className="skill-gh-found-count">
              {discovery.skills.length}{" "}
              {discovery.skills.length === 1 ? "skill" : "skills"} found
            </span>
            <span className="muted">
              {discovery.repo} · {discovery.ref}
            </span>
          </div>
          {/* One row per skill, everything on one line: the badge is pinned to
              the row's end so it can never orphan-wrap onto a line of its own,
              and a long name ellipses instead of pushing it there. */}
          <ul className="skill-gh-list" role="group" aria-label="Skills found">
            {discovery.skills.map((s) => {
              const invalid = s.status === "invalid"
              const on = selected.includes(s.path)
              return (
                <li
                  key={s.path}
                  className={`skill-gh-item${invalid ? " is-invalid" : on ? " is-on" : ""}`}
                >
                  <label className="skill-gh-row">
                    <input
                      type="checkbox"
                      className="skill-gh-check"
                      checked={on}
                      disabled={invalid || importing}
                      onChange={() => onToggle(s.path)}
                    />
                    <span className="skill-gh-name">{s.name || s.path}</span>
                    {invalid ? null : (
                      <code className="skill-gh-trigger">{s.trigger_preview}</code>
                    )}
                    {s.status === "replaces" ? (
                      <span className="tag tag-double skill-gh-badge">replaces yours</span>
                    ) : null}
                  </label>
                  {invalid ? (
                    <p className="skill-gh-reason">Can’t import — {s.reason}</p>
                  ) : null}
                </li>
              )
            })}
          </ul>
          <p className="skill-gh-selected" aria-live="polite">
            {selected.length === 0
              ? "Nothing selected yet — tick the skills you want."
              : `${selected.length} selected.`}
          </p>
        </div>
      ) : null}

      {error ? (
        <p className="settings-msg settings-msg-error" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  )
}

/** The outcome of a MULTI-skill archive: what was added, what was updated
 *  instead of added (a name the company already used replaces that skill), and
 *  what couldn't be imported and why.
 *
 *  Every skipped folder is listed, never summarised as a count — the reason is
 *  the only thing telling the user which SKILL.md to fix. Pure and prop-driven
 *  like the form it replaces, so it renders in a markup test with no hooks. */
function MultiSkillResult({
  result,
  onClose,
}: {
  result: MultiSkillUploadResult
  onClose: () => void
}) {
  const added = result.skills.filter((s) => s.replaced !== true)
  const updated = result.skills.filter((s) => s.replaced === true)
  return (
    <div
      className="modal-overlay open"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
      aria-hidden={false}
    >
      <div className="modal modal-sm" role="dialog" aria-label="Skills imported">
        <div className="modal-head">
          <h2 className="modal-title">Skills imported</h2>
          <button type="button" className="modal-close" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>
        <div className="modal-body">
          <p className="modal-sub" role="status">
            {countLine(added.length, updated.length)} That archive held more than
            one skill, so each one was named from its own SKILL.md. You can edit
            any of them afterwards.
          </p>
          {/* Same row treatment as the GitHub picker's list, so "what you
              chose" and "what you got" read as the same kind of thing. The
              name stays a bare text node beside its trigger — one line, one
              skill. */}
          <ul className="skill-result-list">
            {result.skills.map((s) => (
              <li key={s.id} className="skill-result-row">
                {s.name} — <code className="skill-gh-trigger">{s.trigger}</code>{" "}
                {s.replaced === true ? (
                  <span className="tag tag-double skill-gh-badge">updated</span>
                ) : null}
                {s.name_conflict ? (
                  <span className="tag tag-impact skill-gh-badge">new trigger</span>
                ) : null}
              </li>
            ))}
          </ul>
          {result.skipped.length > 0 ? (
            <>
              <p className="settings-msg settings-warning" role="alert">
                {result.skipped.length === 1
                  ? "One folder wasn’t imported:"
                  : `${result.skipped.length} folders weren’t imported:`}
              </p>
              <ul className="skill-result-list skill-result-list--skipped">
                {result.skipped.map((s) => (
                  <li key={s.path || s.name} className="skill-result-row">
                    {s.name || s.path || "A skill"} — {s.reason}
                  </li>
                ))}
              </ul>
            </>
          ) : null}
        </div>
        <div className="modal-foot">
          <button type="button" className="btn btn-sm btn-primary" onClick={onClose}>
            Done
          </button>
        </div>
      </div>
    </div>
  )
}

/** "4 skills added, 1 updated." — the same sentence the screen's toast uses,
 *  so the modal and the toast never disagree about what just happened. */
export function countLine(added: number, updated: number): string {
  const parts: string[] = []
  if (added > 0) parts.push(`${added} ${added === 1 ? "skill" : "skills"} added`)
  if (updated > 0) parts.push(`${updated} updated`)
  // Both zero is only reachable when every folder was skipped, which the
  // server answers as an error — but the copy still has to say something.
  return parts.length > 0 ? `${parts.join(", ")}.` : "No skills were imported."
}

// ───────────────────── Hooks-wired wrapper ─────────────────────

type Props = {
  open: boolean
  /**
   * Performs the upload. Throws or rejects on failure — the modal catches and
   * shows the message inline, keeping the user's inputs so they can retry.
   *
   * Resolving with a MultiSkillUploadResult means the archive held several
   * skills: the modal stays open on the outcome instead of closing, because
   * the per-skill triggers and the skipped reasons have nowhere else to go.
   * Resolving with nothing is the single-skill success it always was.
   */
  onUpload: (
    file: File,
    name: string,
    description: string,
  ) => Promise<MultiSkillUploadResult | void>
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
  /**
   * Read a connected repo's skills. Absent → the GitHub source is hidden
   * entirely, so a caller that has no import path renders exactly the modal it
   * always did.
   */
  onDiscoverGithub?: (
    repo: string,
    opts: { ref?: string; path?: string },
  ) => Promise<GithubSkillDiscovery>
  /**
   * Import the ticked skills. Like onUpload, it resolves with what happened so
   * the modal can report it — the library merge and the toast belong to the
   * caller.
   */
  onImportGithub?: (req: {
    repo: string
    ref?: string
    path?: string
    paths: string[]
  }) => Promise<MultiSkillUploadResult | void>
}

export function UploadSkillModal({
  open,
  onUpload,
  onClose,
  builtinSlugs,
  customSkills,
  onDiscoverGithub,
  onImportGithub,
}: Props) {
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [file, setFile] = useState<File | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [touched, setTouched] = useState({ name: false, description: false })
  const [result, setResult] = useState<MultiSkillUploadResult | null>(null)

  // ── GitHub source ──────────────────────────────────────────────────────
  const [source, setSource] = useState<SkillSource>("file")
  const [connected, setConnected] = useState<boolean | null>(null)
  const [repos, setRepos] = useState<{ full_name: string; default_branch: string }[] | null>(null)
  const [reposError, setReposError] = useState(false)
  const [repo, setRepo] = useState("")
  const [branch, setBranch] = useState("")
  const [folder, setFolder] = useState("")
  const [searching, setSearching] = useState(false)
  const [discovery, setDiscovery] = useState<GithubSkillDiscovery | null>(null)
  const [selected, setSelected] = useState<string[]>([])

  // The connector lookups are LAZY — they run the first time the GitHub tab is
  // opened, so the file flow (which is most uploads) costs no extra requests.
  useEffect(() => {
    if (source !== "github" || connected !== null) return
    let cancelled = false
    void connectorsApi
      .list()
      .then((r) => {
        if (cancelled) return
        const github = r.connections?.find((c) => c.provider === "github")
        setConnected(getGenerateConnectorRowState(github).connected)
      })
      .catch(() => {
        if (!cancelled) setConnected(false)
      })
    return () => {
      cancelled = true
    }
  }, [source, connected])

  useEffect(() => {
    if (connected !== true || repos !== null) return
    let cancelled = false
    void connectorsApi
      .listAccessibleGithubRepos()
      .then((r) => {
        if (!cancelled) setRepos(r.repositories)
      })
      .catch(() => {
        if (cancelled) return
        setRepos([])
        setReposError(true)
      })
    return () => {
      cancelled = true
    }
  }, [connected, repos])

  function reset() {
    setName("")
    setDescription("")
    setFile(null)
    setError(null)
    setTouched({ name: false, description: false })
    setResult(null)
    setDiscovery(null)
    setSelected([])
  }

  function pickRepo(next: string) {
    setRepo(next)
    // The branch follows the repo's own default rather than assuming "main" —
    // plenty of repos still ship master, and a wrong default reads as "we
    // couldn't find any skills".
    setBranch(repos?.find((r) => r.full_name === next)?.default_branch ?? "")
    setDiscovery(null)
    setSelected([])
    setError(null)
  }

  async function handleSearch() {
    if (!repo || !onDiscoverGithub) return
    setSearching(true)
    setError(null)
    setDiscovery(null)
    setSelected([])
    try {
      setDiscovery(
        await onDiscoverGithub(repo, { ref: branch.trim(), path: folder.trim() }),
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSearching(false)
    }
  }

  async function handleImport() {
    if (!onImportGithub || selected.length === 0) return
    setSubmitting(true)
    setError(null)
    try {
      const imported = await onImportGithub({
        repo,
        ref: branch.trim(),
        path: folder.trim(),
        paths: selected,
      })
      if (imported && Array.isArray(imported.skills)) {
        setResult(imported)
        return
      }
      reset()
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSubmitting(false)
    }
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
      // Only a bare .md is named by the form. For an archive the fields are
      // hidden — send nothing, so a name typed BEFORE picking the zip can't
      // invisibly rename what's inside it.
      const isMd = file.name.toLowerCase().endsWith(".md")
      const uploaded = await onUpload(
        file,
        isMd ? name.trim() : "",
        isMd ? description.trim() : "",
      )
      if (uploaded && Array.isArray(uploaded.skills)) {
        // A multi-skill archive: hold the modal open on its outcome. The
        // library behind it is already updated — this is the report, not a
        // pending step, so the only action left is Done.
        setResult(uploaded)
        return
      }
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
      result={result}
      source={source}
      onSourceChange={(next) => {
        setSource(next)
        setError(null)
      }}
      github={{
        connected,
        repos,
        reposError,
        repo,
        branch,
        folder,
        searching,
        discovery,
        selected,
        importing: submitting,
        error,
        onRepoChange: pickRepo,
        onBranchChange: setBranch,
        onFolderChange: setFolder,
        onSearch: () => void handleSearch(),
        onToggle: (path) =>
          setSelected((prev) =>
            prev.includes(path) ? prev.filter((p) => p !== path) : [...prev, path],
          ),
      }}
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
      onFolderFiles={(picked) => {
        void (async () => {
          const packed = await zipFolderFiles(picked)
          if ("error" in packed) {
            setFile(null)
            setError(packed.error)
            return
          }
          setFile(packed.file)
          setError(null)
        })()
      }}
      onSubmit={() => void (source === "github" ? handleImport() : handleSubmit())}
      onClose={() => {
        reset()
        onClose()
      }}
    />
  )
}
