"use client"

// ── CreateProjectModal — "New project" (design-spec §2 screen 5, build spec
// §6 CreateProjectModal row) ──
//
// Three tabs (AC1): **Start manual** (name + optional invite rows), **From
// an artifact** (pick an existing artifact as the project's first item —
// AC3, a PRD is one selectable artifact among the five types, never the
// project's identity), and **Auto · from PRD** (AD-P9) — fork an existing
// PRD into its own project: pick one of the caller's PRDs, create with
// `origin: "prd_auto"`, then add it as the project's first artifact — the
// exact two-call shape the "From an artifact" tab already uses. This is the
// explicit "fork a project from a PRD I already have" entry point; the
// server-side hook at PRD-generation time (`maybe_auto_create_project_for_prd`,
// `backend/app/project_from_prd.py`) covers the other one (forking at
// generation time). Both converge on `origin: "prd_auto"`.
//
// Invite rows (AD-P5, AC4/AC5): email + ACCESS level ONLY — no title/role
// field. A person's job title comes from their OWN onboarding
// (`profiles.role`), never from whoever invites them. The access selector
// uses the app's REAL permission vocabulary — `admin | member | viewer`
// (`teamApi.ts` `InviteRole`, default `member`) — not any invented
// "can edit"/"can view" scheme (cf. the mockup's placeholder "Can edit"
// label, which this build deliberately does not carry over).
//
// Row-UI reuse (AC6): the email/select/remove-button row layout below reuses
// `InviteModal.tsx`'s row mechanics — literally the same global CSS classes
// that component renders with (`invite-rows`/`invite-email-row`/
// `invite-add-btn`/`invite-remove-btn`, `globals.css`) — WITHOUT importing
// `InviteModal` or its `sendInvites`, which is a toast-only stub with no
// backend call. This modal never invokes that stub; on create, invite rows
// with a non-empty email are passed to the real member-add endpoint
// (`POST /v1/projects/{id}/members`) as a best-effort follow-up — a row
// that fails to add (e.g. no account for that email yet; a non-existing-
// user invite is a fast-follow, out of scope) never blocks project
// creation or navigation.
//
// FILES AT CREATION (2026-09-08): the manual tab also takes documents. They
// upload AFTER the project row exists — `POST /v1/projects/{id}/documents`
// needs an id — so this is the same create-then-follow-up shape the artifact
// tab and the invite rows already use. Each file becomes a `custom_artifact`
// the project agent can READ, which is the point: a project created with its
// brief already attached can answer questions on the first turn, where one
// created empty needs a second trip through Add artifact first.
//
// DELIBERATELY NO FILE-COUNT CAP here (owner decision 2026-09-08). The server
// caps each file at 25 MB and refuses what it cannot read; a count limit on
// top would be an invention with no rule behind it. The chat composer is a
// different question and is not touched by this.
//
// On create (AC2/AC3/AD-P14): `projectsApi.create` then navigate to the
// FLAT `/projects?id=<new_id>` route — never `/projects/<new_id>`.
import { useCallback, useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { useCompany } from "../../../../context/CompanyContext"
import { useNavigation } from "../../../../context/NavigationContext"
import { projectPath } from "../../../../lib/routes"
import { artifactsApi, projectsApi, isProjectArtifactType, type ArtifactItem, type ProjectArtifactType } from "../../../../lib/api"
import type { InviteRole } from "../../../../lib/teamApi"
import { IconClose } from "../../../shared/app-icons"
import { useEscapeToClose } from "./useEscapeToClose"
import styles from "./CreateProjectModal.module.css"

export type CreateTab = "manual" | "artifact" | "auto"

export type InviteRowState = { email: string; role: InviteRole }

export type ArtifactsLoadState = "loading" | "ready" | "error"

/** Same badge palette `ArtifactsModal.tsx`/`ProjectDetailScreen.tsx`
 *  duplicate locally (verbatim from `ArtifactsScreen.tsx`'s `ARTIFACT_BADGE`
 *  — the app's one real palette, not a new one; those files are not
 *  declared Deliverables for this ticket, so the small lookup is
 *  re-duplicated here rather than imported). */
const BADGE: Record<ProjectArtifactType, { label: string; bg: string; color: string }> = {
  prd: { label: "PRD", bg: "var(--accent-soft, #EEEFF0)", color: "var(--accent-ink, #16181A)" },
  prototype: { label: "PROTOTYPE", bg: "#DBEAFE", color: "#1E40AF" },
  evidence: { label: "EVIDENCE", bg: "#FEF0E6", color: "#B45309" },
  report: { label: "REPORT", bg: "#EDE9FE", color: "#6D28D9" },
  ticket_set: { label: "TICKETS", bg: "var(--info-soft)", color: "var(--info)" },
  custom_artifact: { label: "DOC", bg: "var(--surface-2, #F0EDE7)", color: "var(--ink-2, #5A5853)" },
}

/** `BADGE`'s fallback for a type outside `ProjectArtifactType` (a
 *  custom_artifact row from `artifactsApi.list()`) — this tab's picker has
 *  no slot for one (no "custom_artifact" chip/flow exists here), so it
 *  renders a neutral badge rather than crashing; `onSelectArtifact`/create
 *  are still gated by `isProjectArtifactType` below before any write. */
const UNKNOWN_BADGE = { label: "ARTIFACT", bg: "var(--info-soft)", color: "var(--info)" }
function badgeFor(type: ArtifactItem["type"]): { label: string; bg: string; color: string } {
  return isProjectArtifactType(type) ? BADGE[type] : UNKNOWN_BADGE
}

function artifactKey(a: ArtifactItem): string {
  return `${a.type}-${a.id}`
}

function artifactTitle(a: ArtifactItem): string {
  if (a.type === "ticket_set") return a.title.trim() || "Tickets from this conversation"
  return a.title
}

// ── Presentational view ──

export type CreateProjectModalViewProps = {
  open: boolean
  tab: CreateTab
  onTabChange: (t: CreateTab) => void
  name: string
  onNameChange: (v: string) => void
  whyText: string
  onWhyChange: (v: string) => void
  rows: InviteRowState[]
  onRowEmailChange: (i: number, v: string) => void
  onRowRoleChange: (i: number, v: InviteRole) => void
  onAddRow: () => void
  onRemoveRow: (i: number) => void
  artifactsStatus: ArtifactsLoadState
  artifacts: ArtifactItem[]
  selectedArtifact: ArtifactItem | null
  onSelectArtifact: (a: ArtifactItem) => void
  selectedPrd: ArtifactItem | null
  onSelectPrd: (a: ArtifactItem) => void
  /** Files staged for upload after the project is created. */
  files: File[]
  onAddFiles: (picked: FileList | null) => void
  onRemoveFile: (i: number) => void
  creating: boolean
  error: string | null
  onCancel: () => void
  onCreate: () => void
}

export function CreateProjectModalView({
  open,
  tab,
  onTabChange,
  name,
  onNameChange,
  whyText,
  onWhyChange,
  rows,
  onRowEmailChange,
  onRowRoleChange,
  onAddRow,
  onRemoveRow,
  artifactsStatus,
  artifacts,
  selectedArtifact,
  onSelectArtifact,
  selectedPrd,
  onSelectPrd,
  files,
  onAddFiles,
  onRemoveFile,
  creating,
  error,
  onCancel,
  onCreate,
}: CreateProjectModalViewProps) {
  const dialogRef = useRef<HTMLDivElement>(null)
  const openerRef = useRef<Element | null>(null)

  useEffect(() => {
    if (!open) return
    openerRef.current = document.activeElement
    const first = dialogRef.current?.querySelector<HTMLElement>(
      "button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])",
    )
    first?.focus()
    const opener = openerRef.current
    return () => {
      if (opener instanceof HTMLElement) opener.focus()
    }
  }, [open])

  // Document-level listener — reliable Escape-to-close regardless of where
  // focus actually is (the panel's own onKeyDown below only ever handles
  // Tab-wrap now; see useEscapeToClose.ts for why).
  useEscapeToClose(open, onCancel)

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key !== "Tab") return
      const focusables = Array.from(
        dialogRef.current?.querySelectorAll<HTMLElement>(
          "button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])",
        ) ?? [],
      )
      if (focusables.length === 0) return
      const first = focusables[0]
      const last = focusables[focusables.length - 1]
      const active = document.activeElement
      if (e.shiftKey && active === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && active === last) {
        e.preventDefault()
        first.focus()
      }
    },
    [],
  )

  if (!open) return null

  const canCreateManual = name.trim().length > 0
  const canCreateArtifact = selectedArtifact != null
  const canCreateAuto = selectedPrd != null
  const prdArtifacts = artifacts.filter((a) => a.type === "prd")

  return (
    <div className="modal-overlay open" onClick={(e) => e.target === e.currentTarget && onCancel()}>
      <div
        ref={dialogRef}
        className={`modal modal-md ${styles.panel}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-project-modal-title"
        onKeyDown={onKeyDown}
        data-testid="create-project-modal"
      >
        <div className="modal-head">
          <div className="modal-head-text">
            <h2 className="modal-title" id="create-project-modal-title">
              New project
            </h2>
          </div>
          <button
            type="button"
            className="modal-close"
            onClick={onCancel}
            aria-label="Close"
            data-testid="create-project-close"
          >
            <IconClose size={16} title="Close" />
          </button>
        </div>

        <div className={styles.tabs} role="tablist" aria-label="Create a project">
          <button
            type="button"
            role="tab"
            aria-selected={tab === "manual"}
            className={`${styles.tab} ${tab === "manual" ? styles.tabOn : ""}`}
            onClick={() => onTabChange("manual")}
            data-testid="create-project-tab-manual"
          >
            Start manually
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "artifact"}
            className={`${styles.tab} ${tab === "artifact" ? styles.tabOn : ""}`}
            onClick={() => onTabChange("artifact")}
            data-testid="create-project-tab-artifact"
          >
            From an artifact
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "auto"}
            className={`${styles.tab} ${tab === "auto" ? styles.tabOn : ""}`}
            onClick={() => onTabChange("auto")}
            data-testid="create-project-tab-auto"
          >
            Auto · from PRD
          </button>
        </div>

        <div className="modal-body" data-testid="create-project-body">
          {error ? (
            <div className={styles.errorBanner} data-testid="create-project-error">
              {error}
            </div>
          ) : null}

          {tab === "manual" ? (
            <div data-testid="create-project-panel-manual">
              <div className={styles.field}>
                <label className="field-label" htmlFor="create-project-name">
                  Project name
                </label>
                <input
                  id="create-project-name"
                  className="input"
                  type="text"
                  placeholder="e.g. Instant-quote flow"
                  value={name}
                  onChange={(e) => onNameChange(e.target.value)}
                  data-testid="create-project-name-input"
                />
              </div>

              <div className={styles.field}>
                <label className="field-label" htmlFor="create-project-why">
                  What&apos;s this project about? <span className={styles.hint}>(optional)</span>
                </label>
                <input
                  id="create-project-why"
                  className="input"
                  type="text"
                  placeholder="One line on the goal / why this project exists (optional)"
                  value={whyText}
                  onChange={(e) => onWhyChange(e.target.value)}
                  data-testid="create-project-why-input"
                />
              </div>

              {/* Documents, before the invite rows: what the project is made
                  of belongs nearer its name than who else can see it. */}
              <div className={styles.field}>
                <label className="field-label" htmlFor="create-project-files">
                  Add documents <span className={styles.hint}>(optional)</span>
                </label>
                <input
                  id="create-project-files"
                  className="input"
                  type="file"
                  multiple
                  onChange={(e) => {
                    onAddFiles(e.target.files)
                    // Clear the control so picking the SAME file again after
                    // removing it still fires a change event.
                    e.target.value = ""
                  }}
                  data-testid="create-project-files-input"
                />
                {files.length > 0 ? (
                  <ul className={styles.fileList} data-testid="create-project-file-list">
                    {files.map((f, i) => (
                      <li key={`${f.name}-${i}`} className={styles.fileRow}>
                        <span className={styles.fileName}>{f.name}</span>
                        <button
                          type="button"
                          className="invite-remove-btn"
                          onClick={() => onRemoveFile(i)}
                          aria-label={`Remove ${f.name}`}
                          data-testid={`create-project-file-remove-${i}`}
                        >
                          <IconClose size={14} />
                        </button>
                      </li>
                    ))}
                  </ul>
                ) : null}
                <p className={styles.hint} data-testid="create-project-files-hint">
                  We read the text and attach each one to the project, so the
                  project chat can use them from the first question. Up to 25 MB
                  a file.
                </p>
              </div>

              <div className={styles.field}>
                <label className="field-label">Invite teammates (optional)</label>
                <div className="invite-rows" data-testid="create-project-invite-rows">
                  {rows.map((row, i) => (
                    <div className="invite-email-row" key={i} data-testid={`create-project-invite-row-${i}`}>
                      <input
                        type="email"
                        className="input"
                        placeholder="teammate@company.com"
                        aria-label={`Teammate ${i + 1} email`}
                        value={row.email}
                        onChange={(e) => onRowEmailChange(i, e.target.value)}
                        data-testid={`create-project-invite-email-${i}`}
                      />
                      <select
                        className="ticket-select"
                        aria-label={`Teammate ${i + 1} access`}
                        value={row.role}
                        onChange={(e) => onRowRoleChange(i, e.target.value as InviteRole)}
                        data-testid={`create-project-invite-role-${i}`}
                      >
                        <option value="member">Member</option>
                        <option value="admin">Admin</option>
                        <option value="viewer">Viewer</option>
                      </select>
                      {rows.length > 1 ? (
                        <button
                          type="button"
                          className="invite-remove-btn"
                          onClick={() => onRemoveRow(i)}
                          aria-label={`Remove teammate ${i + 1}`}
                          data-testid={`create-project-invite-remove-${i}`}
                        >
                          <IconClose size={14} />
                        </button>
                      ) : null}
                    </div>
                  ))}
                </div>
                <button
                  type="button"
                  className="invite-add-btn"
                  onClick={onAddRow}
                  data-testid="create-project-invite-add"
                >
                  + Add another
                </button>
                <p className={styles.hint} data-testid="create-project-invite-hint">
                  Just email and access here — each teammate&apos;s title comes from their own onboarding, not from
                  you.
                </p>
              </div>
            </div>
          ) : null}

          {tab === "artifact" ? (
            <div data-testid="create-project-panel-artifact">
              <p className={styles.pickNote}>
                Pick any artifact — it becomes the project&apos;s first item, and the project takes its name. A PRD
                is just one selectable artifact here, never the project&apos;s identity.
              </p>
              <div className={styles.field}>
                <label className="field-label" htmlFor="create-project-why">
                  What&apos;s this project about? <span className={styles.hint}>(optional)</span>
                </label>
                <input
                  id="create-project-why"
                  className="input"
                  type="text"
                  placeholder="One line on the goal / why this project exists (optional)"
                  value={whyText}
                  onChange={(e) => onWhyChange(e.target.value)}
                  data-testid="create-project-why-input"
                />
              </div>
              {artifactsStatus === "loading" ? (
                <div className={styles.stateWrap} aria-busy="true" data-testid="create-project-artifacts-loading">
                  Loading artifacts…
                </div>
              ) : artifactsStatus === "error" ? (
                <div className={styles.stateWrap} data-testid="create-project-artifacts-error">
                  Couldn&apos;t load your artifacts. Try again.
                </div>
              ) : artifacts.length === 0 ? (
                <div className={styles.stateWrap} data-testid="create-project-artifacts-empty">
                  No artifacts yet — create one first, or start manually instead.
                </div>
              ) : (
                <div className={styles.artPick} data-testid="create-project-artifact-list">
                  {artifacts.map((a) => {
                    const cfg = badgeFor(a.type)
                    const isSel = selectedArtifact != null && artifactKey(selectedArtifact) === artifactKey(a)
                    return (
                      <div
                        key={artifactKey(a)}
                        role="button"
                        tabIndex={0}
                        className={`${styles.artRow} ${isSel ? styles.artRowSel : ""}`}
                        onClick={() => onSelectArtifact(a)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") onSelectArtifact(a)
                        }}
                        data-testid={`create-project-artifact-row-${artifactKey(a)}`}
                        aria-current={isSel ? "true" : undefined}
                      >
                        <span className={styles.badge} style={{ background: cfg.bg, color: cfg.color }}>
                          {cfg.label}
                        </span>
                        <span className={styles.artTitle}>{artifactTitle(a)}</span>
                      </div>
                    )
                  })}
                </div>
              )}
              {selectedArtifact ? (
                <p className={styles.pickNote} data-testid="create-project-artifact-selected">
                  <b>Project name:</b> &ldquo;{artifactTitle(selectedArtifact)}&rdquo; · <b>first item:</b> the
                  selected {badgeFor(selectedArtifact.type).label.toLowerCase()}.
                </p>
              ) : null}
            </div>
          ) : null}

          {tab === "auto" ? (
            <div data-testid="create-project-panel-auto">
              <p className={styles.pickNote}>
                Pick a PRD you already have — it forks into its own project, with that PRD carried over as the
                first artifact and this thread bound to it.
              </p>
              {artifactsStatus === "loading" ? (
                <div className={styles.stateWrap} aria-busy="true" data-testid="create-project-auto-loading">
                  Loading your PRDs…
                </div>
              ) : artifactsStatus === "error" ? (
                <div className={styles.stateWrap} data-testid="create-project-auto-error">
                  Couldn&apos;t load your PRDs. Try again.
                </div>
              ) : prdArtifacts.length === 0 ? (
                <div className={styles.stateWrap} data-testid="create-project-auto-empty">
                  No PRDs yet — generate one first, or start manually instead.
                </div>
              ) : (
                <div className={styles.artPick} data-testid="create-project-auto-prd-list">
                  {prdArtifacts.map((a) => {
                    const cfg = BADGE[a.type]
                    const isSel = selectedPrd != null && artifactKey(selectedPrd) === artifactKey(a)
                    return (
                      <div
                        key={artifactKey(a)}
                        role="button"
                        tabIndex={0}
                        className={`${styles.artRow} ${isSel ? styles.artRowSel : ""}`}
                        onClick={() => onSelectPrd(a)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") onSelectPrd(a)
                        }}
                        data-testid={`create-project-auto-prd-row-${a.id}`}
                        aria-current={isSel ? "true" : undefined}
                      >
                        <span className={styles.badge} style={{ background: cfg.bg, color: cfg.color }}>
                          {cfg.label}
                        </span>
                        <span className={styles.artTitle}>{artifactTitle(a)}</span>
                      </div>
                    )
                  })}
                </div>
              )}
              {selectedPrd ? (
                <p className={styles.pickNote} data-testid="create-project-auto-selected">
                  <b>Forked context:</b> &ldquo;{artifactTitle(selectedPrd)}&rdquo; carries over as this project&apos;s
                  first artifact.
                </p>
              ) : null}
            </div>
          ) : null}
        </div>

        <div className="modal-foot">
          <button type="button" className="btn btn-ghost" onClick={onCancel} data-testid="create-project-cancel">
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={onCreate}
            disabled={
              creating ||
              (tab === "manual" ? !canCreateManual : tab === "artifact" ? !canCreateArtifact : !canCreateAuto)
            }
            data-testid="create-project-submit"
          >
            {creating ? "Creating…" : "Create & open chat"}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Container: state + create wiring ──

export function CreateProjectModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const router = useRouter()
  const { activeCompany } = useCompany()
  const { showToast } = useNavigation()

  const [tab, setTab] = useState<CreateTab>("manual")
  const [name, setName] = useState("")
  const [whyText, setWhyText] = useState("")
  const [rows, setRows] = useState<InviteRowState[]>([{ email: "", role: "member" }])
  const [artifactsStatus, setArtifactsStatus] = useState<ArtifactsLoadState>("loading")
  const [artifacts, setArtifacts] = useState<ArtifactItem[]>([])
  const [selectedArtifact, setSelectedArtifact] = useState<ArtifactItem | null>(null)
  const [selectedPrd, setSelectedPrd] = useState<ArtifactItem | null>(null)
  const [files, setFiles] = useState<File[]>([])
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Reset to a clean slate every time the modal (re)opens, and load the
  // artifact-picker's candidates — cheap enough to fetch up front rather
  // than gate it behind a tab switch.
  useEffect(() => {
    if (!open) return
    setTab("manual")
    setName("")
    setWhyText("")
    setRows([{ email: "", role: "member" }])
    setSelectedArtifact(null)
    setSelectedPrd(null)
    setFiles([])
    setCreating(false)
    setError(null)
    setArtifactsStatus("loading")
    if (!activeCompany) {
      setArtifactsStatus("error")
      return
    }
    artifactsApi
      .list(activeCompany)
      .then((items) => {
        setArtifacts(items)
        setArtifactsStatus("ready")
      })
      .catch(() => setArtifactsStatus("error"))
  }, [open, activeCompany])

  const onRowEmailChange = useCallback((i: number, v: string) => {
    setRows((prev) => prev.map((r, idx) => (idx === i ? { ...r, email: v } : r)))
  }, [])

  const onRowRoleChange = useCallback((i: number, v: InviteRole) => {
    setRows((prev) => prev.map((r, idx) => (idx === i ? { ...r, role: v } : r)))
  }, [])

  const onAddRow = useCallback(() => {
    setRows((prev) => [...prev, { email: "", role: "member" }])
  }, [])

  const onRemoveRow = useCallback((i: number) => {
    setRows((prev) => (prev.length > 1 ? prev.filter((_, idx) => idx !== i) : prev))
  }, [])

  /** APPEND, never replace. A picker fires once per visit, so someone adding
   *  three files in three visits must end up with three — assigning would
   *  leave them with the last one and no sign the others were dropped. */
  const onAddFiles = useCallback((picked: FileList | null) => {
    if (!picked || picked.length === 0) return
    setFiles((prev) => [...prev, ...Array.from(picked)])
  }, [])

  const onRemoveFile = useCallback((i: number) => {
    setFiles((prev) => prev.filter((_, idx) => idx !== i))
  }, [])

  const onCreate = useCallback(() => {
    if (creating) return
    setError(null)

    if (tab === "manual") {
      const trimmed = name.trim()
      if (!trimmed) {
        setError("Name your project to continue.")
        return
      }
      setCreating(true)
      projectsApi
        .create({ name: trimmed, origin: "manual", seed_text: whyText.trim() || undefined })
        .then((project) => {
          // Best-effort member-add for rows with a non-empty email — never
          // blocks navigation. A row with no account behind that email
          // (404) or any other failure is silently skipped: non-existing-
          // user invites are a fast-follow (org_invites), out of scope
          // here.
          const validRows = rows.filter((r) => r.email.trim().length > 0)
          return Promise.allSettled(
            validRows.map((r) => projectsApi.addMember(project.id, r.email.trim())),
          ).then(() => project)
        })
        .then((project) => {
          // Uploads run AFTER creation because the endpoint is keyed on the
          // project id, and they are AWAITED before navigating so the reader
          // lands on a project whose documents are already there rather than
          // watching them appear.
          //
          // Best-effort, exactly like the member-add above: the server refuses
          // a file it cannot read (a scanned PDF is a 422) and one bad file
          // must not cost someone the project and everything else in it. What
          // failed is named on arrival — a document silently missing from a
          // project is worse than a sentence saying which one.
          if (files.length === 0) return { project, failed: [] as string[] }
          return Promise.allSettled(
            files.map((f) => projectsApi.uploadDocument(project.id, f)),
          ).then((results) => ({
            project,
            failed: files
              .filter((_, i) => results[i]?.status === "rejected")
              .map((f) => f.name),
          }))
        })
        .then(({ project, failed }) => {
          // SAY WHICH FILE DIDN'T TAKE. The project is created and we are about
          // to navigate into it, so an error inside the modal would vanish with
          // the modal — the toast is the only surface that survives the
          // navigation. A document silently missing from a project is the
          // failure worth avoiding: nobody re-checks an upload they were not
          // told about.
          if (failed.length > 0) {
            showToast(
              failed.length === 1 ? "One file couldn't be read" : `${failed.length} files couldn't be read`,
              `${failed.join(", ")} — the project was created without ${failed.length === 1 ? "it" : "them"}. Scanned PDFs and images aren't readable yet; try again from Add artifact.`,
            )
          }
          router.push(projectPath(project.id))
          onClose()
        })
        .catch(() => setError("Couldn't create the project. Try again."))
        .finally(() => setCreating(false))
      return
    }

    if (tab === "artifact") {
      if (!selectedArtifact) {
        setError("Pick an artifact to continue.")
        return
      }
      setCreating(true)
      const artifact = selectedArtifact
      // A custom_artifact row can't be a project's first item today (no
      // slot exists in project_artifacts for it — see isProjectArtifactType's
      // own doc) — unreachable via this tab's own picker (which never shows
      // one, see badgeFor above), but the type is statically wider, so the
      // ref-add itself stays gated rather than assuming it away.
      if (!isProjectArtifactType(artifact.type)) {
        setError("That artifact can't be a project's first item yet.")
        setCreating(false)
        return
      }
      // Narrowing on `artifact.type` above doesn't carry into the `.then()`
      // closure below (a promise callback, not a nested function TS's
      // control-flow analysis treats as immediately-invoked) — captured into
      // its own binding so the guard's proof actually reaches the call.
      const artifactType = artifact.type
      projectsApi
        .create({ name: artifactTitle(artifact), origin: "artifact", seed_text: whyText.trim() || undefined })
        .then((project) =>
          projectsApi
            .addArtifact(project.id, artifactType, artifact.id)
            // Best-effort follow-up ref-add (Implementation Notes: "the
            // ref-add is a follow-up call") — a failure here leaves the
            // project created but without its first item rather than
            // stranding the user with no project at all.
            .catch(() => {})
            .then(() => project),
        )
        .then((project) => {
          router.push(projectPath(project.id))
          onClose()
        })
        .catch(() => setError("Couldn't create the project. Try again."))
        .finally(() => setCreating(false))
      return
    }

    if (tab === "auto") {
      if (!selectedPrd) {
        setError("Pick a PRD to fork.")
        return
      }
      setCreating(true)
      const prd = selectedPrd
      // This tab's own picker only ever lists `prdArtifacts` (`a.type ===
      // "prd"`), so this is always true in practice — guarded rather than
      // assumed, since `selectedPrd`'s declared type is the wider
      // `ArtifactItem | null`.
      if (!isProjectArtifactType(prd.type)) {
        setError("Pick a PRD to fork.")
        setCreating(false)
        return
      }
      // Same closure-narrowing note as the "artifact" tab above.
      const prdType = prd.type
      projectsApi
        // prd_id lets the server dedupe (first-write-wins, AD-P9):
        // re-selecting an already-forked PRD returns the EXISTING project
        // instead of minting a duplicate.
        .create({ name: artifactTitle(prd), origin: "prd_auto", prd_id: prd.id })
        .then((project) =>
          projectsApi
            .addArtifact(project.id, prdType, prd.id)
            // Same best-effort follow-up posture as the "From an artifact"
            // tab: the project exists either way, even if the artifact ref
            // add fails.
            .catch(() => {})
            .then(() => project),
        )
        .then((project) => {
          router.push(projectPath(project.id))
          onClose()
        })
        .catch(() => setError("Couldn't create the project. Try again."))
        .finally(() => setCreating(false))
    }
  }, [creating, tab, name, whyText, rows, files, selectedArtifact, selectedPrd, router, onClose, showToast])

  return (
    <CreateProjectModalView
      open={open}
      tab={tab}
      onTabChange={setTab}
      name={name}
      onNameChange={setName}
      whyText={whyText}
      onWhyChange={setWhyText}
      rows={rows}
      onRowEmailChange={onRowEmailChange}
      onRowRoleChange={onRowRoleChange}
      onAddRow={onAddRow}
      onRemoveRow={onRemoveRow}
      artifactsStatus={artifactsStatus}
      artifacts={artifacts}
      selectedArtifact={selectedArtifact}
      onSelectArtifact={setSelectedArtifact}
      selectedPrd={selectedPrd}
      onSelectPrd={setSelectedPrd}
      files={files}
      onAddFiles={onAddFiles}
      onRemoveFile={onRemoveFile}
      creating={creating}
      error={error}
      onCancel={onClose}
      onCreate={onCreate}
    />
  )
}
