"use client"

import { useState } from "react"
import { useNavigation } from "../../../context/NavigationContext"
import { OnboardingLayout } from "./OnboardingLayout"

export function Onboarding8() {
  const { goTo } = useNavigation()
  const [invites, setInvites] = useState([
    { email: "", role: "Admin" },
    { email: "", role: "Viewer" },
    { email: "", role: "Viewer" },
  ])

  const addInvite = () => {
    setInvites([...invites, { email: "", role: "Viewer" }])
  }

  const updateInvite = (index: number, field: "email" | "role", value: string) => {
    const newInvites = [...invites]
    newInvites[index][field] = value
    setInvites(newInvites)
  }

  return (
    <OnboardingLayout
      heroTitle={
        <>
          Good decisions <span>travel in teams.</span>
        </>
      }
      heroSub="Bring in your co-founder, your PM lead, or your design partner. They'll see the same brief and can comment, reprioritize, or flag evidence."
      step={8}
      eyebrow="Step 8 of 8 · optional"
      title="Invite your team"
      desc="Add up to 5 teammates. Everyone gets view access; you pick admins."
    >
      {invites.map((invite, i) => (
        <div key={i} className="invite-row">
          <input
            type="email"
            className="input"
            placeholder="teammate@company.com"
            value={invite.email}
            onChange={(e) => updateInvite(i, "email", e.target.value)}
          />
          <select
            className="invite-role"
            value={invite.role}
            onChange={(e) => updateInvite(i, "role", e.target.value)}
          >
            <option>Admin</option>
            <option>Viewer</option>
          </select>
        </div>
      ))}
      <button
        className="btn btn-ghost"
        style={{ fontSize: 12, padding: "6px 10px" }}
        onClick={addInvite}
      >
        + Add another
      </button>

      <div style={{ display: "flex", gap: 8, marginTop: 24 }}>
        <button className="btn" onClick={() => goTo("ob-7")}>
          Back
        </button>
        <button className="btn btn-ghost" onClick={() => goTo("chat")}>
          Skip
        </button>
        <button
          className="btn btn-accent"
          style={{ flex: 1 }}
          onClick={() => goTo("chat")}
        >
          Finish & enter Sprntly
        </button>
      </div>
    </OnboardingLayout>
  )
}
