"use client"

// Formats we write in · the company's own PRD / ticket / engineering-spec forms.
//
// The governing half of /templates. Its neighbour below (TemplatesView) is the
// EXEMPLAR library — finished documents Sprntly reads for voice and depth. This
// one decides SHAPE: one active format per document type, and every new
// document of that type gets written into it. The two are deliberately
// different silhouettes — a grouped list here, a card grid there — because a PM
// landing on this screen has to know in under three seconds which of the two
// decides what their next PRD looks like, and copy alone won't carry that.
//
// Three things about this surface that are not obvious from the markup:
//
//   * THREE GROUPS ALWAYS RENDER, even with zero rows in all of them. The group
//     header is the "what governs this document right now" statement; hiding an
//     empty group hides two thirds of the feature and makes "one active per
//     type" unlearnable. The empty state is not "no data" — it is the built-in
//     being active, and it reads that way.
//
//   * THREE ACTIONS ARE ADMIN-GATED, not one: activate, deactivate, and delete
//     OF THE ROW THAT IS CURRENTLY ACTIVE. All three change the format the
//     whole company writes in; the third does it through the side door by
//     falling back to the built-in. Deleting any NON-active format is open to
//     any member. A denied non-admin gets static text, never a disabled button
//     — a disabled trash icon reads as a bug rather than as a rule.
//
//   * `orgRole` IS NULL UNTIL THE WORKSPACES LIST LANDS (WorkspaceContext:72).
//     While null the gated controls are disabled + aria-busy with NO denial
//     text. Flashing "Only an admin can…" at an admin for 200ms is worse than a
//     brief disabled button.
//
// The view layer (ArtifactFormatsView) is pure and prop-driven so every state
// above renders in a markup test with no API and no hooks; ArtifactFormatsSection
// owns state, the API calls and the confirms.

import { useCallback, useMemo, useRef, useState } from "react"
import {
  IconAlertCircle,
  IconAlertTriangle,
  IconCircleCheck,
  IconLayoutList,
  IconLoader2,
  IconUpload,
} from "@tabler/icons-react"
import { ConfirmDialog } from "../../shared/ConfirmDialog"
import {
  FORMAT_CAP_ERROR,
  MAX_FORMAT_SOURCE_CHARS,
  UploadFormatModal,
  formatFileError,
  type UploadFormatRequest,
} from "../../shared/UploadFormatModal"
import { FormatPreviewModal } from "../../shared/FormatPreviewModal"
import { useNavigation } from "../../../context/NavigationContext"
import { useWorkspace } from "../../../context/WorkspaceContext"
import {
  ApiError,
  artifactTemplatesApi,
  type ArtifactTemplate,
  type ArtifactTemplateType,
  type CompileStatus,
} from "../../../lib/api"
import {
  ARTIFACT_TYPE_DOC,
  ARTIFACT_TYPE_IDS,
  ARTIFACT_TYPE_LABELS,
  ARTIFACT_TYPE_NOUN,
  ARTIFACT_TYPE_PLURAL,
  activationRefusal,
  addFormatLabel,
  builtinFormatName,
  notWiredNote,
  notWiredShort,
  recompilingActiveNote,
  translateCompileNote,
} from "../../../lib/compileNotes"
import {
  useArtifactTemplates,
  type ArtifactTemplateGroups,
} from "../../../lib/useArtifactTemplates"

/** The denial lines. Two, not one — activate/deactivate and deleting the active
 *  row are different actions and saying the same thing for both would leave a
 *  member wondering why the trash is refusing them. */
export const DENY_CHANGE = "Only an admin can change your team's format."
export const DENY_DELETE_ACTIVE =
  "Only an admin can delete the format your team is using."

/** What a `needs_review` row offers instead of Activate. Names the whole loop —
 *  look, fix, re-check — because "can't activate" on its own is a dead end. */
export const NEEDS_REVIEW_NEXT =
  "Preview it to see what we could map, fix the gap in your format, then " +
  "check again."

/** Badge word + icon per status. Every badge carries a WORD; colour alone is
 *  not a status. */
const BADGE: Record<CompileStatus, { label: string; className: string }> = {
  pending: { label: "Queued", className: "afmt-badge--pending" },
  compiling: { label: "Checking…", className: "afmt-badge--compiling" },
  ready: { label: "Ready", className: "afmt-badge--ready" },
  needs_review: { label: "Needs a look", className: "afmt-badge--review" },
  failed: { label: "Couldn't be read", className: "afmt-badge--failed" },
}

function BadgeIcon({ status }: { status: CompileStatus }) {
  switch (status) {
    case "compiling":
      return <IconLoader2 size={12} className="icon-spin" aria-hidden />
    case "ready":
      return <IconCircleCheck size={12} aria-hidden />
    case "needs_review":
      return <IconAlertTriangle size={12} aria-hidden />
    case "failed":
      return <IconAlertCircle size={12} aria-hidden />
    default:
      return null
  }
}

