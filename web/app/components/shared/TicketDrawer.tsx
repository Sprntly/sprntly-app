"use client"

import { useState, useEffect } from "react"
import { useNavigation } from "../../context/NavigationContext"
import { useContent } from "../../context/ContentContext"
import { connectorsApi, type ConnectionSummary } from "../../lib/api"
import { htmlPrdToPlainText } from "../../lib/htmlBrief"
import type { PrdState } from "../../types/content"
import { saveTicket } from "../screens/app/TicketsScreen"
import { IconCheck, IconClose } from "./app-icons"

const TICKET_PROVIDERS = new Set(["linear", "jira", "asana"])

function hasTicketConnector(connections: ConnectionSummary[]): boolean {
  return connections.some(
    (c) => c.status === "active" && TICKET_PROVIDERS.has(c.provider)
  )
}

function prdDescription(prd: PrdState | null): string {
  if (!prd) return ""
  // v3 HTML PRD: no parsed sections — derive the description from the page text.
  if (prd.html) return htmlPrdToPlainText(prd.html).slice(0, 800)
  const lines: string[] = []
  for (const sec of prd.sections) {
    if (sec.type === "h2") lines.push(`\n${sec.text}`)
    else if (sec.type === "p") lines.push(sec.text)
    else if (sec.type === "ul") lines.push(...sec.items.map((it) => `• ${it}`))
  }
  return lines.join("\n").trim().slice(0, 800)
}

// ── Internal ticket form (no tracker connected) ────────────────────────────

