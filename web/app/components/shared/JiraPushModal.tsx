"use client"

import { useEffect, useState } from "react"
import { IconCheck, IconX } from "@tabler/icons-react"
import type { JiraMember, JiraProject } from "../../lib/api"

/** One ticket to place in the push list — the minimal shape the modal needs. */
export type JiraPushItem = { key: string; title: string }

/** The result the modal hands back on push. `assigneeByKey` maps a ticket key to
 *  the chosen Atlassian accountId ("" = unassigned). */
export type JiraPushChoice = {
  projectKey: string
  issueType: string
  assigneeByKey: Record<string, string>
  remember: boolean
}

// Jira's built-in issue types cover the common cases. Fetching per-project
// createmeta would be more precise but is a heavier call — this covers pilot use
// and matches the backend default ("Task").
const ISSUE_TYPES = ["Task", "Story", "Bug", "Epic"] as const

/** The Jira push modal: choose a project + issue type, then assign each ticket to
 *  a project member (or leave unassigned), and push. Unlike the compact ClickUp
 *  DestinationPicker, Jira push needs a per-ticket assignee column, so this is a
 *  centered modal with a scrollable ticket list. Members load when a project is
 *  selected (assignable-users are project-scoped). */
export function JiraPushModal({
  items, projects, initialProjectKey, loadMembers, onPush, onCancel, busy,
}: {
  items: JiraPushItem[]
  projects: JiraProject[]
  initialProjectKey?: string | null
  loadMembers: (projectKey: string) => Promise<JiraMember[]>
  onPush: (choice: JiraPushChoice) => void
  onCancel: () => void
  busy?: boolean
}) {
  const firstKey = initialProjectKey || projects[0]?.key || ""
  const [projectKey, setProjectKey] = useState(firstKey)
  const [issueType, setIssueType] = useState<string>("Task")
  const [remember, setRemember] = useState(true)
  const [members, setMembers] = useState<JiraMember[]>([])
  const [membersState, setMembersState] = useState<"idle" | "loading" | "error">("idle")
  const [assignee, setAssignee] = useState<Record<string, string>>({})

  // Load assignable members whenever the selected project changes. Assignments
  // are cleared on project switch — an accountId assignable in one project may
  // not be in another.
  useEffect(() => {
    if (!projectKey) return
    let cancelled = false
    setMembersState("loading")
    setMembers([])
    setAssignee({})
    loadMembers(projectKey)
      .then((m) => { if (!cancelled) { setMembers(m); setMembersState("idle") } })
      .catch(() => { if (!cancelled) { setMembers([]); setMembersState("error") } })
    return () => { cancelled = true }
  }, [projectKey, loadMembers])

  const setOne = (key: string, accountId: string) =>
    setAssignee((prev) => ({ ...prev, [key]: accountId }))

  const assignAll = (accountId: string) =>
    setAssignee(Object.fromEntries(items.map((it) => [it.key, accountId])))

  return (
    <>
      <div onClick={busy ? undefined : onCancel} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.35)", zIndex: 40 }} aria-hidden />
      <div
        className="tkv2-picker"
        role="dialog"
        aria-label="Push to Jira"
        style={{
          position: "fixed", top: "50%", left: "50%", transform: "translate(-50%,-50%)",
          zIndex: 41, width: "min(560px, 92vw)", maxWidth: "min(560px, 92vw)",
          maxHeight: "82vh", display: "flex", flexDirection: "column", padding: 0,
        }}
      >
        <div className="ph2" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "12px 12px 8px", marginBottom: 0 }}>
          <span>Push to Jira</span>
          <button type="button" onClick={onCancel} disabled={busy} aria-label="Close" style={{ background: "none", border: "none", cursor: "pointer", display: "inline-flex" }}>
            <IconX size={14} />
          </button>
        </div>

        {/* Project + issue type row */}
        <div style={{ display: "flex", gap: 10, padding: "10px 12px", flexWrap: "wrap" }}>
          <label style={{ display: "flex", flexDirection: "column", gap: 4, flex: "1 1 220px", fontSize: 12 }}>
            Project
            <select value={projectKey} onChange={(e) => setProjectKey(e.target.value)} disabled={busy} className="tkv2-select">
              {projects.map((p) => (
                <option key={p.id || p.key} value={p.key}>{p.name} ({p.key})</option>
              ))}
            </select>
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: 4, flex: "0 1 140px", fontSize: 12 }}>
            Issue type
            <select value={issueType} onChange={(e) => setIssueType(e.target.value)} disabled={busy} className="tkv2-select">
              {ISSUE_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </label>
        </div>

        {/* Assign-all convenience + member load state */}
        <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "0 12px 8px", fontSize: 12, color: "var(--muted, #667)" }}>
          <span>Assign all to</span>
          <select
            onChange={(e) => assignAll(e.target.value)}
            disabled={busy || membersState !== "idle"}
            className="tkv2-select"
            style={{ maxWidth: 200 }}
            defaultValue=""
          >
            <option value="">— choose —</option>
            <option value="">Unassigned</option>
            {members.map((m) => <option key={m.accountId} value={m.accountId}>{m.displayName || m.email || m.accountId}</option>)}
          </select>
          {membersState === "loading" && <span>Loading members…</span>}
          {membersState === "error" && <span style={{ color: "var(--red, #c33)" }}>Couldn’t load members</span>}
        </div>

        {/* Per-ticket assignee list */}
        <div style={{ overflowY: "auto", borderTop: "1px solid var(--border, #e5e7eb)", flex: 1 }}>
          {items.map((it) => (
            <div key={it.key} style={{ display: "flex", alignItems: "center", gap: 8, padding: "7px 12px", borderBottom: "1px solid var(--border, #f0f1f3)" }}>
              <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: 13 }} title={it.title}>{it.title}</span>
              <select
                value={assignee[it.key] ?? ""}
                onChange={(e) => setOne(it.key, e.target.value)}
                disabled={busy || membersState === "loading"}
                className="tkv2-select"
                style={{ flex: "0 0 180px" }}
                aria-label={`Assignee for ${it.title}`}
              >
                <option value="">Unassigned</option>
                {members.map((m) => <option key={m.accountId} value={m.accountId}>{m.displayName || m.email || m.accountId}</option>)}
              </select>
            </div>
          ))}
        </div>

        <div className="tkv2-pfoot" style={{ padding: "10px 12px", marginTop: 0 }}>
          <label style={{ display: "inline-flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
            <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} style={{ accentColor: "var(--green)" }} />
            Remember for this PRD
          </label>
          <button
            type="button"
            className="tkv2-btn2 tkv2-btn2--primary"
            style={{ display: "inline-flex", alignItems: "center", gap: 5 }}
            onClick={() => onPush({ projectKey, issueType, assigneeByKey: assignee, remember })}
            disabled={busy || !projectKey}
          >
            <IconCheck size={12} /> {busy ? "Pushing…" : `Push ${items.length} ticket${items.length !== 1 ? "s" : ""}`}
          </button>
        </div>
      </div>
    </>
  )
}
