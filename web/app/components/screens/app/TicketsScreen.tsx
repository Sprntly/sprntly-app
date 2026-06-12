"use client"

import { useState, useEffect, useCallback } from "react"
import { AppLayout } from "./AppLayout"
import { IconSortAscendingLetters, IconAdjustments, IconFilter, IconPlus, IconUpload, IconSparkles } from "@tabler/icons-react"
import { EmptyPane } from "../../shared/EmptyPane"
import { ticketPushApi, type ClickUpList } from "../../../lib/api"

export type InternalTicket = {
  id: string
  title: string
  priority: "P0" | "P1" | "P2" | "P3"
  assignee: string
  description: string
  status: "Backlog" | "In progress" | "In review" | "Done"
  category: string
  createdAt: string
  note?: string
  tag?: string
  comments?: { author: string; text: string; time: string }[]
}

export const TICKETS_KEY = "sprntly_internal_tickets"

export function loadTickets(): InternalTicket[] {
  try {
    const raw = JSON.parse(localStorage.getItem(TICKETS_KEY) ?? "[]") as any[]
    return raw.map((t) => ({
      ...t,
      status: ({ "To Do": "Backlog", "In Progress": "In progress" } as Record<string, string>)[t.status] ?? t.status ?? "Backlog",
      priority: ({ Urgent: "P0", High: "P1", Medium: "P2", Low: "P3" } as Record<string, string>)[t.priority] ?? t.priority ?? "P1",
      category: t.category || "Product",
      comments: t.comments || [],
    }))
  } catch {
    return []
  }
}

export function saveTicket(ticket: Omit<InternalTicket, "id" | "createdAt" | "status">): InternalTicket {
  const tickets = loadTickets()
  const next: InternalTicket = {
    ...ticket,
    id: `SPR-${Math.floor(Math.random() * 900) + 100}`,
    status: "Backlog",
    createdAt: new Date().toISOString(),
    comments: [],
  }
  localStorage.setItem(TICKETS_KEY, JSON.stringify([next, ...tickets]))
  return next
}

// ── Constants ──

const COLUMNS: InternalTicket["status"][] = ["Backlog", "In progress", "In review", "Done"]
const CATEGORIES = ["Product", "Design", "Backend", "Frontend", "AI", "Infra", "Analytics", "CS"]

const COL_DOT: Record<string, string> = {
  Backlog: "#AAB3AE", "In progress": "#2a6ec8", "In review": "#c16a0b", Done: "#179463",
}

const PRIORITY_LABELS: Record<string, string> = {
  P0: "P0 — Critical", P1: "P1 — High", P2: "P2 — Medium", P3: "P3 — Low",
}

const PRIORITY_STYLE: Record<string, { bg: string; color: string }> = {
  P0: { bg: "#FEE2E2", color: "#DC2626" }, P1: { bg: "#FEF3C7", color: "#D97706" },
  P2: { bg: "#DBEAFE", color: "#2563EB" }, P3: { bg: "#F3F4F6", color: "#6B7280" },
}

const CAT_STYLE: Record<string, { bg: string; color: string; bordercolor: string }> = {
  Product: { bg: "#DBF1E7", color: "#0E6E49", bordercolor: "#9BDcc1" }, Design: { bg: "#DBF1E7", color: "#0E6E49", bordercolor: "#9BDcc1" },
  Backend: { bg: "#D1FAE5", color: "#065F46", bordercolor: "#9BDcc1" }, Frontend: { bg: "#EDE9FE", color: "#6D28D9", bordercolor: "#BDABE0" },
  AI: { bg: "#FEF3C7", color: "#92400E", bordercolor: "#F0BF73" }, Infra: { bg: "#DBEAFE", color: "#1E40AF", bordercolor: "#9BDcc1" },
  Analytics: { bg: "#E0E7FF", color: "#3730A3", bordercolor: "#BDABE0" }, CS: { bg: "#DBEAFE", color: "#1E40AF", bordercolor: "#9CBDEA" },
}

const TAG_STYLE: Record<string, { bg: string; color: string }> = {
  Blocker: { bg: "#FEE2E2", color: "#DC2626" }, Stale: { bg: "#FEF3C7", color: "#D97706" },
}

