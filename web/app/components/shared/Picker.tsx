"use client"

import { useNavigation } from "../../context/NavigationContext"
import { ONBOARDING_SCREENS, APP_SCREENS, type ScreenId } from "../../types"

const PICKER_LABELS: Record<ScreenId, string> = {
  "ob-1": "1·Sign up",
  "ob-2": "2·Email",
  "ob-3": "3·Role",
  "ob-4": "4·Product",
  "ob-5": "5·Goals",
  "ob-6": "6·Connectors",
  "ob-7": "7·Slack",
  "ob-8": "8·Team",
  chat: "Home",
  brief: "Brief",
  detail: "Evidence",
  prd: "PRD",
  ondemand: "Ask Sprntly",
  past: "Past",
  shipped: "Shipped",
  settings: "Settings",
  team: "Team",
  connectors: "Connectors",
}

export function Picker() {
  const { currentScreen, goTo } = useNavigation()

  return (
    <div className="picker">
      <span className="picker-label">
        spr<span>ntly</span>
        <sup>v3</sup>
      </span>
      <div className="picker-group">
        {ONBOARDING_SCREENS.map((screen) => (
          <button
            key={screen}
            className={`picker-btn ${currentScreen === screen ? "active" : ""}`}
            onClick={() => goTo(screen)}
          >
            {PICKER_LABELS[screen]}
          </button>
        ))}
      </div>
      <div className="picker-group">
        {APP_SCREENS.map((screen) => (
          <button
            key={screen}
            className={`picker-btn ${currentScreen === screen ? "active" : ""}`}
            onClick={() => goTo(screen)}
          >
            {PICKER_LABELS[screen]}
          </button>
        ))}
      </div>
    </div>
  )
}
