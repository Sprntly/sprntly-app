"use client"

import { ReactNode } from "react"
import { useNavigation } from "../../../context/NavigationContext"
import { Sidebar } from "../../shared/Sidebar"

interface AppLayoutProps {
  children: ReactNode
  style?: React.CSSProperties
  mainStyle?: React.CSSProperties
}

export function AppLayout({ children, style, mainStyle }: AppLayoutProps) {
  const { sidebarCollapsed } = useNavigation()
  return (
    <div
      className={`app${sidebarCollapsed ? " app--sidebar-collapsed" : ""}`}
      style={style}
    >
      <Sidebar />
      <main className="main" style={mainStyle}>
        {children}
      </main>
    </div>
  )
}
