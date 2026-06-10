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
  /** Render the AI chat panel as an inline column instead of a global fixed overlay. */
  inlineChat?: boolean
}

export function AppLayout({ children, style, mainStyle, mainClassName, inlineChat }: AppLayoutProps) {
  const { sidebarCollapsed, contentPanelTab } = useNavigation()
  const { activeCompany, setActiveCompany } = useCompany()
  const mainCls = ["main", mainClassName].filter(Boolean).join(" ")
  return (
    <div
      className={`app${sidebarCollapsed ? " app--sidebar-collapsed" : ""}${contentPanelTab ? " app--cpanel-open" : ""}`}
      style={style}
    >
      <Sidebar activeCompany={activeCompany} onSwitchCompany={setActiveCompany} />
      <div className="main-column">
        <MainChromeStrip />
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
