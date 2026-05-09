"use client"

import { useState } from "react"
import { useNavigation } from "../../context/NavigationContext"

export function InviteModal() {
  const { activeModal, closeModal, showToast } = useNavigation()
  const [rows, setRows] = useState([
    { email: "", role: "Viewer" },
    { email: "", role: "Viewer" },
  ])

  if (activeModal !== "invite") return null

  const addRow = () => {
    setRows([...rows, { email: "", role: "Viewer" }])
  }

  const removeRow = (index: number) => {
    if (rows.length > 1) {
      setRows(rows.filter((_, i) => i !== index))
    }
  }

  const updateRow = (index: number, field: "email" | "role", value: string) => {
    const newRows = [...rows]
    newRows[index][field] = value
    setRows(newRows)
  }

  const sendInvites = () => {
    const count = rows.filter((r) => r.email.trim()).length || rows.length
    closeModal()
    showToast(
      `${count} invite${count === 1 ? "" : "s"} sent`,
      "They'll get an email with a sign-up link. Expires in 7 days.",
      "View pending →"
    )
  }

  return (
    <div
      className="modal-overlay open"
      onClick={(e) => e.target === e.currentTarget && closeModal()}
    >
      <div className="modal invite-modal">
        <div className="modal-head">
          <div className="modal-badge">Invite</div>
          <h2 className="modal-title">Invite teammates to Sprntly</h2>
          <p className="modal-sub">
            Everyone gets the Monday brief. Admins can approve PRDs and push to
            Claude Code or your tracker.
          </p>
        </div>
        <div style={{ padding: "0 26px 20px" }}>
          <label className="field-label">Email addresses</label>
          <div className="invite-rows">
            {rows.map((row, i) => (
              <div key={i} className="invite-email-row">
                <input
                  type="email"
                  className="input"
                  placeholder="teammate@company.com"
                  value={row.email}
                  onChange={(e) => updateRow(i, "email", e.target.value)}
                />
                <select
                  className="ticket-select"
                  value={row.role}
                  onChange={(e) => updateRow(i, "role", e.target.value)}
                >
                  <option>Admin</option>
                  <option>Viewer</option>
                </select>
                <button
                  className="invite-remove-btn"
                  onClick={() => removeRow(i)}
                >
                  ×
                </button>
              </div>
            ))}
          </div>
          <button className="invite-add-btn" onClick={addRow}>
            + Add another
          </button>

          <div style={{ marginTop: 20 }}>
            <label className="field-label">Personal message (optional)</label>
            <textarea
              className="textarea"
              placeholder="Join us on Sprntly — it's how we decide what to build next."
              style={{ minHeight: 80 }}
            />
          </div>

          <div
            style={{
              padding: "10px 12px",
              background: "var(--info-soft)",
              borderRadius: 8,
              fontSize: 12,
              color: "var(--ink-2)",
              marginTop: 14,
              display: "flex",
              gap: 8,
              alignItems: "flex-start",
            }}
          >
            <span style={{ color: "var(--info)", fontWeight: 600 }}>i</span>
            <span>
              Invites expire in 7 days. You'll see pending invites on the team
              page with the option to resend or revoke.
            </span>
          </div>
        </div>
        <div className="modal-foot">
          <button className="btn btn-ghost" onClick={closeModal}>
            Cancel
          </button>
          <button className="btn btn-accent" onClick={sendInvites}>
            Send invites
          </button>
        </div>
      </div>
    </div>
  )
}