function InternalTicketForm({ onClose }: { onClose: () => void }) {
  const { showToast, goTo } = useNavigation()
  const { content } = useContent()
  const [title, setTitle] = useState(content.prd?.title ?? "")
  const [priority, setPriority] = useState<"P0" | "P1" | "P2" | "P3">("P1")
  const [category, setCategory] = useState("Product")
  const [assignee, setAssignee] = useState("")
  const [description, setDescription] = useState(() => prdDescription(content.prd))
  const [pushToClickUp, setPushToClickUp] = useState(false)
  const [clickUpConnected, setClickUpConnected] = useState(false)
  const [creating, setCreating] = useState(false)

  // Check if ClickUp is connected on mount
  useEffect(() => {
    import("../../lib/api").then(({ connectorsApi }) => {
      connectorsApi.list().then((conns) => {
        const list = Array.isArray(conns) ? conns : (conns as unknown as { connections?: any[] }).connections ?? []
        const cu = list.find((c: any) => c.provider === "clickup")
        if (cu) { setClickUpConnected(true); setPushToClickUp(true) }
      }).catch(() => {})
    })
  }, [])

  const handleCreate = async () => {
    setCreating(true)
    const ticket = saveTicket({ title, priority, category, assignee, description })

    // Auto-push to ClickUp if enabled
    if (pushToClickUp && clickUpConnected) {
      try {
        const { ticketPushApi } = await import("../../lib/api")
        const listsRes = await ticketPushApi.listClickUpLists()
        const lists = listsRes.lists
        if (lists.length > 0) {
          const targetList = lists[0] // Use first available list
          const result = await ticketPushApi.pushToClickUp(targetList.id, [{
            task_id: ticket.id,
            title: ticket.title,
            description: ticket.description,
            priority: ticket.priority,
          }])
          if (result.created?.length > 0) {
            const task = result.created[0]
            onClose()
            showToast(
              `Ticket created in ClickUp`,
              `"${title.slice(0, 50)}" pushed to ClickUp. ${task.url ? "" : ""}`,
              task.url || "View tickets →"
            )
            goTo("tickets")
            return
          }
        }
      } catch {
        // ClickUp push failed — still save internally
      }
    }

    onClose()
    showToast(
      `Ticket created · ${ticket.id}`,
      `"${title.slice(0, 60) || "Untitled"}" saved to Tickets.${pushToClickUp ? " ClickUp push failed — saved locally." : ""}`,
      "View tickets →"
    )
    setCreating(false)
    goTo("tickets")
  }

  return (
    <>
      <div className="drawer-body">
        <p className="drawer-sub">
          {clickUpConnected
            ? "ClickUp is connected. Your ticket will be saved internally and pushed to ClickUp automatically."
            : "No ticket tracker connected. This ticket will be saved internally. Connect ClickUp, Linear, or Jira in Settings → Connectors to push externally."}
        </p>

        <div className="ticket-row">
          <div className="ticket-row-label">Title</div>
          <input
            type="text"
            className="input"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Ticket title"
          />
        </div>

        <div className="ticket-row">
          <div className="ticket-row-label">Priority</div>
          <select
            className="ticket-select"
            value={priority}
            onChange={(e) => setPriority(e.target.value as typeof priority)}
          >
            <option value="P0">P0 — Critical</option>
            <option value="P1">P1 — High</option>
            <option value="P2">P2 — Medium</option>
            <option value="P3">P3 — Low</option>
          </select>
        </div>

        <div className="ticket-row">
          <div className="ticket-row-label">Category</div>
          <select
            className="ticket-select"
            value={category}
            onChange={(e) => setCategory(e.target.value)}
          >
            <option>Product</option>
            <option>Design</option>
            <option>Backend</option>
            <option>Frontend</option>
            <option>AI</option>
            <option>Infra</option>
            <option>Analytics</option>
            <option>CS</option>
          </select>
        </div>

        <div className="ticket-row">
          <div className="ticket-row-label">Assignee</div>
          <input
            type="text"
            className="input"
            value={assignee}
            onChange={(e) => setAssignee(e.target.value)}
            placeholder="Name or email"
          />
        </div>

        <div
          className="ticket-row"
          style={{ gridTemplateColumns: "110px 1fr", alignItems: "flex-start" }}
        >
          <div className="ticket-row-label" style={{ paddingTop: 10 }}>
            Description
          </div>
          <textarea
            className="textarea"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            style={{ minHeight: 140, fontSize: 12.5 }}
          />
        </div>

        <div
          style={{
            padding: "10px 12px",
            background: "var(--accent-soft)",
            borderRadius: 8,
            fontSize: 12,
            color: "var(--accent-ink)",
            marginTop: 14,
          }}
        >
          <strong>Saved in Sprntly:</strong> Track status and move to In Progress
          or Done from the Tickets screen. Push to a tracker anytime once
          connected.
        </div>
      </div>

      {/* ClickUp auto-push toggle */}
      {clickUpConnected && (
        <div style={{
          padding: "8px 16px", borderTop: "1px solid var(--line)",
          display: "flex", alignItems: "center", gap: 8,
        }}>
          <input
            type="checkbox"
            id="push-clickup"
            checked={pushToClickUp}
            onChange={(e) => setPushToClickUp(e.target.checked)}
            style={{ accentColor: "var(--accent)" }}
          />
          <label htmlFor="push-clickup" style={{ fontSize: 12.5, color: "var(--ink-2)", cursor: "pointer" }}>
            Also push to ClickUp
          </label>
        </div>
      )}

      <div className="drawer-foot">
        <span style={{ fontSize: 11.5, color: "var(--muted)" }}>
          {clickUpConnected && pushToClickUp ? "Will push to ClickUp" : "Saved internally in Sprntly"}
        </span>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn" onClick={onClose}>Cancel</button>
          <button
            type="button"
            className="btn btn-accent"
            onClick={handleCreate}
            disabled={!title.trim() || creating}
          >
            <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
              <IconCheck size={16} />
              {creating ? "Creating..." : pushToClickUp ? "Create & push" : "Save ticket"}
            </span>
          </button>
        </div>
      </div>
    </>
  )
}

// ── Connected tracker form — original design preserved ─────────────────────

