"use client"

import { useState, useEffect } from "react"
import { useNavigation } from "../../context/NavigationContext"
import { useContent } from "../../context/ContentContext"
import { connectorsApi, type ConnectionSummary } from "../../lib/api"
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

  const handleCreate = () => {
    const ticket = saveTicket({ title, priority, category, assignee, description })
    onClose()
    showToast(
      `Internal ticket created · ${ticket.id}`,
      `"${title.slice(0, 60) || "Untitled"}" saved to Tickets.`,
      "View tickets →"
    )
    goTo("tickets")
  }

  return (
    <>
      <div className="drawer-body">
        <p className="drawer-sub">
          No ticket tracker connected. This ticket will be saved internally in
          Sprntly. Connect Linear, Jira, or Asana in{" "}
          <span style={{ color: "var(--accent)" }}>Settings → Connectors</span>{" "}
          to push it externally.
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

      <div className="drawer-foot">
        <span style={{ fontSize: 11.5, color: "var(--muted)" }}>
          Saved internally in Sprntly
        </span>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn" onClick={onClose}>Cancel</button>
          <button
            type="button"
            className="btn btn-accent"
            onClick={handleCreate}
            disabled={!title.trim()}
          >
            <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
              <IconCheck size={16} />
              Save ticket
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

// ── Main drawer ────────────────────────────────────────────────────────────

export function TicketDrawer() {
  const { activeDrawer, closeDrawers } = useNavigation()
  const [connections, setConnections] = useState<ConnectionSummary[] | null>(null)

  useEffect(() => {
    if (activeDrawer !== "ticket") return
    connectorsApi.list().then((r) => setConnections(r.connections)).catch(() => setConnections([]))
  }, [activeDrawer])

  if (activeDrawer !== "ticket") return null

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
