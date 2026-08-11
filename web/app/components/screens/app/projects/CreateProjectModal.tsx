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
// On create (AC2/AC3/AD-P14): `projectsApi.create` then navigate to the
// FLAT `/projects?id=<new_id>` route — never `/projects/<new_id>`.
import { useCallback, useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { useCompany } from "../../../../context/CompanyContext"
import { projectPath } from "../../../../lib/routes"
import { artifactsApi, projectsApi, type ArtifactItem, type ProjectArtifactType } from "../../../../lib/api"
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
  prd: { label: "PRD", bg: "#DBF1E7", color: "#0E6E49" },
  prototype: { label: "PROTOTYPE", bg: "#DBEAFE", color: "#1E40AF" },
  evidence: { label: "EVIDENCE", bg: "#FEF0E6", color: "#B45309" },
  report: { label: "REPORT", bg: "#EDE9FE", color: "#6D28D9" },
  ticket_set: { label: "TICKETS", bg: "var(--info-soft)", color: "var(--info)" },
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
                    const cfg = BADGE[a.type]
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
                  selected {BADGE[selectedArtifact.type].label.toLowerCase()}.
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

  const [tab, setTab] = useState<CreateTab>("manual")
  const [name, setName] = useState("")
  const [rows, setRows] = useState<InviteRowState[]>([{ email: "", role: "member" }])
  const [artifactsStatus, setArtifactsStatus] = useState<ArtifactsLoadState>("loading")
  const [artifacts, setArtifacts] = useState<ArtifactItem[]>([])
  const [selectedArtifact, setSelectedArtifact] = useState<ArtifactItem | null>(null)
  const [selectedPrd, setSelectedPrd] = useState<ArtifactItem | null>(null)
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Reset to a clean slate every time the modal (re)opens, and load the
  // artifact-picker's candidates — cheap enough to fetch up front rather
  // than gate it behind a tab switch.
  useEffect(() => {
    if (!open) return
    setTab("manual")
    setName("")
    setRows([{ email: "", role: "member" }])
    setSelectedArtifact(null)
    setSelectedPrd(null)
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
        .create({ name: trimmed, origin: "manual" })
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
      projectsApi
        .create({ name: artifactTitle(artifact), origin: "artifact" })
        .then((project) =>
          projectsApi
            .addArtifact(project.id, artifact.type, artifact.id)
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
      projectsApi
        .create({ name: artifactTitle(prd), origin: "prd_auto" })
        .then((project) =>
          projectsApi
            .addArtifact(project.id, prd.type, prd.id)
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
  }, [creating, tab, name, rows, selectedArtifact, selectedPrd, router, onClose])

  return (
    <CreateProjectModalView
      open={open}
      tab={tab}
      onTabChange={setTab}
      name={name}
      onNameChange={setName}
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
      creating={creating}
      error={error}
      onCancel={onClose}
      onCreate={onCreate}
    />
  )
}
