"use client"

import { useNavigation } from "../../../context/NavigationContext"
import { OnboardingLayout } from "./OnboardingLayout"

export function Onboarding4() {
  const { goTo } = useNavigation()

  return (
    <OnboardingLayout
      heroTitle={
        <>
          Tell us <span>what you're building.</span>
        </>
      }
      heroSub="We read your site, skim your docs, and ground every finding in what your product actually does. No generic advice."
      step={4}
      eyebrow="Step 4 of 8"
      title="Your product"
      desc="A one-line site and a few sentences. We'll take it from there."
    >
      <div className="field">
        <label className="field-label">Product website</label>
        <input type="url" className="input" placeholder="https://acme.com" />
      </div>
      <div className="field">
        <label className="field-label">Product description</label>
        <textarea
          className="textarea"
          placeholder="Acme is a payroll tool for small remote teams. We automate compliance across 40+ countries and handle contractor payouts..."
        />
      </div>
      <div style={{ display: "flex", gap: 8 }}>
        <button className="btn" onClick={() => goTo("ob-3")}>
          Back
        </button>
        <button
          className="btn btn-primary"
          style={{ flex: 1 }}
          onClick={() => goTo("ob-5")}
        >
          Continue
        </button>
      </div>
    </OnboardingLayout>
  )
}
