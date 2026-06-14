"use client"

import { useNavigation } from "../../context/NavigationContext"
import { getMainChromeTitle } from "../../types"
import { IconChevronLeft } from "./app-icons"

/** Sticky strip under the app shell — shows the active module name. */
export function MainChromeStrip({ onTitleBack }: { onTitleBack?: () => void } = {}) {
  const { currentScreen } = useNavigation()
  const title = getMainChromeTitle(currentScreen)
  return (
    <header className="app-main-chrome" role="banner" aria-label={title}>
      <div className="app-main-chrome-inner">
        {onTitleBack ? (
          <button type="button" className="app-main-chrome-title app-main-chrome-title--back" onClick={onTitleBack}>
            <IconChevronLeft size={16} />
            {title}
          </button>
        ) : (
          <p className="app-main-chrome-title">{title}</p>
        )}
      </div>
    </header>
  )
}