function initials(name: string): string {
  if (!name) return ""
  return name.split(" ").map((w) => w[0]).join("").toUpperCase().slice(0, 2)
}

const selectStyle: React.CSSProperties = {
  fontSize: 12.5, padding: "3px 10px", borderRadius: 6,
  border: "1px solid var(--line-strong, #D5D3CC)", background: "#fff", cursor: "pointer",
}

const inputStyle: React.CSSProperties = {
  fontSize: 12.5, padding: "6px 10px", borderRadius: 6, width: "100%",
  border: "1px solid var(--line, #E8E6E0)", outline: "none",
}

const toolBtnStyle: React.CSSProperties = {
  fontSize: 12, padding: "5px 10px", background: "var(--surface-2, #F4F1EA)",
  border: "1px solid var(--line, #E8E6E0)", borderRadius: 17, color: "var(--ink-2, #5A5853)", cursor: "pointer", display: "flex", alignItems: "center", gap: 4,
}

// ── Add Ticket Modal ──

function AddTicketModal({ onClose, onAdd }: { onClose: () => void; onAdd: (t: InternalTicket) => void }) {
  const [title, setTitle] = useState("")
  const [priority, setPriority] = useState<InternalTicket["priority"]>("P1")
  const [category, setCategory] = useState("Product")
  const [assignee, setAssignee] = useState("")
  const [description, setDescription] = useState("")
  const [pushClickUp, setPushClickUp] = useState(false)
  const [clickUpOk, setClickUpOk] = useState(false)
  const [busy, setBusy] = useState(false)

  // Check ClickUp connection on mount
  useEffect(() => {
    import("../../../lib/api").then(({ connectorsApi }) => {
      connectorsApi.list().then((conns) => {
        const list = Array.isArray(conns) ? conns : (conns as unknown as { connections?: any[] }).connections ?? []
        if (list.find((c: any) => c.provider === "clickup")) {
          setClickUpOk(true); setPushClickUp(true)
        }
      }).catch(() => {})
    })
  }, [])

  const handleSubmit = async () => {
    if (!title.trim()) return
    setBusy(true)
    const ticket = saveTicket({ title: title.trim(), priority, category, assignee: assignee.trim(), description: description.trim() })
    onAdd(ticket)

    // Auto-push to ClickUp if enabled
    if (pushClickUp && clickUpOk) {
      try {
        const listsRes = await ticketPushApi.listClickUpLists()
        if (listsRes.lists.length > 0) {
          await ticketPushApi.pushToClickUp(listsRes.lists[0].id, [{
            title: ticket.title, description: ticket.description, priority: ticket.priority,
          }])
        }
      } catch { /* silent — saved internally */ }
    }
    setBusy(false)
    onClose()
  }

  return (
    <>
      <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.35)", zIndex: 1000 }} />
      <div style={{
        position: "fixed", top: "50%", left: "50%", transform: "translate(-50%,-50%)", zIndex: 1001,
        background: "var(--surface, #fff)", borderRadius: 14, width: 480, maxHeight: "85vh", overflow: "auto",
        boxShadow: "0 20px 60px rgba(0,0,0,0.18)", border: "1px solid var(--line, #E8E6E0)",
      }}>
        <div style={{ padding: "18px 22px 0", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <span style={{ fontSize: 15, fontWeight: 600, color: "var(--ink, #1A1A17)" }}>New ticket</span>
          <button type="button" onClick={onClose} style={{ background: "none", border: "none", fontSize: 20, color: "var(--ink-3)", cursor: "pointer", padding: 0, lineHeight: 1 }}>×</button>
        </div>
        <div style={{ padding: "16px 22px", display: "flex", flexDirection: "column", gap: 14 }}>
          <div>
            <div style={{ fontSize: 11.5, fontWeight: 600, color: "var(--ink-3, #8C8A84)", textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: 5 }}>Title *</div>
            <input style={inputStyle} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Ticket title" autoFocus />
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            <div>
              <div style={{ fontSize: 11.5, fontWeight: 600, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: 5 }}>Priority</div>
              <select style={{ ...selectStyle, width: "100%" }} value={priority} onChange={(e) => setPriority(e.target.value as InternalTicket["priority"])}>
                {Object.entries(PRIORITY_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </div>
            <div>
              <div style={{ fontSize: 11.5, fontWeight: 600, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: 5 }}>Category</div>
              <select style={{ ...selectStyle, width: "100%" }} value={category} onChange={(e) => setCategory(e.target.value)}>
                {CATEGORIES.map((c) => <option key={c}>{c}</option>)}
              </select>
            </div>
          </div>
          <div>
            <div style={{ fontSize: 11.5, fontWeight: 600, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: 5 }}>Assignee</div>
            <input style={inputStyle} value={assignee} onChange={(e) => setAssignee(e.target.value)} placeholder="Name" />
          </div>
          <div>
            <div style={{ fontSize: 11.5, fontWeight: 600, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: 5 }}>Description</div>
            <textarea style={{ ...inputStyle, minHeight: 90, resize: "vertical", fontFamily: "inherit" }} value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What needs to be done?" />
          </div>
        </div>
        {clickUpOk && (
          <div style={{ padding: "0 22px 10px", display: "flex", alignItems: "center", gap: 8 }}>
            <input type="checkbox" id="modal-push-cu" checked={pushClickUp} onChange={(e) => setPushClickUp(e.target.checked)} style={{ accentColor: "var(--accent)" }} />
            <label htmlFor="modal-push-cu" style={{ fontSize: 12.5, color: "var(--ink-2)", cursor: "pointer" }}>Also push to ClickUp</label>
          </div>
        )}
        <div style={{ padding: "0 22px 18px", display: "flex", justifyContent: "flex-end", gap: 8 }}>
          <button type="button" onClick={onClose} style={{ ...toolBtnStyle, padding: "7px 16px" }}>Cancel</button>
          <button type="button" onClick={handleSubmit} disabled={!title.trim() || busy} style={{
            fontSize: 12, padding: "7px 16px", background: title.trim() && !busy ? "var(--accent, #179463)" : "#ccc", color: "#fff",
            border: "none", borderRadius: 7, fontWeight: 600, cursor: title.trim() && !busy ? "pointer" : "not-allowed",
          }}>{busy ? "Creating..." : pushClickUp ? "Create & push to ClickUp" : "Create ticket"}</button>
        </div>
      </div>
    </>
  )
}

// ── Card ──

function KanbanCard({ ticket, onDragStart, onClick, isDone, isSelected }: {
  ticket: InternalTicket; onDragStart: (e: React.DragEvent, id: string) => void
  onClick: (t: InternalTicket) => void; isDone: boolean; isSelected: boolean
}) {
  const pri = PRIORITY_STYLE[ticket.priority] ?? PRIORITY_STYLE.P2
  const cat = CAT_STYLE[ticket.category] ?? CAT_STYLE.Product
  const tag = ticket.tag ? TAG_STYLE[ticket.tag] : null

  return (
    <div draggable onDragStart={(e) => onDragStart(e, ticket.id)} onClick={() => onClick(ticket)}
      style={{
        background: "#fff", borderRadius: 10,
        border: isSelected ? "2px solid var(--accent, #179463)" : "1px solid var(--line, #E8E6E0)",
        padding: isSelected ? "13px 15px" : "14px 15px", cursor: "pointer",
        opacity: isDone ? 0.55 : 1, transition: "box-shadow 0.15s, opacity 0.2s, border 0.15s",
      }}
      onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.border = "1px solid #9BDcc1" }}
      onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.border = "1px solid transparent" }}
    >
      <div style={{ fontSize: 13.5, fontWeight: 500, color: "var(--ink, #1A1A17)", lineHeight: 1.45, marginBottom: 10 }}>{ticket.title}</div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span style={{ display: "inline-block", fontSize: 11, fontWeight: 400, padding: "2px 10px", borderRadius: 30, background: cat.bg, color: cat.color }}>{ticket.category}</span>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {!isDone && <span style={{ width: 8, height: 8, borderRadius: "50%", background: pri.color, flexShrink: 0 }} />}
          {ticket.assignee && (
            <span style={{ width: 23, height: 23, borderRadius: "50%", fontSize: 10, fontWeight: 600, display: "flex", alignItems: "center", justifyContent: "center", background: cat.bg, border: 1, borderStyle: "solid", borderColor: cat.bordercolor, color: cat.color, flexShrink: 0 }}>{initials(ticket.assignee)}</span>
          )}
        </div>
      </div>
      {ticket.note && (
        <div style={{ marginTop: 10, display: "flex", alignItems: "center", gap: 6, fontSize: 11.5, color: "var(--ink-3, #8C8A84)" }}>
          <span style={{ fontSize: 13 }}>☐</span><span>{ticket.note}</span>
          {tag && <span style={{ marginLeft: "auto", fontSize: 10.5, fontWeight: 600, padding: "2px 7px", borderRadius: 5, background: tag.bg, color: tag.color }}>{ticket.tag}</span>}
        </div>
      )}
    </div>
  )
}