function ConnectedTicketForm({
  connections,
  onClose,
}: {
  connections: ConnectionSummary[]
  onClose: () => void
}) {
  const { showToast } = useNavigation()
  const [selectedAssignees, setSelectedAssignees] = useState<string[]>(["LR"])
  const [selectedLabels, setSelectedLabels] = useState<string[]>([
    "sprntly",
    "activation",
    "auth",
  ])

  const handleCreate = () => {
    onClose()
    showToast(
      "Ticket created in Linear",
      "SPR-412 · Assigned to Lena · High priority. We'll fold impact into Shipped when closed.",
      "Open ticket →"
    )
  }

  const toggleAssignee = (id: string) => {
    setSelectedAssignees((prev) =>
      prev.includes(id) ? prev.filter((a) => a !== id) : [...prev, id]
    )
  }

  const toggleLabel = (label: string) => {
    setSelectedLabels((prev) =>
      prev.includes(label) ? prev.filter((l) => l !== label) : [...prev, label]
    )
  }

  return (
    <>
      <div className="drawer-body">
        <p className="drawer-sub">
          Create a ticket in your connected tracker. The PRD, evidence summary,
          and acceptance criteria travel with it.
        </p>

        <div className="ticket-row">
          <div className="ticket-row-label">Tracker</div>
          <select className="ticket-select">
            <option>Linear — Sprntly · Growth</option>
            <option>Jira — PROD</option>
            <option>Asana — Engineering</option>
          </select>
        </div>

        <div className="ticket-row">
          <div className="ticket-row-label">Project</div>
          <select className="ticket-select">
            <option>Growth — Q2 sprint</option>
            <option>Growth — Backlog</option>
            <option>Platform</option>
          </select>
        </div>

        <div className="ticket-row">
          <div className="ticket-row-label">Title</div>
          <input
            type="text"
            className="input"
            defaultValue="Fix SMS verification delivery on Android for non-US carriers"
          />
        </div>

        <div className="ticket-row">
          <div className="ticket-row-label">Priority</div>
          <select className="ticket-select" defaultValue="High">
            <option>Urgent</option>
            <option>High</option>
            <option>Medium</option>
            <option>Low</option>
          </select>
        </div>

        <div className="ticket-row">
          <div className="ticket-row-label">Assignee</div>
          <div className="ticket-assignees">
            <AssigneeChip
              id="LR"
              name="Lena Reyes"
              selected={selectedAssignees.includes("LR")}
              onClick={() => toggleAssignee("LR")}
            />
            <AssigneeChip
              id="DW"
              name="Dan Westbrook"
              color="#B4541A"
              selected={selectedAssignees.includes("DW")}
              onClick={() => toggleAssignee("DW")}
            />
            <AssigneeChip
              id="RK"
              name="Raj Kapoor"
              color="#2B4A8A"
              selected={selectedAssignees.includes("RK")}
              onClick={() => toggleAssignee("RK")}
            />
            <div className="ticket-assignee-chip">
              <span className="mini-av" style={{ background: "#7A827C" }}>+</span>
              Other
            </div>
          </div>
        </div>

        <div className="ticket-row">
          <div className="ticket-row-label">Labels</div>
          <div className="ticket-assignees">
            {["sprntly", "activation", "auth"].map((label) => (
              <div
                key={label}
                className={`ticket-assignee-chip ${selectedLabels.includes(label) ? "selected" : ""}`}
                onClick={() => toggleLabel(label)}
              >
                {label}
              </div>
            ))}
            <div className="ticket-assignee-chip">+ Add</div>
          </div>
        </div>

        <div
          className="ticket-row"
          style={{ gridTemplateColumns: "110px 1fr", alignItems: "flex-start" }}
        >
          <div className="ticket-row-label" style={{ paddingTop: 10 }}>
            Description
          </div>
          <div
            style={{
              padding: "12px 14px",
              background: "var(--surface-2)",
              borderRadius: 8,
              fontSize: 12.5,
              lineHeight: 1.55,
              color: "var(--ink-2)",
              maxHeight: 180,
              overflowY: "auto",
            }}
          >
            <strong>From Sprntly PRD-042</strong>
            <br /><br />
            <strong>Problem:</strong> New Android users outside the US drop off
            at 43% at phone verification. ~2,100 users/week affected. ~$14.2K
            MRR at risk.
            <br /><br />
            <strong>Solution:</strong> Tiered delivery — regional Twilio senders
            → WhatsApp fallback at 20s → email fallback at 40s, with real-time
            UX status.
            <br /><br />
            <strong>Acceptance:</strong> Non-US Android verification ≥75% within
            30 days. Support tickets drop ≥60%.
            <br /><br />
            <strong>Full PRD + evidence:</strong>{" "}
            <span style={{ color: "var(--accent)" }}>sprntly.ai/prd/042</span>
          </div>
        </div>

        <div
          style={{
            padding: "10px 12px",
            background: "var(--accent-soft)",
            borderRadius: 8,
            fontSize: 12,
            color: "var(--accent-ink)",
            marginTop: 14,
          }}
        >
          <strong>Linked back to Sprntly:</strong> We'll track this ticket's
          status and fold its impact into your Shipped ledger automatically when
          it's closed.
        </div>
      </div>

      <div className="drawer-foot">
        <span style={{ fontSize: 11.5, color: "var(--muted)" }}>
          Will create in Linear · Sprntly · Growth
        </span>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn" onClick={onClose}>Cancel</button>
          <button type="button" className="btn btn-accent" onClick={handleCreate}>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
              <IconCheck size={16} />
              Create ticket
            </span>
          </button>
        </div>
      </div>
    </>
  )
}

