"use client"

import { ReactNode } from "react"
import { useNavigation } from "../../../context/NavigationContext"
import { MainChromeStrip } from "../../shared/MainChromeStrip"
import { Sidebar } from "../../shared/Sidebar"

interface AppLayoutProps {
  children: ReactNode
  style?: React.CSSProperties
  mainStyle?: React.CSSProperties
  /** Extra classes on `<main className="main ...">` (e.g. `main--home-chat`). */
  mainClassName?: string
}

export function AppLayout({ children, style, mainStyle, mainClassName }: AppLayoutProps) {
  const { sidebarCollapsed } = useNavigation()
  const mainCls = ["main", mainClassName].filter(Boolean).join(" ")
  return (
    <div
      className={`app${sidebarCollapsed ? " app--sidebar-collapsed" : ""}`}
      style={style}
    >
      <Sidebar />
      <div className="main-column">
        <MainChromeStrip />
        <main className={mainCls} style={mainStyle}>
          {children}
        </main>
      </div>
    </div>
  )
}
