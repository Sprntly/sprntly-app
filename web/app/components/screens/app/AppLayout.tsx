"use client"

import { ReactNode } from "react"
import { useNavigation } from "../../../context/NavigationContext"
import { useCompany } from "../../../context/CompanyContext"
import { MainChromeStrip } from "../../shared/MainChromeStrip"
import { Sidebar } from "../../shared/Sidebar"
import { AIBar } from "../../shared/AIBar"

interface AppLayoutProps {
  children: ReactNode
  style?: React.CSSProperties
  mainStyle?: React.CSSProperties
  /** Extra classes on `<main className="main ...">` (e.g. `main--home-chat`). */
  mainClassName?: string
  /** Extra classes on `<div className="main-column ...">` (e.g. `main-column--flush`). */
  mainColumnClassName?: string
  /** Render the AI chat panel as an inline column instead of a global fixed overlay. */
  inlineChat?: boolean
  /** When set, the chrome-strip title renders as a back affordance (e.g. the /prototype canvas route). */
  onTitleBack?: () => void
  /** When true, suppresses the MainChromeStrip entirely (e.g. the /prototype canvas where the DA control bar owns the top bar). */
  hideChromeStrip?: boolean
}

export function AppLayout({ children, style, mainStyle, mainClassName, mainColumnClassName, inlineChat, onTitleBack, hideChromeStrip }: AppLayoutProps) {
  const { sidebarCollapsed, contentPanelTab } = useNavigation()
  const { activeCompany, setActiveCompany } = useCompany()
  const mainCls = ["main", mainClassName].filter(Boolean).join(" ")
  const mainColumnCls = ["main-column", mainColumnClassName].filter(Boolean).join(" ")
  return (
    <div
      className={`app${sidebarCollapsed ? " app--sidebar-collapsed" : ""}${contentPanelTab ? " app--cpanel-open" : ""}`}
      style={style}
    >
      <Sidebar activeCompany={activeCompany} onSwitchCompany={setActiveCompany} />
      <div className={mainColumnCls}>
        {!hideChromeStrip && <MainChromeStrip onTitleBack={onTitleBack} />}
        {inlineChat ? (
          <div className="main-with-inline-chat">
            <main className={mainCls} style={mainStyle}>
              {children}
            </main>
            <aside className="ai-inline-column">
              <AIBar inline />
            </aside>
          </div>
        ) : (
          <main className={mainCls} style={mainStyle}>
            {children}
          </main>
        )}
      </div>
    </div>
  )
}
