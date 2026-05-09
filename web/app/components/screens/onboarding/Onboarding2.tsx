"use client"

import { useNavigation } from "../../../context/NavigationContext"
import { OnboardingLayout } from "./OnboardingLayout"

export function Onboarding2() {
  const { goTo } = useNavigation()

  return (
    <OnboardingLayout
      heroTitle={
        <>
          Almost there. <span>Secure your account.</span>
        </>
      }
      heroSub="Work email ties your workspace to your company domain. Teammates who join later get auto-matched."
      step={2}
      eyebrow="Step 2 of 8"
      title="Your details"
      desc="We'll use this to personalize your weekly brief."
    >
      <div className="field">
        <label className="field-label">Full name</label>
        <input type="text" className="input" placeholder="Ada Lovelace" />
      </div>
      <div className="field">
        <label className="field-label">Work email</label>
        <input type="email" className="input" placeholder="ada@company.com" />
      </div>
      <div className="field">
        <label className="field-label">Password</label>
        <input
          type="password"
          className="input"
          placeholder="At least 8 characters"
        />
      </div>
      <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
        <button className="btn" onClick={() => goTo("ob-1")}>
          Back
        </button>
        <button
          className="btn btn-primary"
          style={{ flex: 1 }}
          onClick={() => goTo("ob-3")}
        >
          Continue
        </button>
      </div>
    </OnboardingLayout>
  )
}
