"use client"

import { useNavigation } from "../../context/NavigationContext"
import { getMainChromeTitle } from "../../types"

/** Sticky strip under the app shell — shows the active module name. */
export function MainChromeStrip() {
  const { currentScreen } = useNavigation()
  const title = getMainChromeTitle(currentScreen)
  return (
    <header className="app-main-chrome" role="banner" aria-label={title}>
      <div className="app-main-chrome-inner">
        <p className="app-main-chrome-title">{title}</p>
      </div>
    </header>
  )
}