// ── Jira ticket form (real create + per-ticket assignee) ───────────────────
//
// When Jira is connected, the drawer creates a REAL issue via
// ticketPushApi.pushToJira (the #701 per-task path with assignee_account_id),
// with a project picker + a member-picker assignee — replacing the old
// hardcoded mock (ConnectedTicketForm), which is kept only for the not-yet-real
// trackers (Linear/Asana).

function JiraTicketForm({ onClose }: { onClose: () => void }) {
  const { showToast, goTo } = useNavigation()
  const { content } = useContent()
  const [projects, setProjects] = useState<{ id: string; key: string; name: string }[]>([])
  const [projectKey, setProjectKey] = useState("")
  const [issueType, setIssueType] = useState("Task")
  const [members, setMembers] = useState<{ accountId: string; displayName: string | null; email: string | null }[]>([])
  const [assigneeAccountId, setAssigneeAccountId] = useState("")
  const [title, setTitle] = useState(content.prd?.title ?? "")
  const [priority, setPriority] = useState<"P0" | "P1" | "P2" | "P3">("P1")
  const [description, setDescription] = useState(() => prdDescription(content.prd))
  const [creating, setCreating] = useState(false)
  const [projectsState, setProjectsState] = useState<"loading" | "idle" | "error">("loading")

  // Load the Jira projects once (target picker).
  useEffect(() => {
    let cancelled = false
    import("../../lib/api").then(({ ticketPushApi }) => {
      ticketPushApi.listJiraProjects()
        .then((r) => {
          if (cancelled) return
          setProjects(r.projects)
          setProjectKey(r.projects[0]?.key ?? "")
          setProjectsState("idle")
        })
        .catch(() => { if (!cancelled) setProjectsState("error") })
    })
    return () => { cancelled = true }
  }, [])

  // Load assignable members whenever the project changes (project-scoped).
  useEffect(() => {
    if (!projectKey) return
    let cancelled = false
    setMembers([])
    setAssigneeAccountId("")
    import("../../lib/api").then(({ ticketPushApi }) => {
      ticketPushApi.listJiraMembers(projectKey)
        .then((r) => { if (!cancelled) setMembers(r.members) })
        .catch(() => { if (!cancelled) setMembers([]) })
    })
    return () => { cancelled = true }
  }, [projectKey])

  const handleCreate = async () => {
    if (!projectKey) return
    setCreating(true)
    // Save internally too (parity with the ClickUp path) so the ticket shows in
    // the Tickets screen and can be tracked back.
    const assigneeName = members.find((m) => m.accountId === assigneeAccountId)?.displayName ?? ""
    const ticket = saveTicket({ title, priority, category: "Product", assignee: assigneeName, description })
    try {
      const { ticketPushApi } = await import("../../lib/api")
      const result = await ticketPushApi.pushToJira(projectKey, [{
        task_id: ticket.id,
        title: ticket.title,
        description: ticket.description,
        priority: ticket.priority,
        assignee_account_id: assigneeAccountId || null,
      }], issueType)
      onClose()
      if (result.created?.length > 0) {
        const issue = result.created[0]
        showToast(
          `Ticket created in Jira · ${issue.jira_issue_key}`,
          `"${title.slice(0, 50)}" pushed to ${projectKey}.`,
          issue.url || "View tickets →",
        )
      } else {
        const err = result.errors?.[0]?.error ?? "saved locally"
        showToast("Jira push failed", `"${title.slice(0, 50)}" — ${err.slice(0, 80)}`, "View tickets →")
      }
      goTo("tickets")
    } catch (e) {
      onClose()
      const msg = e instanceof Error ? e.message : "Unknown error"
      showToast("Jira push failed", `"${title.slice(0, 50)}" saved locally — ${msg.slice(0, 80)}`, "View tickets →")
      goTo("tickets")
    } finally {
      setCreating(false)
    }
  }

  return (
    <>
      <div className="drawer-body">
        <p className="drawer-sub">
          Jira is connected. This creates a real issue in your chosen project — the PRD summary travels in the description.
        </p>

        <div className="ticket-row">
          <div className="ticket-row-label">Project</div>
          <select className="ticket-select" value={projectKey} onChange={(e) => setProjectKey(e.target.value)} disabled={creating || projectsState !== "idle"}>
            {projectsState === "loading" && <option>Loading…</option>}
            {projectsState === "error" && <option>Couldn’t load projects</option>}
            {projects.map((p) => <option key={p.id || p.key} value={p.key}>{p.name} ({p.key})</option>)}
          </select>
        </div>

        <div className="ticket-row">
          <div className="ticket-row-label">Issue type</div>
          <select className="ticket-select" value={issueType} onChange={(e) => setIssueType(e.target.value)} disabled={creating}>
            {["Task", "Story", "Bug", "Epic"].map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>

        <div className="ticket-row">
          <div className="ticket-row-label">Title</div>
          <input type="text" className="input" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Ticket title" />
        </div>

        <div className="ticket-row">
          <div className="ticket-row-label">Priority</div>
          <select className="ticket-select" value={priority} onChange={(e) => setPriority(e.target.value as typeof priority)} disabled={creating}>
            <option value="P0">P0 — Highest</option>
            <option value="P1">P1 — High</option>
            <option value="P2">P2 — Medium</option>
            <option value="P3">P3 — Low</option>
          </select>
        </div>

        <div className="ticket-row">
          <div className="ticket-row-label">Assignee</div>
          <select className="ticket-select" value={assigneeAccountId} onChange={(e) => setAssigneeAccountId(e.target.value)} disabled={creating || !projectKey} aria-label="Assignee">
            <option value="">Unassigned</option>
            {members.map((m) => <option key={m.accountId} value={m.accountId}>{m.displayName || m.email || m.accountId}</option>)}
          </select>
        </div>

        <div className="ticket-row" style={{ gridTemplateColumns: "110px 1fr", alignItems: "flex-start" }}>
          <div className="ticket-row-label" style={{ paddingTop: 10 }}>Description</div>
          <textarea className="textarea" value={description} onChange={(e) => setDescription(e.target.value)} style={{ minHeight: 140, fontSize: 12.5 }} />
        </div>
      </div>

      <div className="drawer-foot">
        <span style={{ fontSize: 11.5, color: "var(--muted)" }}>
          {projectKey ? `Will create in Jira · ${projectKey}` : "Select a Jira project"}
        </span>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn" onClick={onClose}>Cancel</button>
          <button type="button" className="btn btn-accent" onClick={handleCreate} disabled={!title.trim() || !projectKey || creating}>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
              <IconCheck size={16} />
              {creating ? "Creating…" : "Create & push"}
            </span>
          </button>
        </div>
      </div>
    </>
  )
}

// ── Main drawer ────────────────────────────────────────────────────────────

export function TicketDrawer() {
  const { activeDrawer, closeDrawers } = useNavigation()
  const [connections, setConnections] = useState<ConnectionSummary[] | null>(null)

  useEffect(() => {
    if (activeDrawer !== "ticket") return
    connectorsApi.list().then((r) => setConnections(r.connections)).catch(() => setConnections([]))
  }, [activeDrawer])

  if (activeDrawer !== "ticket") return null

  const jiraConnected =
    connections !== null && connections.some((c) => c.status === "active" && c.provider === "jira")
  // Jira has a real create path now; other TICKET_PROVIDERS (Linear/Asana) are
  // still the design mock until their connectors exist.
  const connected = connections !== null && hasTicketConnector(connections)

  return (
    <>
      <div className="drawer-overlay open" onClick={closeDrawers} />
      <aside className="drawer open">
        <div className="drawer-head">
          <h3 className="drawer-title">
            <span className="drawer-icon">J</span>
            {connected ? "Create ticket" : "Create ticket"}
          </h3>
          <button type="button" className="drawer-close" onClick={closeDrawers} aria-label="Close">
            <IconClose size={18} />
          </button>
        </div>

        {connections === null ? (
          <div className="drawer-body" style={{ color: "var(--ink-4)", fontSize: 13 }}>
            Loading…
          </div>
        ) : jiraConnected ? (
          <JiraTicketForm onClose={closeDrawers} />
        ) : connected ? (
          <ConnectedTicketForm connections={connections} onClose={closeDrawers} />
        ) : (
          <InternalTicketForm onClose={closeDrawers} />
        )}
      </aside>
    </>
  )
}

function AssigneeChip({
  id,
  name,
  color,
  selected,
  onClick,
}: {
  id: string
  name: string
  color?: string
  selected: boolean
  onClick: () => void
}) {
  return (
    <div
      className={`ticket-assignee-chip ${selected ? "selected" : ""}`}
      onClick={onClick}
    >
      <span className="mini-av" style={color ? { background: color } : undefined}>
        {id}
      </span>
      {name}
    </div>
  )
}
