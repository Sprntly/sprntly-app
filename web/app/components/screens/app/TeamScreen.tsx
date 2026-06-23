"use client"

import { useNavigation } from "../../../context/NavigationContext"
import { useContent } from "../../../context/ContentContext"
import { AppLayout } from "./AppLayout"
import { EmptyPane } from "../../shared/EmptyPane"

export function TeamScreen() {
  const { openModal } = useNavigation()
  const { content } = useContent()
  const members = content.teamMembers
  const pending = content.teamPending

  const empty = members.length === 0 && pending.length === 0

  return (
    <AppLayout>
      <div className="main-header">
        <div>
          <h1 className="main-title">Team</h1>
          <p className="main-sub">
            Everyone here gets the Weekly brief and can view evidence. Admins can
            approve PRDs and push to trackers.
          </p>
        </div>
        <button type="button" className="btn btn-primary btn-sm" onClick={() => openModal("invite")}>
          + Invite
        </button>
      </div>

      {empty ? (
        <EmptyPane
          title="No team members loaded"
          hint="Hydrate `content.teamMembers` and `content.teamPending` from your directory API after sign-in."
          placeholders={3}
        />
      ) : (
        <div className="settings-card">
          {members.map((member) => (
            <div key={member.id} className="team-row">
              <div className="team-av" style={member.color ? { background: member.color } : undefined}>
                {member.initials}
              </div>
              <div>
                <div className="team-name">
                  {member.name}
                  {member.isSelf ? (
                    <>
                      <span className="team-role-pill" style={{ marginLeft: 8 }}>
                        You
                      </span>
                    </>
                  ) : null}
                </div>
                <div className="team-email">{member.email}</div>
              </div>
              <select className="ticket-select" style={{ maxWidth: 110 }} defaultValue={member.role}>
                <option>Admin</option>
                <option>Viewer</option>
              </select>
              <span className="team-role-pill" style={{ color: "var(--muted)" }}>
                {member.isSelf ? "Owner" : "Member"}
              </span>
              <div className="team-row-actions">
                {member.isSelf ? (
                  <button type="button" className="team-action-btn" disabled style={{ opacity: 0.4 }}>
                    —
                  </button>
                ) : (
                  <button type="button" className="team-action-btn danger">
                    Remove
                  </button>
                )}
              </div>
            </div>
          ))}
          {pending.map((invite) => (
            <div key={invite.email} className="team-row">
              <div className="team-av" style={{ background: "var(--muted)" }}>
                ?
              </div>
              <div>
                <div className="team-name">{invite.email}</div>
                <div className="team-email">Pending invite</div>
              </div>
              <span className="team-role-pill" style={{ background: "var(--warn-soft)", color: "var(--warn)" }}>
                Pending
              </span>
              <span className="team-role-pill" style={{ color: "var(--muted)" }}>
                Sent {invite.sent}
              </span>
              <div className="team-row-actions">
                <button type="button" className="team-action-btn accent">
                  Resend invite
                </button>
                <button type="button" className="team-action-btn danger">
                  Revoke
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {!empty ? (
        <div
          style={{
            marginTop: 32,
            padding: "14px 18px",
            background: "var(--surface-2)",
            borderRadius: 10,
            fontSize: 13,
            color: "var(--ink-3)",
          }}
        >
          <strong>Team plan:</strong> Seat counts will reflect your billing API when
          connected.
        </div>
      ) : null}
    </AppLayout>
  )
}