// ── Column ──

function KanbanColumn({ status, tickets, onDragStart, onDrop, onCardClick, selectedId }: {
  status: InternalTicket["status"]; tickets: InternalTicket[]
  onDragStart: (e: React.DragEvent, id: string) => void
  onDrop: (status: InternalTicket["status"]) => void
  onCardClick: (t: InternalTicket) => void; selectedId: string | null
}) {
  const [dragOver, setDragOver] = useState(false)
  const isDone = status === "Done"

  return (
    <div style={{ flex: 1, minWidth: 220 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14, paddingLeft: 2 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ width: 8, height: 8, borderRadius: "50%", background: COL_DOT[status], flexShrink: 0 }} />
          <span style={{ fontSize: "12.5px", fontWeight: 500, color: "var(--ink, #1A1A17)" }}>{status}</span>
        </div>
        <span style={{ padding: "0px 8px", fontSize: 11, background: "#EEF0EE", color: "var(--ink-4, #82D887)", borderRadius: 30, fontWeight: 400 }}>{tickets.length}</span>
      </div>
      <div
        onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => { e.preventDefault(); setDragOver(false); onDrop(status) }}
        style={{
          display: "flex", flexDirection: "column", gap: 10, minHeight: 120, padding: 4, borderRadius: 12,
          background: dragOver ? "rgba(23,148,99,0.04)" : "transparent",
          border: dragOver ? "2px dashed var(--accent, #179463)" : "2px dashed transparent",
          transition: "background 0.15s, border 0.15s",
        }}
      >
        {tickets.map((t) => (
          <KanbanCard key={t.id} ticket={t} onDragStart={onDragStart} onClick={onCardClick} isDone={isDone} isSelected={t.id === selectedId} />
        ))}
      </div>
    </div>
  )
}