const IN_FLIGHT_STATUSES = new Set<CompileStatus>(["pending", "compiling"])

/** "3 Aug 2026", or null when the value can't be read as a date. The caller
 *  turns null into a labelled honest string — never a dropped line. */
export function fmtFormatDate(iso: string | null): string | null {
  if (!iso) return null
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return null
  return d.toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  })
}

/**
 * The row's metadata line, as three labelled parts.
 *
 * HOUSE RULE: every part renders even when its value is blank. A missing
 * uploader is not a reason to drop "Uploaded by" — it is a reason to say what
 * we actually know. A bare dash would be worse than either: it looks like a
 * rendering bug and tells the reader nothing about whether the value is unknown
 * or simply empty. Pure → the "blank uploader, null date" case is a unit test.
 */
export function metaParts(row: ArtifactTemplate): string[] {
  const uploader = (row.uploader_name ?? "").trim()
  const added = fmtFormatDate(row.created_at)
  const chars = row.source_chars
  return [
    uploader
      ? `Uploaded by ${uploader}`
      : "Uploaded by someone no longer on the team",
    added ? `Added ${added}` : "Added — date not recorded",
    typeof chars === "number" && Number.isFinite(chars) && chars > 0
      ? `${chars.toLocaleString()} characters of Markdown`
      : "Length not recorded",
  ]
}

/**
 * The reason line — rendered in ALL FIVE states, never conditionally.
 *
 * The badge says WHAT; this says WHY, and it is what makes a non-ready badge
 * actionable without opening anything. It is also the only place a compile note
 * reaches a person, and it goes through `translateCompileNote` to get there:
 * the backend's own wording names CSS classes, and `ul.ev` on a screen reads as
 * a crash.
 *
 * ONE state overrides the compile status: the ACTIVE format being re-checked.
 * That row shows an "Active — in use now" pill and a "Checking…" badge at the
 * same time, and read together those say "nobody knows what my next PRD will
 * look like". They can both be true — `resolve_prd_template` gates on
 * `compiled != ''`, so the last good skeleton keeps serving throughout — and
 * this line is the only thing on the surface that says so. It replaces the
 * generic checking copy rather than joining it, because the generic line
 * answers a question ("what are we doing?") nobody is asking while their
 * company-wide format is in flight.
 */
export function reasonLine(row: ArtifactTemplate, stalled: boolean): string {
  const inFlight =
    row.compile_status === "pending" || row.compile_status === "compiling"
  if (row.is_active && inFlight) {
    // Deliberately NOT varied by `stalled`. A slow check does not change what
    // is being served, and the "Check again" control still appears beside it —
    // so the affordance survives without inventing a second sentence.
    return recompilingActiveNote(row.artifact_type)
  }
  switch (row.compile_status) {
    case "pending":
      return stalled
        ? "Still checking — this is taking longer than usual."
        : "Queued — we'll check this format in a moment."
    case "compiling":
      return stalled
        ? "Still checking — this is taking longer than usual."
        : "Checking your format against what a Sprntly document needs…"
    case "ready":
      return "Checked — every part of a Sprntly document has a home in this format."
    case "needs_review":
    case "failed":
      return translateCompileNote(row.compile_summary)
    default:
      return "Checked — every part of a Sprntly document has a home in this format."
  }
}

export type ArtifactFormatsViewProps = {
  /** Rows by type, active-first then newest-first. ALWAYS all three keys, even
   *  when a group is empty — the group header is the "what governs this
   *  document right now" statement and must render regardless. */
  groups: ArtifactTemplateGroups
  /** First load only. Later refreshes use `refreshing` so rows never blank. */
  loading: boolean
  refreshing: boolean
  error: string | null
  /** Company role. NULL WHILE UNKNOWN — must not default to "member" or an
   *  admin sees the denial line for a beat. Gates THREE controls, not one:
   *  activate, deactivate, and delete OF THE ACTIVE ROW. Delete of a non-active
   *  row is open to any member. */
  orgRole: string | null
  /** Types whose GENERATION honours a custom format today, from the list
   *  response's top-level `generation_enabled`. A type outside the set still
   *  uploads and previews; its Activate slot carries the note. Must be readable
   *  with ZERO rows — that's the state most companies live in. */
  liveTypes: ReadonlySet<ArtifactTemplateType>
  pollingIds: ReadonlySet<string>
  stalledIds: ReadonlySet<string>
  connectionLost: boolean
  activatingId: string | null
  deletingId: string | null
  recompilingId: string | null
  announcement: string | null
  onAddFormat: (type: ArtifactTemplateType) => void
  onPreview: (id: string) => void
  onActivateRequest: (id: string) => void
  onDeactivateRequest: (id: string) => void
  onDeleteRequest: (id: string) => void
  onRenameRequest: (id: string) => void
  onRecompile: (id: string) => void
  onRetryLoad: () => void
  // ── rename + replace (not in the spec's prop block; the spec names
  // `onRenameRequest` but no rename surface, and a `failed` row's "Replace the
  // file" action needs a picker. Both are inline on the row rather than another
  // modal — the row is where the mistake is visible.) ──
  renamingId: string | null
  renameValue: string
  renameSaving: boolean
  onRenameChange: (v: string) => void
  onRenameSave: () => void
  onRenameCancel: () => void
  onReplaceFile: (id: string, file: File) => void
}

