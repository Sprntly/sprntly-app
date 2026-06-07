"use client"

import { useState, useEffect } from "react"
import { AppLayout } from "./AppLayout"
import { EmptyPane } from "../../shared/EmptyPane"

export type InternalTicket = {
  id: string
  title: string
  priority: "Urgent" | "High" | "Medium" | "Low"
  assignee: string
  description: string
  status: "To Do" | "In Progress" | "Done"
  createdAt: string
}

export const TICKETS_KEY = "sprntly_internal_tickets"

export function loadTickets(): InternalTicket[] {
  try {
    return JSON.parse(localStorage.getItem(TICKETS_KEY) ?? "[]")
  } catch {
    return []
  }
}

export function saveTicket(ticket: Omit<InternalTicket, "id" | "createdAt" | "status">): InternalTicket {
  const tickets = loadTickets()
  const next: InternalTicket = {
    ...ticket,
    id: `SPR-${Math.floor(Math.random() * 900) + 100}`,
    status: "To Do",
    createdAt: new Date().toISOString(),
  }
  localStorage.setItem(TICKETS_KEY, JSON.stringify([next, ...tickets]))
  return next
}

const PRIORITY_COLOR: Record<InternalTicket["priority"], string> = {
  Urgent: "var(--danger)",
  High:   "#D97706",
  Medium: "var(--accent)",
  Low:    "var(--ink-4)",
}

const STATUS_OPTIONS: InternalTicket["status"][] = ["To Do", "In Progress", "Done"]

function TicketRow({
  ticket,
  onChange,
  onDelete,
}: {
  ticket: InternalTicket
  onChange: (id: string, status: InternalTicket["status"]) => void
  onDelete: (id: string) => void
}) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "80px 1fr 80px 120px 100px 36px",
        alignItems: "center",
        gap: 12,
        padding: "10px 16px",
        borderBottom: "1px solid var(--line)",
        fontSize: 13,
      }}
    >
      <span style={{ color: "var(--ink-3)", fontFamily: "var(--font-mono)", fontSize: 11.5 }}>
        {ticket.id}
      </span>
      <div>
        <div style={{ fontWeight: 500, color: "var(--ink)" }}>{ticket.title}</div>
        {ticket.assignee && (
          <div style={{ fontSize: 11.5, color: "var(--ink-4)", marginTop: 2 }}>
            {ticket.assignee}
          </div>
        )}
      </div>
      <span
        style={{
          fontSize: 11.5,
          fontWeight: 600,
          color: PRIORITY_COLOR[ticket.priority],
        }}
      >
        {ticket.priority}
      </span>
      <select
        value={ticket.status}
        onChange={(e) => onChange(ticket.id, e.target.value as InternalTicket["status"])}
        style={{
          fontSize: 11.5,
          padding: "3px 6px",
          borderRadius: 6,
          border: "1px solid var(--line-strong)",
          background: "var(--surface-2)",
          color: "var(--ink-2)",
          cursor: "pointer",
        }}
      >
        {STATUS_OPTIONS.map((s) => (
          <option key={s}>{s}</option>
        ))}
      </select>
      <span style={{ fontSize: 11, color: "var(--ink-4)" }}>
        {new Date(ticket.createdAt).toLocaleDateString()}
      </span>
      <button
        type="button"
        onClick={() => onDelete(ticket.id)}
        style={{
          background: "none",
          border: "none",
          color: "var(--ink-4)",
          cursor: "pointer",
          fontSize: 16,
          lineHeight: 1,
          padding: 0,
        }}
        title="Delete ticket"
      >
        ×
      </button>
    </div>
  )
}

export function TicketsScreen() {
  const [tickets, setTickets] = useState<InternalTicket[]>([])

  useEffect(() => {
    setTickets(loadTickets())
  }, [])

  const handleStatusChange = (id: string, status: InternalTicket["status"]) => {
    const updated = tickets.map((t) => (t.id === id ? { ...t, status } : t))
    setTickets(updated)
    localStorage.setItem(TICKETS_KEY, JSON.stringify(updated))
  }

  const handleDelete = (id: string) => {
    const updated = tickets.filter((t) => t.id !== id)
    setTickets(updated)
    localStorage.setItem(TICKETS_KEY, JSON.stringify(updated))
  }

  const counts = {
    "To Do": tickets.filter((t) => t.status === "To Do").length,
    "In Progress": tickets.filter((t) => t.status === "In Progress").length,
    "Done": tickets.filter((t) => t.status === "Done").length,
  }

  return (
    <AppLayout>
      <div className="main-header">
        <div>
          <h1 className="main-title">Tickets</h1>
          <p className="main-sub">
            Internal tickets created from PRDs. Connect Linear, Jira, or Asana in Settings to push them externally.
          </p>
        </div>
        <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
          {Object.entries(counts).map(([label, count]) => (
            <div key={label} style={{ textAlign: "center" }}>
              <div style={{ fontSize: 18, fontWeight: 700, color: "var(--ink)" }}>{count}</div>
              <div style={{ fontSize: 11, color: "var(--ink-4)" }}>{label}</div>
            </div>
          ))}
        </div>
      </div>

      {tickets.length === 0 ? (
        <EmptyPane
          title="No tickets yet"
          hint="Approve a PRD and choose 'Create a ticket' to add internal tickets here. They appear instantly and can be pushed to Linear, Jira, or Asana once connected."
          placeholders={3}
        />
      ) : (
        <div
          style={{
            borderRadius: 10,
            border: "1px solid var(--line)",
            overflow: "hidden",
            background: "var(--surface)",
          }}
        >
          {/* Header row */}
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "80px 1fr 80px 120px 100px 36px",
              gap: 12,
              padding: "8px 16px",
              background: "var(--surface-2)",
              fontSize: 11,
              fontWeight: 600,
              color: "var(--ink-4)",
              textTransform: "uppercase",
              letterSpacing: "0.04em",
              borderBottom: "1px solid var(--line)",
            }}
          >
            <span>ID</span>
            <span>Title</span>
            <span>Priority</span>
            <span>Status</span>
            <span>Created</span>
            <span />
          </div>

          {tickets.map((ticket) => (
            <TicketRow
              key={ticket.id}
              ticket={ticket}
              onChange={handleStatusChange}
              onDelete={handleDelete}
            />
          ))}
        </div>
      )}
    </AppLayout>
  )
}
