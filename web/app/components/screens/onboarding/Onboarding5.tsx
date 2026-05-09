"use client"

import { useState } from "react"
import { useNavigation } from "../../../context/NavigationContext"
import { OnboardingLayout } from "./OnboardingLayout"

const METRICS = [
  "Daily active users",
  "Weekly active users",
  "Activation rate",
  "D7 retention",
  "D30 retention",
  "Revenue / MRR",
  "NPS / CSAT",
  "Churn rate",
  "Support ticket volume",
  "Time to first value",
]

export function Onboarding5() {
  const { goTo } = useNavigation()
  const [selected, setSelected] = useState<string[]>([
    "Daily active users",
    "Activation rate",
    "Revenue / MRR",
  ])

  const toggleMetric = (metric: string) => {
    if (selected.includes(metric)) {
      setSelected(selected.filter((m) => m !== metric))
    } else if (selected.length < 3) {
      setSelected([...selected, metric])
    }
  }

  return (
    <OnboardingLayout
      heroTitle={
        <>
          Every finding tied to <span>a number you care about.</span>
        </>
      }
      heroSub="Pick the metrics you're steering toward. Our intelligence engine ranks opportunities by projected impact on exactly these goals."
      step={5}
      eyebrow="Step 5 of 8"
      title="Primary goals"
      desc="Select up to 3. You can change these later."
    >
      <div className="metric-list">
        {METRICS.map((metric) => (
          <div
            key={metric}
            className={`metric-chip ${selected.includes(metric) ? "selected" : ""}`}
            onClick={() => toggleMetric(metric)}
          >
            {metric}
          </div>
        ))}
        <div className="metric-chip" style={{ opacity: 0.6 }}>
          + Edit goals
        </div>
      </div>
      <div style={{ display: "flex", gap: 8 }}>
        <button className="btn" onClick={() => goTo("ob-4")}>
          Back
        </button>
        <button
          className="btn btn-primary"
          style={{ flex: 1 }}
          onClick={() => goTo("ob-6")}
        >
          Continue
        </button>
      </div>
    </OnboardingLayout>
  )
}