export function ArtifactFormatsView({
  groups,
  loading,
  refreshing,
  error,
  orgRole,
  liveTypes,
  pollingIds,
  stalledIds,
  connectionLost,
  activatingId,
  deletingId,
  recompilingId,
  announcement,
  onAddFormat,
  onPreview,
  onActivateRequest,
  onDeactivateRequest,
  onDeleteRequest,
  onRenameRequest,
  onRecompile,
  onRetryLoad,
  renamingId,
  renameValue,
  renameSaving,
  onRenameChange,
  onRenameSave,
  onRenameCancel,
  onReplaceFile,
}: ArtifactFormatsViewProps) {
  const isAdmin = orgRole === "owner" || orgRole === "admin"
  // Distinct from `!isAdmin`: until the workspaces list lands we know nothing,
  // and guessing "member" flashes a denial at an admin.
  const roleKnown = orgRole !== null
  const allEmpty = ARTIFACT_TYPE_IDS.every((t) => (groups[t] ?? []).length === 0)

  /** The activate/deactivate slot for one row. Returns a control, a reason, or
   *  a disabled placeholder — never a disabled button with no explanation and
   *  never a button a click would 403. */
  function activationSlot(row: ArtifactTemplate, type: ArtifactTemplateType) {
    if (row.is_active) {
      if (!roleKnown) {
        return (
          <button
            type="button"
            className="btn btn-sm afmt-act"
            disabled
            aria-busy="true"
          >
            Use Sprntly&apos;s built-in format instead
          </button>
        )
      }
      if (!isAdmin) {
        return (
          <span className="afmt-denied" role="note">
            {DENY_CHANGE}
          </span>
        )
      }
      return (
        <button
          type="button"
          id={`afmt-deactivate-${row.id}`}
          className="btn btn-sm afmt-act"
          onClick={() => onDeactivateRequest(row.id)}
          disabled={activatingId === row.id}
        >
          Use Sprntly&apos;s built-in format instead
        </button>
      )
    }
    // Only a clean compile can be activated — a needs_review format turns
    // downstream features off silently, so the slot carries the loop out of it.
    if (row.compile_status === "needs_review") {
      return (
        <span className="afmt-denied" role="note">
          {NEEDS_REVIEW_NEXT}
        </span>
      )
    }
    if (row.compile_status !== "ready") return null
    if (!liveTypes.has(type)) {
      // The FACT only. The group header directly above carries the same
      // sentence plus what to do about it, and repeating a two-sentence
      // instruction on every row of a group turns the note into wallpaper. An
      // empty slot here would read as merely broken, so the fact stays.
      return (
        <span className="afmt-denied" role="note">
          {notWiredShort(type)}
        </span>
      )
    }
    if (!roleKnown) {
      return (
        <button
          type="button"
          className="btn btn-sm btn-primary afmt-act"
          disabled
          aria-busy="true"
        >
          Use this format
        </button>
      )
    }
    if (!isAdmin) {
      return (
        <span className="afmt-denied" role="note">
          {DENY_CHANGE}
        </span>
      )
    }
    return (
      <button
        type="button"
        id={`afmt-activate-${row.id}`}
        className="btn btn-sm btn-primary afmt-act"
        onClick={() => onActivateRequest(row.id)}
        disabled={activatingId === row.id}
      >
        {activatingId === row.id ? "Switching…" : "Use this format"}
      </button>
    )
  }

  /** Delete, gated only when the row is the ACTIVE one (§7). */
  function deleteSlot(row: ArtifactTemplate) {
    if (row.is_active && !roleKnown) {
      return (
        <button
          type="button"
          className="btn btn-sm afmt-act afmt-act-del"
          disabled
          aria-busy="true"
        >
          Delete
        </button>
      )
    }
    if (row.is_active && !isAdmin) {
      return (
        <span className="afmt-denied" role="note">
          {DENY_DELETE_ACTIVE}
        </span>
      )
    }
    return (
      <button
        type="button"
        id={`afmt-delete-${row.id}`}
        className="btn btn-sm afmt-act afmt-act-del"
        onClick={() => onDeleteRequest(row.id)}
        disabled={deletingId === row.id}
      >
        {deletingId === row.id ? "Deleting…" : "Delete"}
      </button>
    )
  }

  function renderRow(row: ArtifactTemplate, type: ArtifactTemplateType) {
    const inFlight = IN_FLIGHT_STATUSES.has(row.compile_status)
    const stalled = stalledIds.has(row.id)
    const badge = BADGE[row.compile_status] ?? BADGE.pending
    // EITHER signal marks the row busy. The status is the row's own truth and
    // `pollingIds` is the hook's view of what it is watching; requiring both
    // would drop `aria-busy` in the window between an optimistic insert and the
    // poll's first read, which is exactly when a screen reader should hear it.
    const busy = inFlight || pollingIds.has(row.id)
    const notes = row.compile_note_count ?? 0
    const showAll =
      (row.compile_status === "needs_review" || row.compile_status === "failed") &&
      notes > 1
    return (
      <li
        key={row.id}
        className={`afmt-row${row.is_active ? " is-active" : ""}`}
        aria-current={row.is_active ? "true" : undefined}
        aria-busy={busy ? "true" : undefined}
      >
        <div className="afmt-row-main">
          <div className="afmt-row-head">
            {/* `title` carries the whole name: two similar formats truncated to
                where they're indistinguishable is a governing choice made
                blind, so the name wraps rather than ellipsing away. */}
            <span className="afmt-name" title={row.name}>
              {row.name}
            </span>
            {row.is_active ? (
              // Not "Active" alone — the three extra words stop it reading as
              // one more status beside the badge.
              <span className="afmt-active-pill">Active — in use now</span>
            ) : null}
            <span className={`afmt-badge ${badge.className}`}>
              <BadgeIcon status={row.compile_status} />
              {badge.label}
            </span>
          </div>

          <p className="afmt-meta">{metaParts(row).join(" · ")}</p>

          <p className="afmt-reason">
            {reasonLine(row, stalled)}
            {showAll ? (
              <>
                {" "}
                <button
                  type="button"
                  className="afmt-link"
                  onClick={() => onPreview(row.id)}
                >
                  See all {notes}
                </button>
              </>
            ) : null}
          </p>

          {renamingId === row.id ? (
            <div className="afmt-rename">
              <label className="sr-only" htmlFor={`afmt-rename-input-${row.id}`}>
                Rename {row.name}
              </label>
              <input
                id={`afmt-rename-input-${row.id}`}
                type="text"
                className="input"
                value={renameValue}
                maxLength={120}
                autoFocus
                disabled={renameSaving}
                onChange={(e) => onRenameChange(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault()
                    onRenameSave()
                  } else if (e.key === "Escape") {
                    e.preventDefault()
                    e.stopPropagation()
                    onRenameCancel()
                  }
                }}
              />
              <button
                type="button"
                className="btn btn-sm btn-primary"
                onClick={onRenameSave}
                disabled={renameSaving || renameValue.trim().length === 0}
              >
                {renameSaving ? "Saving…" : "Save"}
              </button>
              <button
                type="button"
                className="btn btn-sm"
                onClick={onRenameCancel}
                disabled={renameSaving}
              >
                Cancel
              </button>
            </div>
          ) : null}
        </div>

        {/* Row actions are always in the DOM, never hover-revealed: on a card
            grid the card itself is the primary action, but here every one of
            these is primary and a keyboard user must reach all of them. */}
        <div className="afmt-row-actions">
          {row.compile_status !== "pending" && row.compile_status !== "compiling" ? (
            <button
              type="button"
              id={`afmt-preview-${row.id}`}
              className="btn btn-sm afmt-act"
              onClick={() => onPreview(row.id)}
            >
              Preview
            </button>
          ) : null}

          {activationSlot(row, type)}

          {row.compile_status === "needs_review" ||
          row.compile_status === "failed" ||
          stalled ? (
            <button
              type="button"
              id={`afmt-recompile-${row.id}`}
              className="btn btn-sm afmt-act"
              onClick={() => onRecompile(row.id)}
              disabled={recompilingId === row.id}
            >
              {recompilingId === row.id
                ? "Checking…"
                : row.compile_status === "failed"
                  ? "Try again"
                  : "Check again"}
            </button>
          ) : null}

          {row.compile_status === "failed" ? (
            <label className="btn btn-sm afmt-act afmt-replace">
              Replace the file
              <input
                type="file"
                accept=".md,.markdown"
                className="afmt-replace-input"
                aria-label={`Replace the file behind ${row.name}`}
                onChange={(e) => {
                  const picked = e.target.files?.[0]
                  e.target.value = ""
                  if (picked) onReplaceFile(row.id, picked)
                }}
              />
            </label>
          ) : null}

          {row.compile_status !== "compiling" && renamingId !== row.id ? (
            <button
              type="button"
              id={`afmt-rename-${row.id}`}
              className="btn btn-sm afmt-act"
              onClick={() => onRenameRequest(row.id)}
            >
              Rename
            </button>
          ) : null}

          {deleteSlot(row)}
        </div>
      </li>
    )
  }

  return (
    <section
      className="afmt"
      aria-labelledby="afmt-title"
      aria-busy={refreshing ? "true" : undefined}
    >
      <div className="afmt-top">
        <h2 className="afmt-title" id="afmt-title">
          <IconLayoutList size={16} className="afmt-title-icon" aria-hidden />
          Formats we write in
          <span className="afmt-sub">
            Your team&apos;s own PRD, ticket and engineering-spec structure. One
            format is active per document type — every new document Sprntly
            writes follows it.
          </span>
        </h2>
        <button
          type="button"
          className="btn btn-primary afmt-upload"
          onClick={() => onAddFormat("prd")}
        >
          <IconUpload size={14} aria-hidden />
          Upload a format
        </button>
      </div>

      <div className="afmt-body">
        {/* Tinted --info, deliberately NOT the accent green of .tpl-intro below
            — two identically-tinted stacked banners would undo the very
            distinction this section exists to make. */}
        <div className="afmt-intro">
          <IconLayoutList size={16} className="afmt-intro-icon" aria-hidden />
          <span>
            <strong>Skills decide what a document says. Formats decide how
            it&apos;s laid out</strong> — your headings, your order, your
            wording. Upload a Markdown copy of the format your team already
            uses, and Sprntly writes into it.
          </span>
        </div>

        {/* ONE live region for the whole section. Per-row would produce N of
            them and a wall of noise; this announces transitions only. */}
        <p className="sr-only" role="status" aria-live="polite">
          {announcement ?? ""}
        </p>

        {error ? (
          <div className="afmt-msg afmt-msg-error" role="alert">
            {error}{" "}
            <button type="button" className="afmt-link" onClick={onRetryLoad}>
              Try again
            </button>
          </div>
        ) : null}

        {connectionLost ? (
          <p className="afmt-msg afmt-msg-info" role="status">
            We&apos;ve lost the connection — we&apos;ll pick this up when
            you&apos;re back.
          </p>
        ) : null}

        {/* The empty state is not "no data" — it is the built-in being active,
            and that framing is what makes the default feel chosen. The three
            group rows still render below it, each naming what is in use, so the
            screen never reads as broken or unconfigured. */}
        {allEmpty && !loading && !error ? (
          <div className="afmt-empty">
            <h3 className="afmt-empty-t">
              Sprntly writes in its own format — until you give it yours.
            </h3>
            <p className="afmt-empty-d">
              Upload the PRD, ticket or engineering-spec format your team
              already uses, and Sprntly writes into that shape instead. Same
              rigour, your headings, your order, your wording.
            </p>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => onAddFormat("prd")}
            >
              Upload a format
            </button>
          </div>
        ) : null}

        {ARTIFACT_TYPE_IDS.map((type) => {
          const rows = groups[type] ?? []
          const active = rows.find((r) => r.is_active) ?? null
          return (
            <section
              key={type}
              className="afmt-group"
              aria-labelledby={`afmt-group-${type}`}
            >
              <div className="afmt-group-head">
                <h3 className="afmt-group-title" id={`afmt-group-${type}`}>
                  {ARTIFACT_TYPE_LABELS[type]}
                </h3>
                {/* The most important string on the screen. NEVER claim the
                    built-in before the list lands — a wrong answer here is a
                    wrong answer about what the company's next document will
                    look like. */}
                {loading ? (
                  <span className="afmt-using" role="status">
                    Checking which format is in use…
                  </span>
                ) : active ? (
                  <span className="afmt-using">
                    Now using: <strong>{active.name}</strong>{" "}
                    <span className="tag tag-double">yours</span>
                  </span>
                ) : (
                  <span className="afmt-using">
                    Now using: {builtinFormatName(type)}{" "}
                    <span className="tag tag-impact">built-in</span>
                  </span>
                )}
                <button
                  type="button"
                  className="btn btn-sm afmt-group-add"
                  onClick={() => onAddFormat(type)}
                >
                  {addFormatLabel(type)}
                </button>
              </div>

              {!liveTypes.has(type) ? (
                <p className="afmt-note" role="note">
                  {notWiredNote(type)}
                </p>
              ) : null}

              {loading ? (
                <ul className="afmt-list" aria-hidden>
                  <li className="afmt-row afmt-row--skel" />
                  <li className="afmt-row afmt-row--skel" />
                </ul>
              ) : rows.length === 0 ? (
                <p className="afmt-group-empty">
                  No {ARTIFACT_TYPE_NOUN[type]} format uploaded — Sprntly uses
                  its own.
                </p>
              ) : (
                <ul className="afmt-list">
                  {rows.map((row) => renderRow(row, type))}
                </ul>
              )}
            </section>
          )
        })}
      </div>
    </section>
  )
}