// ── Detail Panel ──

function TicketDetailPanel({ ticket, onClose, onUpdate, onDelete }: {
  ticket: InternalTicket; onClose: () => void
  onUpdate: (updated: InternalTicket) => void; onDelete: (id: string) => void
}) {
  const [editTitle, setEditTitle] = useState(ticket.title)
  const [editAssignee, setEditAssignee] = useState(ticket.assignee)
  const [editDesc, setEditDesc] = useState(ticket.description)
  const [editCategory, setEditCategory] = useState(ticket.category)
  const [commentText, setCommentText] = useState("")

  // ── ClickUp push state ──
  const [showListPicker, setShowListPicker] = useState(false)
  const [clickUpLists, setClickUpLists] = useState<ClickUpList[]>([])
  const [listsLoading, setListsLoading] = useState(false)
  const [listsError, setListsError] = useState<string | null>(null)
  const [pushStatus, setPushStatus] = useState<"idle" | "pushing" | "done" | "error">("idle")
  const [pushMessage, setPushMessage] = useState<string | null>(null)

  const handleOpenListPicker = useCallback(async () => {
    if (showListPicker) { setShowListPicker(false); return }
    setShowListPicker(true)
    setListsLoading(true)
    setListsError(null)
    try {
      const res = await ticketPushApi.listClickUpLists()
      setClickUpLists(res.lists)
    } catch (err: any) {
      setListsError(err?.status === 404 ? "ClickUp is not connected. Connect it in Settings." : (err?.message || "Failed to load lists"))
    } finally {
      setListsLoading(false)
    }
  }, [showListPicker])

  const handlePushToClickUp = useCallback(async (listId: string) => {
    setShowListPicker(false)
    setPushStatus("pushing")
    setPushMessage(null)
    try {
      const res = await ticketPushApi.pushToClickUp(listId, [{
        title: ticket.title,
        description: ticket.description,
        priority: ticket.priority,
      }])
      if (res.created.length > 0) {
        setPushStatus("done")
        setPushMessage(`Pushed to ClickUp`)
      } else {
        setPushStatus("error")
        setPushMessage(res.errors[0]?.error || "Push failed")
      }
    } catch (err: any) {
      setPushStatus("error")
      setPushMessage(err?.message || "Push failed")
    }
  }, [ticket])

  // Sync local state when ticket changes
  useEffect(() => {
    setEditTitle(ticket.title)
    setEditAssignee(ticket.assignee)
    setEditDesc(ticket.description)
    setEditCategory(ticket.category)
  }, [ticket.id, ticket.title, ticket.assignee, ticket.description, ticket.category])

  const cat = CAT_STYLE[editCategory] ?? CAT_STYLE.Product
  const comments = ticket.comments ?? []

  const commitField = (patch: Partial<InternalTicket>) => onUpdate({ ...ticket, ...patch })

  const addComment = () => {
    if (!commentText.trim()) return
    const c = { author: "You", text: commentText.trim(), time: new Date().toLocaleString() }
    commitField({ comments: [...comments, c] })
    setCommentText("")
  }

  const field = (label: string, content: React.ReactNode) => (
    <div style={{ display: "flex", alignItems: "center", padding: "10px 0", borderBottom: "1px solid var(--line, #E8E6E0)" }}>
      <span style={{ width: 130, fontSize: 12, color: "var(--ink-3, #8C8A84)", fontWeight: 500, textTransform: "uppercase", letterSpacing: "0.04em", flexShrink: 0 }}>{label}</span>
      <div style={{ flex: 1 }}>{content}</div>
    </div>
  )

  return (
    <div style={{
      width: 675, flexShrink: 0, borderLeft: "1px solid var(--line, #E8E6E0)", background: "#fff",
      display: "flex", flexDirection: "column", height: "calc(100vh - 106px)", overflow: "auto",
    }}>
      {/* Header */}
      <div style={{ padding: "14px 20px", borderBottom: "1px solid var(--line, #E8E6E0)", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span style={{ fontSize: 13, fontWeight: 500, color: "var(--ink, #1A1A17)" }}>{ticket.id}</span>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <button type="button" onClick={() => { onDelete(ticket.id); onClose() }} style={{ fontSize: 11.5, padding: "4px 10px", borderRadius: 6, border: "1px solid #FCA5A5", background: "#FEF2F2", cursor: "pointer", color: "#DC2626" }}>Delete</button>
          <button type="button" onClick={onClose} style={{ background: "none", border: "none", cursor: "pointer", fontSize: 18, color: "var(--ink-3, #8C8A84)", padding: 0, lineHeight: 1 }}>×</button>
        </div>
      </div>

      {/* Body */}
      <div style={{ flex: 1, overflowY: "auto", padding: "0 20px 20px" }}>
        <div style={{ fontSize: 11.5, color: "var(--ink-4, #B0AEA6)", marginTop: 16 }}>
          {ticket.id} · Created {new Date(ticket.createdAt).toLocaleDateString()}
        </div>

        {/* Editable title */}
        <input value={editTitle}
          onChange={(e) => setEditTitle(e.target.value)}
          onBlur={() => { if (editTitle.trim() && editTitle !== ticket.title) commitField({ title: editTitle.trim() }) }}
          style={{ fontSize: 18, fontWeight: 600, color: "var(--ink, #1A1A17)", margin: "8px 0 16px", lineHeight: 1.35, border: "none", outline: "none", width: "100%", background: "transparent", padding: 0 }}
        />

        {/* AI Summary */}
        {ticket.description && (
          <div style={{ background: "#edf8f2", borderRadius: 10, padding: "14px 16px", marginBottom: 20, border: "1px solid var(--line, #E8E6E0)" }}>
            <div style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--accent, #179463)", marginBottom: 6 }}><IconSparkles/> AI Summary</div>
            <div style={{ fontSize: 12.5, color: "#4a554f", lineHeight: 1.55 }}>
              {ticket.description.slice(0, 200)}{ticket.description.length > 200 ? "..." : ""}
            </div>
          </div>
        )}

        {/* Fields */}
        {field("Status",
          <select value={ticket.status} onChange={(e) => commitField({ status: e.target.value as InternalTicket["status"] })} style={selectStyle}>
            {COLUMNS.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        )}

        {field("Priority",
          <select value={ticket.priority} onChange={(e) => commitField({ priority: e.target.value as InternalTicket["priority"] })} style={selectStyle}>
            {Object.entries(PRIORITY_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        )}

        {field("Owner",
          <input value={editAssignee} onChange={(e) => setEditAssignee(e.target.value)}
            onBlur={() => commitField({ assignee: editAssignee.trim() })}
            placeholder="Unassigned" style={{ ...inputStyle, maxWidth: 200 }} />
        )}

        {field("Category",
          <select value={editCategory} onChange={(e) => { setEditCategory(e.target.value); commitField({ category: e.target.value }) }} style={selectStyle}>
            {CATEGORIES.map((c) => <option key={c}>{c}</option>)}
          </select>
        )}

        {/* Editable description */}
        <div style={{ marginTop: 24 }}>
          <div style={{ fontSize: 12, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.04em", color: "var(--ink-3, #8C8A84)", marginBottom: 10 }}>Description</div>
          <textarea value={editDesc} onChange={(e) => setEditDesc(e.target.value)}
            onBlur={() => commitField({ description: editDesc })}
            style={{ ...inputStyle, minHeight: 100, resize: "vertical", fontFamily: "inherit", lineHeight: 1.65 }}
            placeholder="Add a description..." />
        </div>

        {/* Comments */}
        <div style={{ marginTop: 24 }}>
          <div style={{ fontSize: 12, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.04em", color: "var(--ink-3, #8C8A84)", marginBottom: 10 }}>
            Comments · {comments.length}
          </div>
          {comments.map((c, i) => (
            <div key={i} style={{ marginBottom: 12, padding: "10px 12px", borderRadius: 8, background: "var(--surface-2, #F4F1EA)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                <span style={{ width: 22, height: 22, borderRadius: "50%", fontSize: 9, fontWeight: 600, display: "flex", alignItems: "center", justifyContent: "center", background: cat.bg, color: cat.color }}>{initials(c.author)}</span>
                <strong style={{ fontSize: 12, color: "var(--ink, #1A1A17)" }}>{c.author}</strong>
                <span style={{ fontSize: 11, color: "var(--ink-4, #B0AEA6)" }}>{c.time}</span>
              </div>
              <div style={{ fontSize: 12.5, color: "var(--ink, #1A1A17)", lineHeight: 1.5 }}>{c.text}</div>
            </div>
          ))}
          <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
            <input value={commentText} onChange={(e) => setCommentText(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addComment() } }}
              placeholder="Add a comment..." style={{ ...inputStyle, flex: 1 }} />
            <button type="button" onClick={addComment} disabled={!commentText.trim()} style={{
              fontSize: 12, padding: "6px 14px", borderRadius: 7, border: "none", fontWeight: 600, cursor: commentText.trim() ? "pointer" : "not-allowed",
              background: commentText.trim() ? "var(--accent, #179463)" : "#ccc", color: "#fff",
            }}>Post</button>
          </div>
        </div>

        {/* Linked */}
        <div style={{ marginTop: 24 }}>
          <div style={{ fontSize: 12, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.04em", color: "var(--ink-3, #8C8A84)", marginBottom: 10 }}>Linked</div>
          <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5, color: "var(--ink-2, #5A5853)" }}>
            <span style={{ fontSize: 10.5, fontWeight: 600, padding: "2px 6px", borderRadius: 4, background: "var(--surface-2, #F4F1EA)", color: "var(--ink-3, #8C8A84)" }}>PRD</span>
            <span>Created from PRD</span>
          </div>
        </div>

        {/* Activity */}
        <div style={{ marginTop: 24 }}>
          <div style={{ fontSize: 12, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.04em", color: "var(--ink-3, #8C8A84)", marginBottom: 10 }}>Activity</div>
          <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5, color: "var(--ink-2, #5A5853)" }}>
            <span style={{ fontSize: 10.5, fontWeight: 600, padding: "2px 6px", borderRadius: 4, background: "var(--surface-2, #F4F1EA)", color: "var(--ink-3, #8C8A84)" }}>System</span>
            <span>Created — {new Date(ticket.createdAt).toLocaleString()}</span>
          </div>
        </div>
      </div>

      {/* Footer */}
      <div style={{ padding: "12px 20px", borderTop: "1px solid var(--line, #E8E6E0)", display: "flex", alignItems: "center", justifyContent: "space-between", fontSize: 12, color: "var(--ink-3, #8C8A84)", position: "relative" }}>
        <span style={{ color: "#15201b", fontWeight: 400, fontSize: 12 }}>
          {pushStatus === "done" && <span style={{ color: "var(--accent, #179463)", fontWeight: 500 }}>{pushMessage}</span>}
          {pushStatus === "error" && <span style={{ color: "#DC2626", fontWeight: 500 }}>{pushMessage}</span>}
          {pushStatus === "pushing" && <span style={{ fontWeight: 500 }}>Pushing...</span>}
          {pushStatus === "idle" && <><strong style={{ fontWeight: 500 }}>Ticket synced</strong> · PRD attached</>}
        </span>
        <div style={{ display: "flex", gap: 8, position: "relative" }}>
          <button type="button" onClick={handleOpenListPicker} disabled={pushStatus === "pushing"} style={{
            fontSize: 11.5, padding: "5px 12px", borderRadius: 30,
            border: "1px solid var(--line, #E8E6E0)", background: showListPicker ? "var(--surface-2, #F4F1EA)" : "var(--surface, #fff)",
            cursor: pushStatus === "pushing" ? "not-allowed" : "pointer", color: "var(--ink-2, #5A5853)",
          }}>Send to ClickUp</button>
          <button type="button" style={{ fontSize: 11.5, padding: "5px 12px", borderRadius: 30, background: "var(--accent, #179463)", color: "#fff", border: "none", cursor: "pointer", fontWeight: 400 }}>Send to Claude Code</button>

          {/* ClickUp list picker dropdown */}
          {showListPicker && (
            <div style={{
              position: "absolute", bottom: "100%", right: 0, marginBottom: 6,
              width: 300, maxHeight: 260, overflowY: "auto",
              background: "#fff", borderRadius: 10, border: "1px solid var(--line, #E8E6E0)",
              boxShadow: "0 8px 24px rgba(0,0,0,0.12)", zIndex: 100, padding: "6px 0",
            }}>
              <div style={{ padding: "8px 14px 6px", fontSize: 11, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.04em", color: "var(--ink-3, #8C8A84)" }}>
                Select ClickUp list
              </div>
              {listsLoading && (
                <div style={{ padding: "12px 14px", fontSize: 12, color: "var(--ink-3, #8C8A84)" }}>Loading lists...</div>
              )}
              {listsError && (
                <div style={{ padding: "12px 14px", fontSize: 12, color: "#DC2626" }}>{listsError}</div>
              )}
              {!listsLoading && !listsError && clickUpLists.length === 0 && (
                <div style={{ padding: "12px 14px", fontSize: 12, color: "var(--ink-3, #8C8A84)" }}>No lists found</div>
              )}
              {!listsLoading && clickUpLists.map((list) => (
                <button key={list.id} type="button" onClick={() => handlePushToClickUp(list.id)} style={{
                  display: "block", width: "100%", textAlign: "left", padding: "8px 14px",
                  background: "none", border: "none", cursor: "pointer", fontSize: 12.5,
                  color: "var(--ink, #1A1A17)", lineHeight: 1.4,
                }}
                  onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.background = "var(--surface-2, #F4F1EA)" }}
                  onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = "none" }}
                >
                  <div style={{ fontWeight: 500 }}>{list.name}</div>
                  {(list.space || list.folder) && (
                    <div style={{ fontSize: 11, color: "var(--ink-3, #8C8A84)", marginTop: 1 }}>
                      {[list.space, list.folder].filter(Boolean).join(" / ")}
                    </div>
                  )}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Screen ──

export function TicketsScreen() {
  const [tickets, setTickets] = useState<InternalTicket[]>([])
  const [dragId, setDragId] = useState<string | null>(null)
  const [selected, setSelected] = useState<InternalTicket | null>(null)
  const [showAdd, setShowAdd] = useState(false)

  useEffect(() => { setTickets(loadTickets()) }, [])

  const persist = useCallback((updated: InternalTicket[]) => {
    setTickets(updated)
    localStorage.setItem(TICKETS_KEY, JSON.stringify(updated))
  }, [])

  const handleDragStart = useCallback((_e: React.DragEvent, id: string) => setDragId(id), [])

  const handleDrop = useCallback((status: InternalTicket["status"]) => {
    if (!dragId) return
    const updated = tickets.map((t) => (t.id === dragId ? { ...t, status } : t))
    persist(updated)
    setDragId(null)
    if (selected?.id === dragId) setSelected((prev) => prev ? { ...prev, status } : null)
  }, [dragId, tickets, persist, selected])

  const handleCardClick = useCallback((t: InternalTicket) => setSelected(t), [])

  const handleUpdate = useCallback((updated: InternalTicket) => {
    setSelected(updated)
    persist(tickets.map((t) => (t.id === updated.id ? updated : t)))
  }, [tickets, persist])

  const handleDelete = useCallback((id: string) => {
    persist(tickets.filter((t) => t.id !== id))
    if (selected?.id === id) setSelected(null)
  }, [tickets, persist, selected])

  const handleAdd = useCallback((t: InternalTicket) => {
    setTickets((prev) => [t, ...prev])
  }, [])

  const total = tickets.length

  return (
    <AppLayout>
      {showAdd && <AddTicketModal onClose={() => setShowAdd(false)} onAdd={handleAdd} />}

      <div style={{ display: "flex", height: "100%", overflow: "hidden" }}>
        {/* Board */}
        <div style={{ flex: 1, overflow: "auto", padding: selected ? "0 16px 0 0" : 0 }}>
          {/* Top bar */}
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 18 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <span style={{ fontSize: 15, color: "var(--accent, #179463)" }}>📋</span>
              <span style={{ fontSize: 15, fontWeight: 600, color: "var(--ink, #1A1A17)" }}>Tickets</span>
              <span style={{ fontSize: 12, color: "var(--ink-4, #828D87)", fontWeight: 400 }}>{total} ticket{total !== 1 ? "s" : ""}</span>

            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <button type="button" style={toolBtnStyle}><IconUpload size={13} color="#5A5853" /> Import CSV</button>
              <button type="button" onClick={() => setShowAdd(true)} style={{
                fontSize: 12, padding: "5px 12px", background: "var(--accent, #179463)", color: "#fff", border: "none",
                borderRadius: 15, fontWeight: 400, display: "inline-flex", alignItems: "center", gap: 5, cursor: "pointer",
              }}><IconPlus size={13} color="#fff" /> Add ticket</button>
            </div>
          </div>

          {/* Toolbar */}
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 18 }}>
            <button type="button" style={toolBtnStyle}><IconFilter size={13} color="#5A5853" /> Filter</button>
            <div style={{ display: "flex", gap: 6 }}>
              <button type="button" style={toolBtnStyle}><IconSortAscendingLetters size={13} color="#5A5853" /> Group</button>
              <button type="button" style={toolBtnStyle}><IconAdjustments size={13} color="#5A5853" /> Display</button>
            </div>
          </div>

          {tickets.length === 0 ? (
            <EmptyPane title="No tickets yet" hint="Click '+ Add ticket' or approve a PRD and choose 'Create a ticket'." placeholders={3} />
          ) : (
            <div style={{ display: "flex", gap: 16, overflowX: "auto", paddingBottom: 24 }}>
              {COLUMNS.map((col) => (
                <KanbanColumn key={col} status={col}
                  tickets={tickets.filter((t) => t.status === col)}
                  onDragStart={handleDragStart} onDrop={handleDrop}
                  onCardClick={handleCardClick} selectedId={selected?.id ?? null} />
              ))}
            </div>
          )}
        </div>

        {/* Detail panel */}
        {selected && (
          <TicketDetailPanel ticket={selected} onClose={() => setSelected(null)} onUpdate={handleUpdate} onDelete={handleDelete} />
        )}
      </div>
    </AppLayout>
  )
}
