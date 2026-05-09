"use client"

import { useState } from "react"
import { useNavigation } from "../../../context/NavigationContext"
import { OnboardingLayout } from "./OnboardingLayout"

const ROLES = [
  { id: "pm", icon: "P", name: "Product Manager", desc: "I prioritize roadmap" },
  { id: "eng", icon: "E", name: "Engineer", desc: "I ship code" },
  { id: "design", icon: "D", name: "Designer", desc: "I shape the UX" },
  { id: "founder", icon: "F", name: "Founder", desc: "I own the product" },
  { id: "data", icon: "A", name: "Data / Analytics", desc: "I run the numbers" },
  { id: "other", icon: "+", name: "Other", desc: "Something else" },
]

export function Onboarding3() {
  const { goTo } = useNavigation()
  const [selectedRole, setSelectedRole] = useState("pm")

  return (
    <OnboardingLayout
      heroTitle={
        <>
          Briefs tuned to <span>how you work.</span>
        </>
      }
      heroSub="A PM cares about activation curves. An engineer wants the root-cause trace. A founder wants the revenue number. We frame findings for your seat."
      step={3}
      eyebrow="Step 3 of 8"
      title="What's your role?"
      desc="Pick the one closest to your day-to-day."
    >
      <div className="role-grid">
        {ROLES.map((role) => (
          <div
            key={role.id}
            className={`role-card ${selectedRole === role.id ? "selected" : ""}`}
            onClick={() => setSelectedRole(role.id)}
          >
            <div className="role-icon">{role.icon}</div>
            <div>
              <div className="role-name">{role.name}</div>
              <div className="role-desc">{role.desc}</div>
            </div>
          </div>
        ))}
      </div>
      <div style={{ display: "flex", gap: 8 }}>
        <button className="btn" onClick={() => goTo("ob-2")}>
          Back
        </button>
        <button
          className="btn btn-primary"
          style={{ flex: 1 }}
          onClick={() => goTo("ob-4")}
        >
          Continue
        </button>
      </div>
    </OnboardingLayout>
  )
}