// ───────────────────── Hooks-wired wrapper ─────────────────────

type Confirm =
  | { kind: "activate"; id: string }
  | { kind: "deactivate"; id: string }
  | { kind: "delete"; id: string }

/** File text via FileReader — `File.text()` is missing in jsdom, the same
 *  reason UploadSkillModal reads its .md this way. */
function readFileText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result ?? ""))
    reader.onerror = () =>
      reject(reader.error ?? new Error("Could not read the file."))
    reader.readAsText(file)
  })
}

export function ArtifactFormatsSection() {
  const { showToast } = useNavigation()
  const { orgRole, workspace } = useWorkspace()
  const companyName = workspace?.display_name?.trim() || "your company"

  const {
    groups,
    loading,
    refreshing,
    error,
    liveTypes,
    pollingIds,
    stalledIds,
    connectionLost,
    announcement,
    refresh,
    retry,
    upsert,
    applyActivation,
    drop,
  } = useArtifactTemplates({
    onReady: (row) => {
      // Third of the three ways a user learns a compile finished (badge, live
      // region, toast) — they may well have navigated away from the section.
      showToast(
        `${row.name} is ready`,
        `Preview it to see what Sprntly will produce, then make it your ` +
          `${ARTIFACT_TYPE_DOC[row.artifact_type] ?? "document"} format.`,
      )
    },
  })

  const [uploadOpen, setUploadOpen] = useState(false)
  const [uploadType, setUploadType] = useState<ArtifactTemplateType>("prd")
  const [previewId, setPreviewId] = useState<string | null>(null)
  const [confirm, setConfirm] = useState<Confirm | null>(null)
  const [activatingId, setActivatingId] = useState<string | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [recompilingId, setRecompilingId] = useState<string | null>(null)
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [renameValue, setRenameValue] = useState("")
  const [renameSaving, setRenameSaving] = useState(false)

  // ConfirmDialog focuses its confirm button on open but does NOT restore focus
  // on close, so the section remembers which row control opened it and puts
  // focus back there. Row action buttons carry stable ids for exactly this.
  const invokerRef = useRef<string | null>(null)

  const allRows = useMemo(
    () => ARTIFACT_TYPE_IDS.flatMap((t) => groups[t] ?? []),
    [groups],
  )
  const rowById = useCallback(
    (id: string | null) => (id ? allRows.find((r) => r.id === id) ?? null : null),
    [allRows],
  )

  function restoreFocus() {
    const id = invokerRef.current
    invokerRef.current = null
    if (!id) return
    // Deferred: the dialog is still mounted on this tick, and focusing under it
    // would be undone by its own teardown.
    window.setTimeout(() => document.getElementById(id)?.focus(), 0)
  }

  /**
   * One error ladder for every row action.
   *
   * 404 → the row is gone; drop it and say so. No existence disclosure, ever.
   * 403 → a role refusal, and the two cases say different things.
   * 409 → activation refused. `apiErrorMessage` (lib/api.ts:27) cannot render
   *       this one: it handles a string or a validation-list `detail`, and
   *       activate's detail is an OBJECT, so `err.message` degrades to "Request
   *       failed (409)". Read `body.detail.{code,notes}` instead, translate the
   *       note, and re-read the row so the badge stops lying.
   */
  const handleActionError = useCallback(
    (e: unknown, id: string, action: "change" | "delete-active" | "other") => {
      if (e instanceof ApiError) {
        if (e.status === 404) {
          drop(id)
          showToast(
            "That format isn't here anymore.",
            "Someone on your team removed it.",
          )
          return
        }
        if (e.status === 403) {
          showToast(
            action === "delete-active" ? DENY_DELETE_ACTIVE : DENY_CHANGE,
            "Ask an owner or an admin on your team to make the change.",
          )
          return
        }
        if (e.status === 409) {
          const { title, reason, refetch } = activationRefusal(e.body)
          showToast(title, reason)
          if (refetch) void refresh()
          return
        }
      }
      showToast(
        "That didn't work",
        e instanceof Error && e.message ? e.message : "Please try again.",
      )
    },
    [drop, refresh, showToast],
  )

  async function onUploadSubmit(req: UploadFormatRequest) {
    const created =
      req.source === "paste"
        ? await artifactTemplatesApi.create({
            name: req.name,
            artifact_type: req.type,
            source_md: req.markdown,
          })
        : await artifactTemplatesApi.upload(req.file!, req.type, req.name)
    // The row appears in its group at `pending` and the poll starts — the user
    // watches the row, never a spinner standing in for the list.
    upsert(created)
    showToast(
      "Format added",
      `We're checking “${created.name}” against what a Sprntly ` +
        `${ARTIFACT_TYPE_DOC[req.type]} needs. This usually takes under a minute.`,
    )
  }

  async function doActivate(id: string) {
    setActivatingId(id)
    try {
      const updated = await artifactTemplatesApi.activate(id)
      // Patched, not optimistically guessed: only a 200 gets here.
      applyActivation(updated)
      void refresh()
      const doc = ARTIFACT_TYPE_DOC[updated.artifact_type] ?? "document"
      showToast(
        `${updated.name} is now your ${doc} format`,
        `The next ${doc} Sprntly writes will use it. Documents already ` +
          `written are unchanged.`,
      )
    } catch (e) {
      handleActionError(e, id, "change")
    } finally {
      setActivatingId(null)
    }
  }

  async function doDeactivate(id: string) {
    const row = rowById(id)
    setActivatingId(id)
    try {
      await artifactTemplatesApi.deactivate(id)
      void refresh()
      const type = row?.artifact_type ?? "prd"
      showToast(
        `Back to Sprntly's built-in ${ARTIFACT_TYPE_DOC[type]} format`,
        `New ${ARTIFACT_TYPE_PLURAL[type]} will use Sprntly's own structure ` +
          `again. Documents already written are unchanged.`,
      )
    } catch (e) {
      handleActionError(e, id, "change")
    } finally {
      setActivatingId(null)
    }
  }

  async function doDelete(id: string) {
    const row = rowById(id)
    const wasActive = row?.is_active === true
    setDeletingId(id)
    try {
      const result = await artifactTemplatesApi.remove(id)
      // The row leaves only after the API confirms; a failure keeps it and
      // surfaces the reason (matching SkillsScreen.onDeleteConfirm).
      drop(id)
      const type = row?.artifact_type ?? result.artifact_type ?? "prd"
      showToast(
        "Format deleted",
        result.fell_back_to_builtin
          ? `${ARTIFACT_TYPE_PLURAL[type]} are back to Sprntly's built-in format.`
          : `“${row?.name ?? "That format"}” was removed for everyone at ${companyName}.`,
      )
    } catch (e) {
      handleActionError(e, id, wasActive ? "delete-active" : "other")
    } finally {
      setDeletingId(null)
    }
  }

  async function doRecompile(id: string) {
    setRecompilingId(id)
    try {
      await artifactTemplatesApi.compile(id)
      // The compile answers with the row's new status; the refresh restarts the
      // poll from whatever that is.
      await refresh()
    } catch (e) {
      handleActionError(e, id, "other")
    } finally {
      setRecompilingId(null)
    }
  }

  async function doRename() {
    const id = renamingId
    if (!id) return
    const next = renameValue.trim()
    if (!next) return
    setRenameSaving(true)
    try {
      const updated = await artifactTemplatesApi.update(id, { name: next })
      upsert(updated)
      setRenamingId(null)
      setRenameValue("")
    } catch (e) {
      handleActionError(e, id, "other")
    } finally {
      setRenameSaving(false)
    }
  }

  async function doReplaceFile(id: string, file: File) {
    const fileError = formatFileError(file)
    if (fileError) {
      showToast("That file can't be used", fileError)
      return
    }
    let text: string
    try {
      text = await readFileText(file)
    } catch {
      showToast("That file can't be used", "We couldn't read that file.")
      return
    }
    if (text.length > MAX_FORMAT_SOURCE_CHARS) {
      showToast("That file is too long", FORMAT_CAP_ERROR)
      return
    }
    setRecompilingId(id)
    try {
      const updated = await artifactTemplatesApi.update(id, { source_md: text })
      // Replacing the source re-queues the check, so the upsert restarts the
      // poll off the row's new `pending`.
      upsert(updated)
      showToast(
        "Format replaced",
        `We're checking “${updated.name}” again. This usually takes under a minute.`,
      )
    } catch (e) {
      handleActionError(e, id, "other")
    } finally {
      setRecompilingId(null)
    }
  }

  // ── confirm dialog copy ──────────────────────────────────────────────────
  const confirmRow = rowById(confirm?.id ?? null)
  const confirmType = confirmRow?.artifact_type ?? "prd"
  const confirmDoc = ARTIFACT_TYPE_DOC[confirmType]
  const replacing =
    confirm?.kind === "activate"
      ? (groups[confirmType] ?? []).find(
          (r) => r.is_active && r.id !== confirm.id,
        ) ?? null
      : null
  const confirmBusy =
    confirm?.kind === "delete"
      ? deletingId === confirm.id
      : confirm != null && activatingId === confirm.id

  let confirmProps: {
    title: string
    body: string
    confirmLabel: string
    busyLabel: string
    tone: "default" | "danger"
  } | null = null
  if (confirm?.kind === "activate" && confirmRow) {
    confirmProps = {
      // tone="default", NOT danger: this isn't destructive, and painting
      // routine setup red trains people to ignore red.
      tone: "default",
      title: `Write every ${confirmDoc} in “${confirmRow.name}”?`,
      body:
        `From now on, every ${confirmDoc} Sprntly writes for ${companyName} ` +
        `uses this format — in every workspace, for everyone on the team. ` +
        `Documents already written keep the format they were written in. You ` +
        `can switch back at any time.` +
        (replacing ? ` This replaces “${replacing.name}”, which stays in your library.` : ""),
      confirmLabel: "Use this format",
      busyLabel: "Switching…",
    }
  } else if (confirm?.kind === "deactivate" && confirmRow) {
    confirmProps = {
      tone: "default",
      title: `Go back to Sprntly's built-in ${confirmDoc} format?`,
      body:
        `Your format stays in your library. New ${ARTIFACT_TYPE_PLURAL[confirmType]} ` +
        `will use Sprntly's own structure again. Documents already written are ` +
        `unchanged.`,
      confirmLabel: "Use the built-in format",
      busyLabel: "Switching…",
    }
  } else if (confirm?.kind === "delete" && confirmRow) {
    confirmProps = {
      tone: "danger",
      title: confirmRow.is_active
        ? `Delete “${confirmRow.name}” — the format you're using?`
        : `Delete “${confirmRow.name}”?`,
      body: confirmRow.is_active
        ? `It's removed for everyone at ${companyName}, and new ` +
          `${ARTIFACT_TYPE_PLURAL[confirmType]} go back to Sprntly's built-in ` +
          `format. Documents already written with it are unchanged. This can't ` +
          `be undone.`
        : `It's removed for everyone at ${companyName}. Documents already ` +
          `written with it are unchanged. This can't be undone.`,
      confirmLabel: "Delete format",
      busyLabel: "Deleting…",
    }
  }

  function runConfirm() {
    if (!confirm) return
    const { kind, id } = confirm
    setConfirm(null)
    restoreFocus()
    if (kind === "activate") void doActivate(id)
    else if (kind === "deactivate") void doDeactivate(id)
    else void doDelete(id)
  }

  const previewRow = rowById(previewId)
  const previewType = previewRow?.artifact_type ?? "prd"
  const isAdmin = orgRole === "owner" || orgRole === "admin"
  const previewCanActivate =
    previewRow != null &&
    previewRow.compile_status === "ready" &&
    !previewRow.is_active &&
    isAdmin &&
    liveTypes.has(previewType)
  const previewBlockedReason =
    previewRow == null || previewCanActivate
      ? null
      : previewRow.is_active
        ? null
        : !liveTypes.has(previewType)
          ? notWiredNote(previewType)
          : previewRow.compile_status === "needs_review"
            ? NEEDS_REVIEW_NEXT
            : previewRow.compile_status !== "ready"
              ? null
              : orgRole === null
                ? null
                : DENY_CHANGE

  return (
    <>
      <ArtifactFormatsView
        groups={groups}
        loading={loading}
        refreshing={refreshing}
        error={error}
        orgRole={orgRole}
        liveTypes={liveTypes}
        pollingIds={pollingIds}
        stalledIds={stalledIds}
        connectionLost={connectionLost}
        activatingId={activatingId}
        deletingId={deletingId}
        recompilingId={recompilingId}
        announcement={announcement}
        onAddFormat={(type) => {
          setUploadType(type)
          setUploadOpen(true)
        }}
        onPreview={(id) => setPreviewId(id)}
        onActivateRequest={(id) => {
          invokerRef.current = `afmt-activate-${id}`
          setConfirm({ kind: "activate", id })
        }}
        onDeactivateRequest={(id) => {
          invokerRef.current = `afmt-deactivate-${id}`
          setConfirm({ kind: "deactivate", id })
        }}
        onDeleteRequest={(id) => {
          invokerRef.current = `afmt-delete-${id}`
          setConfirm({ kind: "delete", id })
        }}
        onRenameRequest={(id) => {
          setRenamingId(id)
          setRenameValue(rowById(id)?.name ?? "")
        }}
        onRecompile={(id) => void doRecompile(id)}
        onRetryLoad={retry}
        renamingId={renamingId}
        renameValue={renameValue}
        renameSaving={renameSaving}
        onRenameChange={setRenameValue}
        onRenameSave={() => void doRename()}
        onRenameCancel={() => {
          setRenamingId(null)
          setRenameValue("")
        }}
        onReplaceFile={(id, file) => void doReplaceFile(id, file)}
      />

      <UploadFormatModal
        open={uploadOpen}
        initialType={uploadType}
        existing={allRows.map((r) => ({
          name: r.name,
          artifact_type: r.artifact_type,
        }))}
        onSubmit={onUploadSubmit}
        onClose={() => setUploadOpen(false)}
      />

      <FormatPreviewModal
        templateId={previewId}
        name={previewRow?.name ?? ""}
        canActivate={previewCanActivate}
        activateBlockedReason={previewBlockedReason}
        activating={activatingId != null && activatingId === previewId}
        onActivate={() => {
          if (!previewId) return
          const id = previewId
          setPreviewId(null)
          setConfirm({ kind: "activate", id })
        }}
        onNotFound={drop}
        onClose={() => setPreviewId(null)}
      />

      {confirmProps ? (
        <ConfirmDialog
          open
          tone={confirmProps.tone}
          title={confirmProps.title}
          body={confirmProps.body}
          confirmLabel={confirmProps.confirmLabel}
          busyLabel={confirmProps.busyLabel}
          busy={confirmBusy}
          onConfirm={runConfirm}
          onCancel={() => {
            setConfirm(null)
            restoreFocus()
          }}
        />
      ) : null}
    </>
  )
}
