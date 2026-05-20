"use client"

import { useNavigation } from "../../context/NavigationContext"
import { IconCheck, IconSparkle } from "./app-icons"

export function ApproveModal() {
  const { activeModal, closeModal, openDrawer } = useNavigation()

  if (activeModal !== "approve") return null

  const handleClaudeClick = () => {
    closeModal()
    openDrawer("claude")
  }

  const handleTicketClick = () => {
    closeModal()
    openDrawer("ticket")
  }

  return (
    <div
      className="modal-overlay open"
      onClick={(e) => e.target === e.currentTarget && closeModal()}
    >
      <div className="modal">
        <div className="modal-head">
          <div className="modal-badge">
            <IconCheck size={12} />
            PRD Approved
          </div>
          <h2 className="modal-title">Where should this go next?</h2>
          <p className="modal-sub">
            Pick how you want to move from spec to code. You can change your mind
            later.
          </p>
        </div>
        <div className="modal-options">
          <div className="modal-option" onClick={handleClaudeClick}>
            <div className="modal-option-icon">
              <IconSparkle size={18} />
            </div>
            <div className="modal-option-name">Generate Prototype</div>
            <div className="modal-option-desc">
              Full context package → Claude Code scopes, implements, opens a PR
              against main.
            </div>
          </div>
          <div className="modal-option" onClick={handleTicketClick}>
            <div className="modal-option-icon">J</div>
            <div className="modal-option-name">Create a ticket</div>
            <div className="modal-option-desc">
              Push to Linear, Jira, or Asana with evidence attached. Track it to
              merge.
            </div>
          </div>
        </div>
        <div className="modal-foot">
          <button className="btn btn-ghost" onClick={closeModal}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  )
}
